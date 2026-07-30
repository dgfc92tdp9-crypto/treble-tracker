"""Local TAPI — the in-process implementation of the data path (spec §8.3).

Invariant I7: every screen, export and client feature reads through TAPI.
This is the local-only transport (spec §2.4, §23.3); the gRPC/Arrow Flight
server transport arrives at Phase 2 behind the same interface, so nothing
above this layer changes when it does.

Two responsibilities:

- **Resolve a security reference to a subject key.** `IBM US Equity` must
  become the storage key the facts were written under. The mapping comes
  from EDGAR's own company index, so it is source-derived, not a hand-kept
  list that would drift.
- **Read fields point-in-time.** `as_of` is required all the way down (I2);
  values carry their provenance id (I1) and staleness so the presentation
  layer can honour the display conventions (§6.3) without knowing anything
  about storage.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

from treble.analytics._ql import DayCount
from treble.analytics.bonds.spec import FixedBondSpec, Frequency
from treble.core.facts import Fact
from treble.core.identifiers import TUID, SecurityQuery, YellowKey
from treble.core.provenance import ProvenanceId
from treble.store.duck import DuckStore
from treble.tapi.fields import FIELDS, FieldDef, FieldDictionary
from treble.tapi.types import FieldResult

#: A value older than this is displayed as stale (§6.3 mandates that any
#: value known not to be current is visually distinguished). Fundamentals
#: are quarterly, so the window is generous; the ticker plant tightens this
#: dramatically at Phase 2.
DEFAULT_STALE_AFTER = timedelta(days=120)


#: Model ids with a wired data path. Anything in the field dictionary but
#: not here raises rather than returning null, so an unimplemented analytic
#: is distinguishable from an absent input.
_WIRED_MODELS = frozenset(
    {
        "bonds.yield_from_price",
        "bonds.modified_duration",
        "bonds.convexity",
        "bonds.dv01",
        "bonds.yield_to_worst",
    }
)


def _looks_like_cusip(security: SecurityQuery) -> bool:
    """Whether this bond reference is a CUSIP rather than a description.

    `IBM 4.15 05/15/39 Corp` is a perfectly valid bond reference whose
    ticker is "IBM" — treating every Govt/Corp ticker as a CUSIP resolved
    that to `cusip:IBM` and reported a missing instrument instead of an
    unbuilt lookup. Descriptor-based resolution needs a security-master
    search over coupon and maturity, which does not exist yet, so those
    fall through to the honest "no resolution for this namespace" error.
    """
    ticker = security.ticker
    return security.descriptor is None and len(ticker) == 9 and ticker.isalnum()


def _wire_value(value: object) -> str | float | int | bool | None:
    """Narrow a stored value to what crosses the TAPI boundary.

    Dates become ISO strings here rather than at the renderer. Field values
    travel over HTTP as JSON to the desktop client and are frozen into
    conformance fixtures, so a type that survives in-process but not through
    `json.dumps` would make the live path and the tested path differ — the
    desktop would render something no golden had ever checked.
    """
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str | float | int | bool) or value is None:
        return value
    return str(value)


class _UnpriceableBondError(Exception):
    """Reference data insufficient (or unsuitable) to price this bond."""


class SecurityNotFoundError(KeyError):
    """No subject key could be resolved for the reference."""


class TickerIndex:
    """ticker -> CIK, built from EDGAR's published company index.

    Source-derived rather than hand-maintained: the same payload the
    population runner uses for discovery also supplies this mapping, so the
    two cannot disagree.
    """

    def __init__(self, mapping: dict[str, int]) -> None:
        self._by_ticker = {k.upper(): v for k, v in mapping.items()}

    @classmethod
    def from_company_index(cls, payload: bytes) -> TickerIndex:
        doc = json.loads(payload)
        rows = doc.values() if isinstance(doc, dict) else doc
        mapping: dict[str, int] = {}
        for row in rows:
            if isinstance(row, dict) and row.get("ticker") and row.get("cik_str"):
                mapping[str(row["ticker"])] = int(row["cik_str"])
        return cls(mapping)

    def cik(self, ticker: str) -> int | None:
        return self._by_ticker.get(ticker.upper())

    def __len__(self) -> int:
        return len(self._by_ticker)


class LocalTapi:
    """In-process TAPI over the local store."""

    def __init__(
        self,
        store: DuckStore,
        *,
        tickers: TickerIndex | None = None,
        fields: FieldDictionary | None = None,
        stale_after: timedelta = DEFAULT_STALE_AFTER,
    ) -> None:
        self._store = store
        self._tickers = tickers
        self._fields = fields or FIELDS
        self._stale_after = stale_after

    # -- resolution ----------------------------------------------------

    def resolve(self, security: SecurityQuery) -> TUID:
        """Security reference -> storage subject key.

        Equities resolve through the EDGAR company index to a CIK, which is
        the key the fundamentals were written under. Other namespaces get
        their own resolution as their data lands (bonds by ISIN/CUSIP from
        N-PORT, macro series by FRED id).
        """
        if security.key in (YellowKey.EQUITY, YellowKey.PFD):
            if self._tickers is None:
                raise SecurityNotFoundError(
                    "no ticker index loaded; equity resolution needs EDGAR's "
                    "company index (see TickerIndex.from_company_index)"
                )
            cik = self._tickers.cik(security.ticker)
            if cik is None:
                raise SecurityNotFoundError(
                    f"{security.display()!r}: ticker not in EDGAR's company index"
                )
            return TUID(f"cik:{cik:010d}")
        if security.key in (YellowKey.GOVT, YellowKey.CORP) and _looks_like_cusip(security):
            # Bonds are addressed by CUSIP, the subject the Treasury and
            # N-PORT adapters write under. Existence is checked rather than
            # assumed: a mistyped CUSIP would otherwise resolve to a subject
            # with no facts and render a screen of dashes indistinguishable
            # from a real bond nobody has data for.
            subject = TUID(f"cusip:{security.ticker.upper()}")
            if not self._store.has_subject(subject):
                raise SecurityNotFoundError(
                    f"{security.display()!r}: that CUSIP has not been ingested"
                )
            return subject
        if security.key == YellowKey.INDEX and security.venue is None:
            # Macro series are addressable as tickers (spec §7.4).
            return TUID(f"fred:{security.ticker.upper()}")
        raise SecurityNotFoundError(
            f"{security.display()!r}: no resolution for namespace "
            f"{security.key.value!r} yet — data for it has not been ingested"
        )

    # -- reads (I2: as_of required) ------------------------------------

    def field(
        self,
        security: SecurityQuery | None,
        mnemonic: str,
        overrides: dict[str, str],
        *,
        as_of: datetime,
    ) -> FieldResult:
        """One field value, point-in-time, with provenance and staleness."""
        definition = self._fields.get(mnemonic)  # raises on unknown mnemonic

        if definition.model_derived:
            return self._model_field(security, definition, as_of=as_of)
        if definition.stored_field is None or security is None:
            return FieldResult(value=None)

        facts = self._store.read(self.resolve(security), definition.stored_field, as_of=as_of)
        if not facts:
            # Genuinely absent, not an error: null with no provenance, which
            # the renderer shows as an em dash rather than a zero.
            return FieldResult(value=None)

        latest = max(facts, key=lambda f: f.effective_from)
        age = as_of.date() - (latest.effective_to or latest.effective_from)
        return FieldResult(
            value=_wire_value(latest.value),
            provenance_id=latest.provenance_id,
            stale=age > self._stale_after,
            model_derived=False,
        )

    #: Bindings that introspect the system rather than a security. Prefixed
    #: so they cannot be mistaken for field mnemonics: CLAUDE.md forbids
    #: coining mnemonics, and these are not fields — no security has a
    #: "sys:models". They back SPTR, MDL and FLDS, the three screens whose
    #: subject is the workstation itself.
    SYSTEM_BINDINGS = ("sys:provenance", "sys:models", "sys:fields", "sys:treasury_curve")

    #: The constant-maturity Treasury tenors, in curve order with their year
    #: fractions. Ordered here rather than sorted by name because "DGS1MO"
    #: sorts after "DGS10" alphabetically, and a yield curve drawn in
    #: alphabetical order is not a yield curve.
    CMT_TENORS: tuple[tuple[str, str, float], ...] = (
        ("DGS1MO", "1M", 1 / 12),
        ("DGS3MO", "3M", 0.25),
        ("DGS6MO", "6M", 0.5),
        ("DGS1", "1Y", 1.0),
        ("DGS2", "2Y", 2.0),
        ("DGS3", "3Y", 3.0),
        ("DGS5", "5Y", 5.0),
        ("DGS7", "7Y", 7.0),
        ("DGS10", "10Y", 10.0),
        ("DGS20", "20Y", 20.0),
        ("DGS30", "30Y", 30.0),
    )

    # -- model-derived fields (YAS) -------------------------------------

    def _model_field(
        self, security: SecurityQuery | None, definition: FieldDef, *, as_of: datetime
    ) -> FieldResult:
        """Compute an analytic from stored reference data.

        Returns a null result whenever the inputs are not all present rather
        than substituting a default. A duration computed from a guessed
        coupon is worse than a dash: the dash is visibly missing, and the
        number is invisibly wrong.
        """
        # A model with no wiring is a gap in this system, not missing data,
        # and the two must not look the same on screen. OAS needs a
        # bootstrapped curve and a vol assumption; until that exists, saying
        # so is better than a dash the user would read as "not reported".
        if definition.model_id not in _WIRED_MODELS:
            raise NotImplementedError(
                f"{definition.mnemonic!r} is model-derived ({definition.model_id}); "
                "that model is not wired to a data path yet"
            )
        if security is None:
            return FieldResult(value=None)
        try:
            spec, price, priced_on, provenance_id = self._bond_inputs(security, as_of=as_of)
        except (SecurityNotFoundError, _UnpriceableBondError):
            # Inputs genuinely absent or unsuitable: null, which renders as
            # an em dash. A number computed from a guessed coupon would be
            # invisibly wrong where the dash is visibly missing.
            return FieldResult(value=None)

        from treble.analytics.bonds import pricing

        # The risk measures take a *yield*, not a price. Passing the price
        # does not fail — QuantLib reads 98.88 as a 9888% yield and returns
        # a modified duration of 0.006 for a twenty-year bond, which is
        # small, plausible-looking and completely wrong.
        quoted_yield = pricing.yield_from_price(spec, price, as_of=priced_on).value

        computations: dict[str, object] = {
            "bonds.yield_from_price": lambda: quoted_yield * 100.0,
            "bonds.modified_duration": lambda: (
                pricing.modified_duration(spec, quoted_yield, as_of=priced_on).value
            ),
            "bonds.convexity": lambda: pricing.convexity(spec, quoted_yield, as_of=priced_on).value,
            "bonds.dv01": lambda: pricing.dv01(spec, quoted_yield, as_of=priced_on).value,
            # A bullet bond works out at maturity; the worst-call case needs
            # a call schedule, which Treasury auction data does not carry.
            "bonds.yield_to_worst": lambda: spec.maturity.isoformat(),
        }
        compute = computations[definition.model_id or ""]

        return FieldResult(
            value=compute(),  # type: ignore[operator]
            provenance_id=provenance_id,
            # The inputs are an auction print, so the analytic is exactly as
            # current as that auction — staleness must follow the input, not
            # the moment of computation.
            stale=(as_of.date() - priced_on) > self._stale_after,
            model_derived=True,
        )

    def _bond_inputs(
        self, security: SecurityQuery, *, as_of: datetime
    ) -> tuple[FixedBondSpec, float, date, ProvenanceId | None]:
        """Assemble a priceable bond from stored auction facts."""
        subject = self.resolve(security)

        def latest(field: str) -> Fact | None:
            facts = self._store.read(subject, field, as_of=as_of)
            return max(facts, key=lambda f: f.effective_from) if facts else None

        indexed = latest("inflation_index_security")
        if indexed is not None and str(indexed.value) == "Yes":
            # Pricing a TIPS with nominal maths yields a real rate that reads
            # as a nominal one — plausible, and wrong by the inflation
            # accrual. Refused until index-ratio handling exists (§10.3).
            raise _UnpriceableBondError("inflation-indexed security")

        def required(field: str) -> Fact:
            fact = latest(field)
            if fact is None:
                raise _UnpriceableBondError(f"no {field}")
            return fact

        coupon = required("int_rate")
        dated = required("dated_date")
        maturity = required("maturity_date")
        price = required("high_price")
        issued = required("issue_date")

        # Narrowed by explicit checks rather than `assert`: assertions are
        # stripped under `python -O`, which would turn a data problem into
        # an unhandled TypeError inside QuantLib in exactly the deployment
        # that runs optimised.
        if not isinstance(coupon.value, int | float) or not isinstance(price.value, int | float):
            raise _UnpriceableBondError("non-numeric coupon or price")
        if not isinstance(dated.value, date) or not isinstance(maturity.value, date):
            raise _UnpriceableBondError("non-date schedule")
        if not isinstance(issued.value, date):
            raise _UnpriceableBondError("non-date issue")

        spec = FixedBondSpec(
            coupon=float(coupon.value) / 100.0,
            # `dated_date` starts the accrual; on a reopening it precedes the
            # issue date, and using the issue date shifts the coupon schedule.
            issue_date=dated.value,
            maturity=maturity.value,
            frequency=Frequency.SEMIANNUAL,
            day_count=DayCount.ACT_ACT_ICMA,  # Treasury notes and bonds
            settlement_days=0,  # the auction price settles on the issue date
        )
        return spec, float(price.value), issued.value, price.provenance_id

    def series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """Tabular data for a pane: a time series, or a system table."""
        if binding in self.SYSTEM_BINDINGS:
            return self._system_series(security, binding, as_of=as_of)
        if security is None:
            return ()
        definition = self._fields.get(binding)
        if definition.stored_field is None:
            return ()
        facts = self._store.history(self.resolve(security), definition.stored_field, as_of=as_of)
        return tuple(
            (f.effective_from.isoformat(), f.value)  # type: ignore[misc]
            for f in sorted(facts, key=lambda f: f.effective_from)
        )

    # -- dictionary ----------------------------------------------------

    def search_fields(self, query: str, *, limit: int = 50):  # type: ignore[no-untyped-def]
        """Backs `FLDS`."""
        return self._fields.search(query, limit=limit)

    # -- system introspection (SPTR, MDL, FLDS) -------------------------

    def _system_series(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The workstation describing itself.

        These read registries and dictionaries rather than the fact store,
        but they still come through TAPI: a screen reaching into
        `treble.analytics` directly would break I7, and the import contract
        would reject it.
        """
        if binding == "sys:fields":
            return tuple(
                (f.mnemonic, f.description, f.field_type.value, ", ".join(f.sources) or "—")
                for f in self._fields.documented()
            )
        if binding == "sys:models":
            # Importing the package is what populates the registry: models
            # register at import time, so a registry read that skipped this
            # would report an empty MDL screen and look like "no models".
            from treble.analytics.registry import load_all_models

            return tuple(
                (
                    registered.meta.model_id,
                    registered.meta.version,
                    registered.meta.spec_section,
                    registered.meta.summary or registered.qualname,
                )
                for registered in sorted(load_all_models().values(), key=lambda r: r.meta.model_id)
            )
        if binding == "sys:treasury_curve":
            return self._treasury_curve(as_of=as_of)
        # sys:provenance — the I1 DAG behind this security's current values.
        if security is None:
            return ()
        return self._provenance_rows(security, as_of=as_of)

    def _treasury_curve(
        self, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The CMT par curve: (tenor, years, rate, observation date).

        Each tenor is a separate FRED series, so a tenor that has not been
        ingested is simply absent — the curve is short, not wrong. The
        observation date travels with each point because tenors publish on
        slightly different schedules, and a curve silently mixing yesterday's
        long end with today's short end would be a plausible-looking lie.
        """
        rows: list[tuple[str | float | int | None, ...]] = []
        for series, label, years in self.CMT_TENORS:
            facts = self._store.read(TUID(f"fred:{series}"), "PX_LAST", as_of=as_of)
            if not facts:
                continue
            latest = max(facts, key=lambda f: f.effective_from)
            # A par yield that is not a number is bad data, not a curve
            # point. Skipping keeps the curve short and honest; coercing
            # would put something on the chart that was never published.
            if not isinstance(latest.value, int | float) or isinstance(latest.value, bool):
                continue
            # Rounded here rather than in a renderer: 1/12 rendered as
            # 0.08333333333333333 is noise in every surface that shows it.
            rows.append((label, round(years, 2), latest.value, latest.effective_from.isoformat()))
        return tuple(rows)

    def _provenance_rows(
        self, security: SecurityQuery, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """One row per distinct source document behind the security.

        SPTR's promise (spec §5.4, I1) is that any figure on screen can be
        followed back to the document it came from. The store is asked what
        provenance it actually holds for the subject; the first cut of this
        walked the field dictionary instead and returned nothing at all,
        because real values live under as-reported XBRL tags that resolve
        dynamically and cannot be enumerated.
        """
        subject = self.resolve(security)
        rows: list[tuple[str | float | int | None, ...]] = []
        for provenance_id in self._store.subject_provenance(subject, as_of=as_of):
            record = self._store.provenance(provenance_id)
            rows.append(
                (
                    record.source_system,
                    record.method.value,
                    record.retrieved_at.date().isoformat(),
                    record.source_uri,
                )
            )
        return tuple(sorted(rows))


def utc_now() -> datetime:
    """Default `as_of` at the TAPI boundary (I2: never inside the store)."""
    return datetime.now(UTC)


class TapiModelSource:
    """Supplies TQL's model-derived fields from TAPI (spec §4.2).

    TQL cannot import the field dictionary — `tapi` sits above `tql` — so
    the dictionary-aware half lives here and is injected downward. This is
    what makes `oas_spread_mid(vol_override=0.20)` a real request rather
    than a parsed string: the override is the assumption the model runs
    under, and §4.2 calls that "the mechanism by which the entire analytics
    library is exposed as data".
    """

    def __init__(self, tapi: LocalTapi) -> None:
        self._tapi = tapi

    def compute(
        self,
        subject: TUID,
        mnemonic: str,
        overrides: tuple[tuple[str, object], ...],
        *,
        as_of: datetime,
    ) -> tuple[object, str | None] | None:
        """The field's value, or None if it is not model-derived.

        Returning None rather than raising lets a non-model field fall
        through to the store, so TQL does not need to know which is which.
        """
        if mnemonic not in FIELDS:
            return None
        definition = FIELDS.get(mnemonic)
        if not definition.model_derived:
            return None
        unknown = [name for name, _ in overrides if name not in definition.overrides]
        if unknown:
            # An override the model does not accept would otherwise be
            # dropped, and the result returned as though the assumption had
            # been applied — a number computed under conditions nobody asked
            # for, indistinguishable from one that was.
            raise UnknownOverrideError(
                f"{mnemonic} does not accept {', '.join(unknown)}; "
                f"it accepts {', '.join(definition.overrides) or 'no overrides'}"
            )
        result = self._tapi.field(
            _subject_query(subject), mnemonic, {k: str(v) for k, v in overrides}, as_of=as_of
        )
        return result.value, result.provenance_id


class UnknownOverrideError(ValueError):
    """An override the field's model does not accept."""


def _subject_query(subject: TUID) -> SecurityQuery | None:
    """A store subject back into a security reference.

    TQL selects subjects; TAPI's field path takes securities. Only the
    namespaces TQL can select are mapped, and anything else returns None so
    the field resolves to null rather than to the wrong instrument.
    """
    text = str(subject)
    if text.startswith("cusip:"):
        return SecurityQuery(ticker=text.removeprefix("cusip:"), key=YellowKey.GOVT)
    if text.startswith("fred:"):
        return SecurityQuery(ticker=text.removeprefix("fred:"), key=YellowKey.INDEX)
    return None
