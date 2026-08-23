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
from treble.analytics.derivatives.swap import SwapSpec
from treble.core.facts import Fact
from treble.core.identifiers import (
    TUID,
    SecurityQuery,
    YellowKey,
    cusip_from_isin,
    isin_from_cusip,
    looks_like_isin,
)
from treble.core.provenance import ProvenanceId
from treble.store.duck import DuckStore
from treble.tapi.contribution import ContributionService
from treble.tapi.factor_model import FACTORS
from treble.tapi.fields import FIELDS, FieldDef, FieldDictionary
from treble.tapi.security_master import SecurityMaster, build_security_master
from treble.tapi.swap_market import SwapMarket
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
    unbuilt lookup.

    The security master itself now exists and is wired in — a CUSIP with no
    stored subject resolves through it to the FIGI that has facts. What is
    still absent is *descriptor* search: matching "IBM 4.15 05/15/39" by
    issuer, coupon and maturity is a different lookup from resolving an
    identifier, and those references still fall through to the honest "no
    resolution for this namespace" error.
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


#: A pane binding carrying this prefix is a TQL query rather than a field
#: mnemonic. Screens reach TQL only through here, which is what keeps I7
#: intact while `tql` sits *below* `tapi` in the layering.
TQL_BINDING = "tql:"


def _cell(value: object) -> str | float | int | None:
    """A query value narrowed to what a pane can carry.

    Dates become ISO strings for the same reason field values do: a pane's
    rows cross the HTTP boundary to the desktop client and are frozen into
    conformance fixtures, so a type that survives in-process but not through
    `json.dumps` would make the live path differ from the tested one.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        # Query panes are read by people; full float expansion is noise.
        return round(value, 4)
    if isinstance(value, str | int) or value is None:
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
        contributions: ContributionService | None = None,
    ) -> None:
        self._store = store
        self._tickers = tickers
        self._fields = fields or FIELDS
        self._stale_after = stale_after
        # An empty contribution service by default, which is the honest
        # state: nobody is contributing to this install. `ALLQ` then renders
        # its correct-when-empty case, which is the Phase 2 criterion rather
        # than a placeholder for it.
        self._contributions = contributions or ContributionService()
        # Built on first use rather than here: most resolutions never need
        # it, and building it walks every mapping subject in the store.
        self._master: SecurityMaster | None = None

    # -- resolution ----------------------------------------------------

    def _security_master(self) -> SecurityMaster:
        """The master, built once and reused.

        Built lazily because most resolutions never need it — a ticker or
        an already-ingested CUSIP answers from the store directly — and
        building it walks every mapping subject. Cached because
        `links_from_facts` is a pure function of the store: rebuilding per
        lookup would repeat that walk for an identical answer.
        """
        if self._master is None:
            self._master = build_security_master(self._store, as_of=datetime.now(UTC))
        return self._master

    @property
    def store(self) -> DuckStore:
        """The underlying store, read-only.

        For same-layer TAPI services that need the store directly rather
        than through a binding — the gRPC `docs` service lists documents
        from provenance, which is not a screen shape. Exposed as a property
        rather than reached at as `_store` from a sibling module, so the
        one legitimate use is visible instead of looking like a private
        access somebody got away with. It does not widen I7: `grpc_server`
        is inside `tapi`, and nothing above `tapi` can see this.
        """
        return self._store

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
        if security.key in (YellowKey.GOVT, YellowKey.CORP) and looks_like_isin(security.ticker):
            # ISIN first, because that is what the filings carry. N-PORT
            # publishes ISINs and this store holds 1,861 of them against
            # 147 CUSIPs, so before this the great majority of the bond
            # universe was addressable only by an identifier no source in
            # the system actually writes.
            ticker = security.ticker.upper()
            subject = TUID(f"isin:{ticker}")
            if self._store.has_subject(subject):
                return subject
            # A US or Canadian ISIN carries its CUSIP in characters 3-11,
            # so the same instrument may be stored under either. Checked
            # rather than assumed: elsewhere those nine characters are a
            # national number that is not a CUSIP at all.
            embedded = cusip_from_isin(ticker)
            if embedded is not None:
                by_cusip = TUID(f"cusip:{embedded}")
                if self._store.has_subject(by_cusip):
                    return by_cusip
            resolved = self._security_master().resolve(subject)
            if resolved is not None and self._store.has_subject(resolved):
                return resolved
            raise SecurityNotFoundError(f"{security.display()!r}: that ISIN has not been ingested")
        if security.key in (YellowKey.GOVT, YellowKey.CORP) and _looks_like_cusip(security):
            # Bonds are addressed by CUSIP, the subject the Treasury and
            # N-PORT adapters write under. Existence is checked rather than
            # assumed: a mistyped CUSIP would otherwise resolve to a subject
            # with no facts and render a screen of dashes indistinguishable
            # from a real bond nobody has data for.
            subject = TUID(f"cusip:{security.ticker.upper()}")
            if self._store.has_subject(subject):
                return subject
            # Nothing wrote under this CUSIP, but the security master may
            # still know the instrument under another identifier: OpenFIGI
            # maps CUSIP to FIGI, and the FIGI subject is where the facts
            # live. Before this, WP7's master was built, tested and
            # consulted by nothing, so a CUSIP the system could resolve
            # reported as "not ingested".
            resolved = self._security_master().resolve(subject)
            if resolved is not None and self._store.has_subject(resolved):
                return resolved
            # The reverse bridge. A trader types a CUSIP; the filings wrote
            # an ISIN. Both country prefixes are tried because a CUSIP alone
            # does not say which — Canadian issues carry CUSIPs too, and
            # assuming US would silently miss them.
            for country in ("US", "CA"):
                candidate = TUID(f"isin:{isin_from_cusip(security.ticker, country=country)}")
                if self._store.has_subject(candidate):
                    return candidate
            raise SecurityNotFoundError(f"{security.display()!r}: that CUSIP has not been ingested")
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
            effective_from=latest.effective_from,
            effective_to=latest.effective_to or latest.effective_from,
        )

    #: Bindings that introspect the system rather than a security. Prefixed
    #: so they cannot be mistaken for field mnemonics: CLAUDE.md forbids
    #: coining mnemonics, and these are not fields — no security has a
    #: "sys:models". They back SPTR, MDL and FLDS, the three screens whose
    #: subject is the workstation itself.
    SYSTEM_BINDINGS = (
        "sys:provenance",
        "sys:models",
        "sys:fields",
        "sys:treasury_curve",
        "sys:swap_curves",
        "sys:swpm_valuation",
        "sys:swpm_cashflows",
        "sys:swpm_ois",
        "sys:swpm_basis",
        "sys:allq",
        "sys:allq_composites",
        "sys:port_summary",
        "sys:port_factors",
        "sys:port_exposures",
        "sys:tval_curves",
        "sys:tval_values",
        "sys:tval_method",
        "sys:tval_snapshots",
        "sys:tval_peers",
        "sys:rels_securities",
        "sys:rels_method",
        "sys:oas1_grid",
        "sys:oas1_method",
        "sys:sprd_spreads",
        "sys:sprd_method",
        "sys:ddis_ladder",
        "sys:ddis_method",
        "sys:eco_dashboard",
        "sys:eco_method",
        "sys:vcub_grid",
        "sys:vcub_method",
        "sys:entity_owners",
        "sys:entity_children",
        "sys:fa_ratios",
        "sys:swpm_products",
        "sys:sptr_documents",
        "sys:allq_evaluated",
        "sys:tval_residual",
    )

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
        if binding.startswith(TQL_BINDING):
            return self._tql_series(binding.removeprefix(TQL_BINDING), as_of=as_of)
        if binding in self.SYSTEM_BINDINGS:
            return self._system_series(security, binding, as_of=as_of)
        if security is None:
            # A reason, not a blank pane — the same correction the `sys:`
            # panels carry, applied once here so it covers every
            # field-bound pane rather than each screen separately. GP and
            # HP were the two that had it: both bind `PX_LAST` directly,
            # so neither went through a `sys:` handler where the rows
            # would have been added.
            return ((f"no security selected: this panel plots {binding}", None),)
        definition = self._fields.get(binding)
        if definition.stored_field is None:
            # The field exists in the dictionary but nothing stores it, so
            # the panel would be permanently blank and look like a load
            # failure. Naming the field is what lets a reader tell "not
            # collected" from "not reported".
            return ((f"{binding} is defined but no source populates it", None),)
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

    def _tql_series(
        self, query_text: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """A pane backed by a TQL query (spec §7.7, §14.3).

        This is the only path from a screen to TQL, and it goes through
        TAPI — which is what keeps I7 intact while `tql` itself sits below
        `tapi` in the layering.

        A query that cannot run surfaces its reason as a row rather than an
        empty table: `SRCH` returning nothing must be distinguishable from
        `SRCH` failing, or a screen reports "no matches" for a broken query.
        """
        from treble.tql.execute import TqlExecutionError, execute, plan
        from treble.tql.grammar import TqlSyntaxError, parse_tql

        try:
            compiled = plan(parse_tql(query_text), as_of=as_of)
            result = execute(compiled, self._store, TapiModelSource(self))
        except (TqlSyntaxError, TqlExecutionError, UnknownOverrideError) as error:
            return ((f"query failed: {error}",),)

        return tuple(
            (row.subject.split(":", 1)[-1], *(_cell(value) for _, value in row.values))
            for row in result.rows
        )

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
        if binding in (
            "sys:swap_curves",
            "sys:swpm_valuation",
            "sys:swpm_cashflows",
            "sys:swpm_ois",
            "sys:swpm_basis",
        ):
            return self._swpm(binding, as_of=as_of)
        if binding in ("sys:allq", "sys:allq_composites"):
            return self._allq(security, binding, as_of=as_of)
        if binding in ("sys:port_summary", "sys:port_factors", "sys:port_exposures"):
            return self._port(binding, as_of=as_of)
        if binding in ("sys:rels_securities", "sys:rels_method"):
            return self._rels(security, binding, as_of=as_of)
        if binding in ("sys:oas1_grid", "sys:oas1_method"):
            return self._oas1(security, binding, as_of=as_of)
        if binding in ("sys:sprd_spreads", "sys:sprd_method"):
            return self._sprd(security, binding, as_of=as_of)
        if binding in ("sys:ddis_ladder", "sys:ddis_method"):
            return self._ddis(security, binding, as_of=as_of)
        if binding in ("sys:eco_dashboard", "sys:eco_method"):
            return self._eco(binding, as_of=as_of)
        if binding == "sys:tval_peers":
            return self._tval_peers(as_of=as_of)
        if binding == "sys:tval_snapshots":
            return self._tval_snapshots(security, as_of=as_of)
        if binding in ("sys:tval_curves", "sys:tval_values", "sys:tval_method"):
            return self._tval(binding, as_of=as_of)
        if binding in ("sys:vcub_grid", "sys:vcub_method"):
            return self._vcub(binding, as_of=as_of)
        if binding in ("sys:entity_owners", "sys:entity_children"):
            return self._entity(security, binding, as_of=as_of)
        if binding == "sys:fa_ratios":
            return self._fa_ratios(security, as_of=as_of)
        if binding == "sys:swpm_products":
            return self._swpm_products(as_of=as_of)
        if binding == "sys:sptr_documents":
            return self._sptr_documents(security, as_of=as_of)
        if binding == "sys:allq_evaluated":
            return self._allq_evaluated(security, as_of=as_of)
        if binding == "sys:tval_residual":
            return self._tval_residual(as_of=as_of)
        # sys:provenance — the I1 DAG behind this security's current values.
        if security is None:
            # A reason, not a blank pane. SPTR unbound is what a reader
            # sees before typing a ticker, and an empty provenance panel
            # says "nothing sourced this" rather than "nothing asked".
            # Same correction as ALLQ above; they were the last two
            # `sys:` panels returning zero rows for an unbound screen.
            return (("no security selected: SPTR traces one security's values", None, None),)
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

    # -- ALLQ (spec §2.2, §23.3) ----------------------------------------

    @property
    def contributions(self) -> ContributionService:
        """The contribution API surface — the only write path in TAPI."""
        return self._contributions

    def _allq(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """Every contributor's quote on one instrument, and the composites.

        Returns rows for a book that may well be empty, because on this
        install it always is — nobody contributes to a network of one. An
        empty book is the answer, not a missing answer, and the screen
        renders it as such (the Phase 2 criterion is `ALLQ`
        *correct-when-empty*).
        """
        if security is None:
            # A reason, not zero rows — the same correction the empty-book
            # branch below already carries, which was applied there and
            # missed here. A blank pane is indistinguishable from one that
            # failed to load, and ALLQ unbound is the state the screen is
            # in every time it is first opened, so this was the *most*
            # frequently seen version of the defect the branch below fixes.
            columns = 7 if binding == "sys:allq" else 3
            blanks = [None] * (columns - 1)
            return (("no security selected: ALLQ quotes one instrument", *blanks),)
        book = self._contributions.book(str(self.resolve(security)), as_of=as_of)

        if binding == "sys:allq":
            if not book.quotes:
                # A reason, not zero rows. The composites pane below has
                # always said "Contributors 0 / Last live never"; this one
                # returned an empty tuple, which renders as a blank pane
                # and is indistinguishable from a pane that failed to load.
                # The criterion is ALLQ *correct-when-empty*, and half the
                # screen was getting that right.
                since = (
                    f"; last live {book.last_live.isoformat(timespec='seconds')}"
                    if book.last_live
                    else "; never quoted"
                )
                return (
                    (
                        f"no contributor is quoting this instrument{since}",
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                    ),
                )
            return tuple(
                (
                    quote.contributor,
                    quote.firmness.value,
                    quote.bid,
                    quote.bid_size,
                    quote.ask,
                    quote.ask_size,
                    quote.quoted_at.isoformat(timespec="seconds"),
                )
                for quote in book.quotes
            )

        tcmp_bid, tcmp_ask = book.tcmp
        tgn_bid, tgn_ask = book.tgn
        spread = book.spread
        rows: list[tuple[str | float | int | None, ...]] = [
            ("TCMP (executable)", tcmp_bid, tcmp_ask),
            ("TGN (indicative)", tgn_bid, tgn_ask),
            # Rounded here rather than in a renderer: a spread of
            # 0.19999999999998863 is float noise in every surface that
            # shows it, and rounding once keeps the two renderers agreeing.
            ("Spread", round(spread, 6) if spread is not None else None, None),
            ("Contributors", float(len(book.quotes)), None),
        ]
        if book.is_empty:
            # An empty screen that cannot say how long it has been empty is
            # indistinguishable from one that failed to load.
            rows.append(
                (
                    "Last live",
                    book.last_live.isoformat(timespec="seconds") if book.last_live else "never",
                    None,
                )
            )
        return tuple(rows)

    # -- SWPM (spec §12.1) ----------------------------------------------

    def _swpm(
        self, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The three `SWPM` panes, off one built curve environment.

        A failure to build returns the reason as a row rather than an empty
        table. `SWPM` with no curve and `SWPM` with a broken curve must not
        look the same, and neither may look like a swap worth nothing.
        """
        from treble.analytics.derivatives.csa import CsaTerms
        from treble.analytics.derivatives.swap import price_swap, swap_dv01, swap_par_rate
        from treble.tapi.swap_market import (
            DISCOUNT_CURVE,
            FORECAST_CURVE,
            SwapMarketUnavailableError,
            build_swap_market,
        )

        try:
            market = build_swap_market(self._store, as_of=as_of)
        except SwapMarketUnavailableError as error:
            return ((f"no curve environment: {error}",),)

        if binding == "sys:swap_curves":
            basis = market.basis_bp
            return tuple(
                (
                    tenor,
                    round(market.discount_rates[tenor] * 100, 4),
                    round(market.forecast_rates[tenor] * 100, 4),
                    round(basis[tenor], 1),
                )
                for tenor in market.tenors
            )

        if binding == "sys:swpm_ois":
            return self._swpm_ois(market)

        if binding == "sys:swpm_basis":
            # Its own market, built with the short curve required. The
            # shared one picks the newest day the discount/forecast pair
            # builds on, which on a thin day is not a day the 3M curve
            # builds on at all — and a basis tab that goes blank on the
            # newest day, while two days earlier it had nine nodes, reads
            # as a broken screen rather than a quiet Friday.
            try:
                basis_market = build_swap_market(self._store, as_of=as_of, require_short=True)
            except SwapMarketUnavailableError as error:
                return ((f"no tenor basis available: {error}",),)
            rows = self._swpm_basis(basis_market)
            if basis_market.report_date != market.report_date:
                # Said, not silently substituted. A basis quoted from an
                # older day than the rest of the screen is legitimate and
                # must not be read as today's.
                return (
                    *rows,
                    (
                        f"basis from {basis_market.report_date}, not "
                        f"{market.report_date}: the newer day had too few 3M nodes",
                        None,
                        None,
                        None,
                    ),
                )
            return rows

        spec = self._swpm_trade(market)
        csa = CsaTerms(collateral_currency="EUR", discount_curve=DISCOUNT_CURVE)
        priced = price_swap.__wrapped__(spec, market.curves, csa)  # type: ignore[attr-defined]

        if binding == "sys:swpm_cashflows":
            return tuple(
                (
                    flow.leg,
                    flow.accrual_end.isoformat(),
                    round(flow.notional, 2),
                    round(flow.rate * 100, 4),
                    round(flow.amount, 2),
                    round(flow.discount_factor, 6),
                    round(flow.present_value, 2),
                )
                for flow in priced.cashflows
            )

        par = swap_par_rate.__wrapped__(spec, market.curves, csa)  # type: ignore[attr-defined]
        dv01 = swap_dv01.__wrapped__(spec, market.curves, csa)  # type: ignore[attr-defined]
        return (
            ("Curve date", market.report_date.isoformat()),
            ("Discount curve", DISCOUNT_CURVE),
            ("Forecast curve", f"{FORECAST_CURVE} (6M index)"),
            ("Collateral", csa.label),
            ("Notional", round(spec.notional, 2)),
            ("Fixed rate", round(spec.fixed_rate * 100, 6)),
            ("Effective", spec.effective.isoformat()),
            ("Maturity", spec.maturity.isoformat()),
            ("Par rate %", round(par * 100, 6)),
            ("Fixed leg PV", round(priced.fixed_leg_pv, 2)),
            ("Float leg PV", round(priced.float_leg_pv, 2)),
            ("PV (pay fixed)", round(priced.pv, 2)),
            ("Annuity", round(priced.annuity, 2)),
            ("DV01 (+1bp)", round(dv01, 2)),
        )

    def _vcub(
        self, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The two `VCUB` panes, off one fitted surface (spec §11.3)."""
        from treble.analytics.vol.surface import (
            DEFAULT_HALF_LIFE_DAYS,
            DEFAULT_MONEYNESS_BAND,
            MIN_OBSERVATIONS_FOR_CONFIDENT,
        )
        from treble.tapi.vol_surface import (
            VolSurfaceUnavailableError,
            build_vol_surface,
        )

        try:
            built = build_vol_surface(self._store, as_of=as_of)
        except VolSurfaceUnavailableError as error:
            return ((f"no volatility surface: {error}",),)

        surface = built.surface
        if binding == "sys:vcub_grid":
            return tuple(
                (
                    f"{node.expiry_years:g}Y",
                    f"{node.tenor_years:g}Y",
                    round(node.volatility * 1e4, 1),
                    node.observations,
                    round(node.effective_observations, 1),
                    round(node.dispersion * 100, 0),
                    "" if node.is_confident else "thin",
                )
                for node in surface.nodes
            )

        return (
            ("As of", surface.as_of.isoformat()),
            ("Currency", surface.currency),
            ("Quoted in", "normal (Bachelier) vol, basis points"),
            ("Prints read", built.prints_read),
            ("Prints solved", f"{built.prints_solved} ({built.solve_rate:.0%})"),
            ("Days used", built.days_used),
            ("Days without curves", built.days_without_curves),
            ("Each day priced on", "its own curve, never another day's"),
            ("Pooled over", f"{surface.pooled_days} day(s)"),
            ("Decay half-life", f"{DEFAULT_HALF_LIFE_DAYS:g} days"),
            ("Moneyness band", f"±{DEFAULT_MONEYNESS_BAND:.0%}"),
            ("Excluded off-money", surface.excluded_off_the_money),
            ("Excluded off-grid", surface.excluded_no_bucket),
            ("Grid coverage", f"{surface.coverage:.0%}"),
            ("Confident needs", f"{MIN_OBSERVATIONS_FOR_CONFIDENT} effective prints"),
            ("Nothing is interpolated", "an absent node is absent, not guessed"),
        )

    # -- TVAL (spec §15.1) ----------------------------------------------

    def _rels(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`RELS` — related securities, by legal ownership (§9.5).

        Related means an entity relationship GLEIF asserts, not a
        similarity somebody judged. A screen that mixed the two would let
        "same parent" and "same sector" sit in one column while only one of
        them comes from a registry.
        """
        from treble.tapi.related import NoRelationsError, related_securities

        if security is None:
            return (("no security selected: RELS relates one bond", None, None, None, None),)
        try:
            subject = self.resolve(security)
            related = related_securities(self._store, identifier=str(subject), as_of=as_of)
        except (SecurityNotFoundError, NoRelationsError) as error:
            return ((str(error), None, None, None, None),)

        if binding == "sys:rels_method":
            return (
                ("Bond", related.subject),
                ("Issuer", related.issuer or "—"),
                ("Issuer LEI", related.lei),
                ("Ultimate parent", related.ultimate_parent or "none recorded by GLEIF"),
                ("Entities under it", str(related.family_size)),
                ("Reachable here", f"{related.reachable} security(ies) in N-PORT filings"),
                (
                    "Why they differ",
                    "the family comes from the registry, the securities from filings",
                ),
                ("Relation", "shared legal ownership — not sector, rating or seniority"),
                ("Why not sector", "no sector or rating source this store's terms permit"),
                ("Issuer identity", "GLEIF registration where it differs from the filer's"),
                ("Scope", "straight debt only; securitisations are a different credit"),
            )

        rows: list[tuple[str | float | int | None, ...]] = [
            (
                item.relationship,
                item.identifier,
                item.issuer,
                item.maturity.isoformat() if item.maturity else None,
                item.coupon_pct,
            )
            for item in (*related.same_issuer, *related.family)
        ]
        # The count the rows cannot show. A family of 130 with one bond
        # here is coverage, not a small group, and a reader seeing one row
        # would conclude the latter.
        rows.append(
            (
                f"{related.family_size} entities under this parent, "
                f"{related.reachable} with paper held here",
                None,
                None,
                None,
                None,
            )
        )
        return tuple(rows)

    def _oas1(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`OAS1` — what callability would cost, under a stated structure.

        Every row is a conditional. N-PORT publishes no call schedule, so
        the structure is an assumption named in the row rather than the
        bond's terms, and the volatility is user-supplied in the way
        ADR-0003 already requires.
        """
        from treble.tapi.option_cost import (
            MIN_OPTION_YEARS,
            STRUCTURES,
            VOLATILITIES,
            OptionCostUnavailableError,
            option_cost_grid,
        )
        from treble.tapi.spreads import BondNotPriceableError

        if security is None:
            return (("no security selected: OAS1 prices one bond", None, None, None, None),)
        try:
            subject = self.resolve(security)
            grid = option_cost_grid(self._store, identifier=str(subject), as_of=as_of)
        except (
            SecurityNotFoundError,
            BondNotPriceableError,
            OptionCostUnavailableError,
        ) as error:
            return ((str(error), None, None, None, None),)

        if binding == "sys:oas1_method":
            return (
                ("Bond", grid.identifier),
                ("Issuer", grid.issuer or "—"),
                ("Maturity", grid.maturity.isoformat()),
                ("Price", f"{grid.price:.4f} — implied mark, not a traded level"),
                ("Bullet Z-spread", f"{grid.z_spread_bp:+.1f}bp — the bond as it actually is"),
                ("Discount curve", f"USD SOFR OIS, {grid.curve_date}"),
                ("MEASURED", "the price, the coupon, the maturity, the curve"),
                ("ASSUMED", "the call schedule and the volatility — both are columns"),
                (
                    "Why assumed",
                    "N-PORT has no call schedule field; without one OAS equals Z",
                ),
                ("Structures tested", "; ".join(label for label, _ in STRUCTURES)),
                ("Volatilities", ", ".join(f"{v:.2%}" for v in VOLATILITIES)),
                ("Model", "Hull-White lattice, mean reversion 3%"),
                ("Option cost", "bullet Z less OAS; positive because the call is the issuer's"),
                (
                    "Minimum option life",
                    f"{MIN_OPTION_YEARS:g}y — shorter is step size, not structure",
                ),
                ("Skipped", "; ".join(f"{k}: {v}" for k, v in grid.skipped) or "none"),
            )

        rows: list[tuple[str | float | int | None, ...]] = [
            (
                row.structure,
                f"{row.volatility:.2%}",
                round(row.oas_bp, 1),
                round(row.option_cost_bp, 1),
                # Flagged rather than hidden. A call right belongs to the
                # issuer, so it can only narrow the holder's spread; a
                # negative cost means the lattice, the schedule or the vol
                # is wrong, not that the market is unusual.
                "" if row.option_cost_bp >= 0.0 else "NEGATIVE — check the inputs",
            )
            for row in grid.rows
        ]
        rows.insert(
            0,
            (
                "BULLET (as it actually is)",
                "—",
                round(grid.z_spread_bp, 1),
                0.0,
                "no call: Z-spread is the OAS",
            ),
        )
        return tuple(rows)

    def _sprd(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`SPRD` — one bond over each benchmark (§10.1).

        Three rows rather than three columns on one row: each is measured
        against a different curve on a possibly different date, and a
        single row would have to pick one date to display or drop them all.
        """
        from treble.tapi.spreads import (
            GOVT_DAY_COUNT,
            MIN_GOVT_TENOR_YEARS,
            BondNotPriceableError,
            bond_spreads,
        )

        if security is None:
            return (("no security selected: SPRD measures one bond", None, None, None),)
        try:
            subject = self.resolve(security)
            measured = bond_spreads(self._store, identifier=str(subject), as_of=as_of)
        except (SecurityNotFoundError, BondNotPriceableError) as error:
            return ((str(error), None, None, None),)

        if binding == "sys:sprd_method":
            from treble.tapi.issuer_curves import ASSUMED_DAY_COUNT, ASSUMED_FREQUENCY

            return (
                ("Bond", measured.identifier),
                ("Issuer", measured.issuer or "—"),
                ("Price", f"{measured.price:.4f} — implied mark, not a traded level"),
                ("Yield", f"{measured.yield_pct:.4f}% at the bond's own frequency"),
                ("Government curve", f"UST CMT, bootstrapped {measured.govt_date or '—'}"),
                ("Swap curve", f"USD SOFR OIS, bootstrapped {measured.swap_date or '—'}"),
                (
                    "Bills excluded",
                    f"CMT tenors under {MIN_GOVT_TENOR_YEARS:g}y are discount-basis",
                ),
                ("Curve day count", GOVT_DAY_COUNT),
                (
                    "Assumed frequency",
                    ASSUMED_FREQUENCY.name.title() + " — N-PORT does not report it",
                ),
                ("Assumed day count", ASSUMED_DAY_COUNT.value + " — N-PORT does not report it"),
                (
                    "I-spread method",
                    "the same computation as G against the swap curve, so one conversion",
                ),
                (
                    "Compounding",
                    "the curve is converted to the bond's frequency before subtracting",
                ),
                ("Currency", "both benchmarks are USD; other currencies are refused"),
            )

        def row(
            name: str, value: float | None, when: date | None, note: str
        ) -> tuple[str | float | int | None, ...]:
            return (
                name,
                None if value is None else round(value, 1),
                when.isoformat() if when else None,
                note,
            )

        rows = [
            row("G-spread", measured.g_spread_bp, measured.govt_date, "over the government curve"),
            row("I-spread", measured.i_spread_bp, measured.swap_date, "over the swap curve"),
            row("Z-spread", measured.z_spread_bp, measured.swap_date, "parallel shift, all flows"),
            row(
                "Swap spread",
                measured.swap_spread_bp,
                None,
                "G less I — the check on the other two",
            ),
        ]
        return tuple(rows)

    def _ddis(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`DDIS` — an issuer's maturity ladder, from the selected bond.

        The security is a *bond*; the ladder is its *issuer's*. Resolving
        one to the other is the whole reason the ISIN path and the GLEIF
        mapping had to exist first: before them a bond could not be
        addressed and its issuer could not be identified.
        """
        from treble.tapi.debt import (
            DebtDistributionUnavailableError,
            debt_distribution,
        )

        if security is None:
            return (("no security selected: DDIS profiles the issuer of a bond", None),)
        try:
            subject = self.resolve(security)
        except SecurityNotFoundError as error:
            return ((str(error), None),)

        lei = next(
            (
                str(fact.value)
                for field in ("gleif:lei", "nport:lei")
                for fact in self._store.read(subject, field, as_of=as_of)
                if isinstance(fact.value, str)
            ),
            None,
        )
        if lei is None:
            # Distinct from "this issuer has no bonds". The instrument is
            # here and carries no issuer identity, which is a gap in the
            # holding record rather than a fact about the issuer's debt.
            return (
                (
                    f"{subject} carries no issuer LEI, so its issuer cannot be identified",
                    None,
                ),
            )
        try:
            profile = debt_distribution(self._store, lei=lei, as_of=as_of)
        except DebtDistributionUnavailableError as error:
            return ((str(error), None),)

        if binding == "sys:ddis_method":
            return (
                ("Issuer", profile.issuer_name or "—"),
                ("LEI", profile.lei),
                ("Report date", profile.report_date.isoformat()),
                ("Date chosen by", "most usable bonds, not most recent"),
                (
                    "Why not most recent",
                    "N-PORT coverage thins until funds file; the newest date is "
                    "reliably the sparsest",
                ),
                ("Bonds on the ladder", str(profile.total_bonds)),
                ("Excluded on that date", str(len(profile.excluded))),
                (
                    "What HELD means",
                    "face reported by a filing fund — not the amount outstanding",
                ),
                (
                    "Why it is not a total",
                    "several funds hold the same bond and write to one subject, so a "
                    "point-in-time read returns one filing's position",
                ),
                ("Coupon", "percent, unweighted mean — weighting would weight the filer"),
                ("Excluded categories", "anything but straight debt (DBT): ABS, CLO, derivatives"),
                ("Issuer identity", "GLEIF registration where it differs from the filer's"),
                ("Currencies", ", ".join(f"{code} x{n}" for code, n in profile.currencies) or "—"),
            )

        rows: list[tuple[str | float | int | None, ...]] = [
            (
                bucket.label,
                bucket.bonds,
                round(bucket.held_face, 2),
                None if bucket.mean_coupon_pct is None else round(bucket.mean_coupon_pct, 3),
                bucket.earliest.isoformat() if bucket.earliest else None,
                bucket.latest.isoformat() if bucket.latest else None,
            )
            for bucket in profile.buckets
        ]
        rows.append(
            (
                "TOTAL",
                profile.total_bonds,
                round(profile.total_held_face, 2),
                None,
                None,
                None,
            )
        )
        return tuple(rows)

    def _eco(
        self, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`ECO` — the macro dashboard and its method tab (§7.4).

        Twenty-five series were arriving on a daily refresh with nothing
        able to display them. The unit and the observation date are columns
        rather than footnotes: without the first a reader cannot tell an
        index from a percentage, and without the second a June CPI print
        sits beside a Monday vol close looking comparable.
        """
        from treble.tapi.macro import CATALOGUE, GROUPS, Frequency, macro_dashboard

        readings = macro_dashboard(self._store, as_of=as_of)
        today = as_of.date()

        if binding == "sys:eco_method":
            rows: list[tuple[str | float | int | None, ...]] = [
                ("Source", "FRED (Federal Reserve Bank of St. Louis)"),
                ("Series catalogued", str(len(CATALOGUE))),
                ("Ingested here", str(sum(1 for r in readings if r.ingested))),
                ("Groups", ", ".join(GROUPS)),
                ("Units", "per series, from FRED's own stated unit — never inferred"),
                (
                    "Stale after",
                    "; ".join(f"{f.value} {f.tolerated_days}d" for f in Frequency),
                ),
                (
                    "Why per series",
                    "monthly CPI six weeks old is a release lag; a daily series six "
                    "weeks old is a dead feed",
                ),
                (
                    "Change column",
                    "difference from the previous observation, not a revision — one "
                    "fact per observation date",
                ),
                (
                    "Missing values",
                    "FRED writes '.' for a non-publishing day; those are skipped, so a "
                    "holiday does not read as a zero",
                ),
                ("Licence", "series carry their own; FRED redistributes rather than originates"),
            ]
            return tuple(rows)

        return tuple(
            (
                reading.series.group,
                reading.series.series_id,
                reading.series.title,
                reading.series.unit,
                reading.value,
                reading.observed.isoformat() if reading.observed else None,
                # Rounded here, not in a renderer. A change of
                # -0.029999999999999805 is float noise from the subtraction
                # and would show in every surface that displayed it;
                # rounding once keeps the two renderers agreeing, which is
                # the same reason ALLQ rounds its spread in the binding.
                # Four places: enough for a breakeven quoted in basis
                # points, and PAYEMS in thousands is unharmed by it.
                None if reading.change is None else round(reading.change, 4),
                reading.staleness(today=today),
            )
            for reading in readings
        )

    def _tval_peers(self, *, as_of: datetime) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`sys:tval_peers` — relative value for bonds with no issuer curve.

        Twenty-eight of 153 issuers have the three bonds a curve needs, so
        157 of 269 bonds were absent from the rich/cheap ranking entirely —
        not refused on screen, simply not there. `ComparableSet` is the
        machinery for exactly those and was called by nothing outside its
        own test suite.

        The peer count is shown over the universe size because the ratio is
        what says how selective the match was. Matching on currency,
        issuer category and maturity proximity routinely selects 226 of 233
        bonds, which is a market level wearing the word peer — and with
        rating, sector and seniority absent, that is what these dimensions
        can deliver.
        """
        from treble.tapi.peers import NoPeersError, peer_values

        try:
            values = peer_values(self._store, as_of=as_of)
        except NoPeersError as error:
            return ((str(error), None, None, None, None, None, None),)

        rows: list[tuple[str | float | int | None, ...]] = [
            (
                value.issuer or value.identifier,
                value.identifier.removeprefix("isin:"),
                value.maturity.isoformat(),
                round(value.yield_pct, 3),
                f"{value.peer_count}/{value.universe_size}",
                round(value.residual_bp, 1),
                value.verdict,
            )
            for value in values
        ]
        # The dimensions are a row, not a footnote: they are the reason a
        # peer call is weaker than a curve call, and a reader ranking these
        # against the ISSUER CURVES tab needs to see it in the same table.
        rows.append(
            (
                f"not matched on: {', '.join(values[0].missing_dimensions)}",
                None,
                None,
                None,
                None,
                None,
                f"{sum(1 for v in values if not v.in_noise)} of {len(values)} significant",
            )
        )
        return tuple(rows)

    def _tval_snapshots(
        self, security: SecurityQuery | None, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """`sys:tval_snapshots` — §15.5's publication times, bid/mid/ask.

        Recorded for a long time as data-blocked: "needs intraday quote
        captures no free source here provides". Half true. The captures are
        genuinely absent and stay absent; knowing when 4pm New York falls in
        UTC never depended on them, and the two were collapsed into one
        excuse. That is the same move as the product catalogue that claimed
        "HICP stored" on a store holding none.

        So the times are real and the marks are whatever the contribution
        book holds — which on this install is nothing, and the panel says
        so per row rather than rendering four blank lines.
        """
        from treble.analytics.tval.snapshots import SNAPSHOT_TIMES, snapshot_series

        rows: list[tuple[str | float | int | None, ...]] = []
        if security is None:
            return (("no security selected", None, None, None, ""),)
        subject = str(self.resolve(security))
        day = as_of.date()
        # One book read per publication instant. The books are gathered
        # here because only this layer knows where they come from; the
        # analytics module takes them already resolved so it can be tested
        # without a service at all.
        books = {
            snapshot_time.at(day).isoformat(): self._contributions.book(
                subject, as_of=snapshot_time.at(day)
            )
            for snapshot_time in SNAPSHOT_TIMES
        }
        # `.value` unwraps the I3 envelope: the panel shows the series, and
        # the envelope is what `MDL` reads to say which model produced it.
        series = snapshot_series(books, day=day).value
        for snapshot in series.snapshots:
            rows.append(
                (
                    snapshot.time_name,
                    snapshot.at.strftime("%H:%M UTC"),
                    snapshot.bid,
                    snapshot.mid,
                    snapshot.ask,
                    snapshot.contributors,
                )
            )
        if series.all_empty:
            rows.append(
                (
                    "no contributed quotes on this install, so every time is empty rather "
                    "than equal",
                    "",
                    None,
                    None,
                    None,
                    0,
                )
            )
        elif series.unchanged:
            # Four identical rows and four independent agreeing evaluations
            # render the same and are very different claims.
            rows.append(("unchanged across all published times", "", None, None, None, 0))
        return tuple(rows)

    def _tval_residual(
        self, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """Whether the residual layer earns its place (§15.4).

        The panel exists to show a *refusal* as often as a result. A
        boosted tree on a couple of hundred bonds always fits the training
        set and usually does worse out of sample, so "measured, rejected"
        is a frequent and correct answer — and it differs from "not
        attempted" in a way a blank row would hide.

        Residuals come from `IssuerCurveSet.values_for`, which is the
        method that already pairs each fitted bond with its curve. I
        previously recorded this panel as blocked on an API change, having
        confused `IssuerCurve.bonds` (identifiers) with
        `IssuerCurveSet.bonds` (the fitted objects). Nothing needed
        changing.
        """
        from treble.analytics.tval.residual import (
            MIN_OBSERVATIONS,
            MIN_SKILL,
            ResidualModelUnavailableError,
            ResidualObservation,
            fit_residual_model,
        )
        from treble.tapi.issuer_curves import IssuerCurvesUnavailableError, build_issuer_curves

        try:
            fitted = build_issuer_curves(self._store, as_of=as_of)
        except IssuerCurvesUnavailableError as error:
            return ((str(error), None),)

        observations: list[ResidualObservation] = []
        for issuer in fitted.issuers:
            count = len(fitted.bonds[issuer])
            for call in fitted.values_for(issuer):
                facts = {
                    f.field: f.value
                    for f in self._store.subject_facts(TUID(call.identifier), as_of=as_of)
                }
                coupon, size = facts.get("nport:annualizedRt"), facts.get("nport:valUSD")
                if not isinstance(coupon, int | float) or not isinstance(size, int | float):
                    continue
                observations.append(
                    ResidualObservation(
                        identifier=call.identifier,
                        residual=call.residual_bp / 1e4,
                        coupon=float(coupon),
                        size_usd=float(size),
                        issuer_bond_count=count,
                    )
                )

        try:
            model = fit_residual_model.__wrapped__(observations)  # type: ignore[attr-defined]
        except ResidualModelUnavailableError as error:
            return (
                (str(error), None),
                ("BONDS WITH COUPON AND SIZE", len(observations)),
                ("MINIMUM TO MEASURE ON", MIN_OBSERVATIONS),
            )
        return (
            ("BONDS MEASURED", model.observations),
            ("NULL MAE (bp)", round(model.null_mae_bp, 2)),
            ("MODEL MAE (bp)", round(model.model_mae_bp, 2)),
            ("SKILL vs NULL", f"{model.skill:+.1%}"),
            ("BAR TO APPLY", f"{MIN_SKILL:.0%}"),
            (
                "VERDICT",
                "applied" if model.is_useful else "measured and rejected — curve stands alone",
            ),
        )

    def _allq_evaluated(
        self, security: SecurityQuery | None, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """What the contributed book implies as a price (§15.1-15.3).

        `ALLQ` shows the quotes; this shows what they add up to, with the
        ASC 820 level and the score beside it. Both come from the same book
        at the same moment, so a user can see the evidence and the
        conclusion without switching screens and wondering whether they
        were computed from the same thing.

        The one-way count is shown even when the price succeeds. A book of
        ten quotes that produced two observations is a different statement
        about liquidity from one that produced ten, and an evaluated price
        that did not say so would look equally well-supported either way.
        """
        from treble.analytics.tval.evaluate import UnpriceableError
        from treble.tapi.evaluated import evaluate_contributed, observations_from_book

        if security is None:
            return (("No security selected.", None),)
        try:
            subject = self.resolve(security)
        except SecurityNotFoundError as error:
            return ((str(error), None),)

        book = self._contributions.book(str(subject), as_of=as_of)
        if book.is_empty:
            # ALLQ's own correct-when-empty case, restated here rather than
            # borrowed: an empty network and a screen that failed to load
            # must not render alike.
            return (("No contributed quotes for this instrument.", None),)
        inputs = observations_from_book(book, as_of=as_of)
        try:
            priced = evaluate_contributed(book, as_of=as_of)
        except UnpriceableError as error:
            return ((str(error), None),)
        return (
            ("EVALUATED PRICE", priced.price),
            ("FAIR VALUE LEVEL", priced.level.value),
            ("SCORE (1-10)", priced.score),
            ("OBSERVATIONS", len(priced.observations)),
            ("ONE-WAY QUOTES SKIPPED", inputs.one_way_skipped),
            # "No evidence" and "no *recent* evidence" are different states
            # and the score alone cannot separate them.
            ("DROPPED AS STALE", priced.dropped_stale),
        )

    def _sptr_documents(
        self, security: SecurityQuery | None, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The source documents behind a subject's facts (§8.3).

        `SPTR` already renders the provenance DAG — which model, which
        inputs, which extraction. This is the step further down: the
        document itself, content-addressed, so what is listed is the one
        the facts were parsed from rather than whatever the URL serves
        today. EDGAR restates and vendors correct.

        The restricted flag is on the row. A payload from a personal-use
        source is the most concentrated form of that source's data — the
        whole document rather than the fields parsed out of it — and a user
        about to export one should meet that before the export refuses.
        """
        from treble.tapi.documents import DocumentUnavailableError, documents_for

        if security is None:
            return (("No security selected.", None, None, None),)
        try:
            subject = self.resolve(security)
        except SecurityNotFoundError as error:
            return ((str(error), None, None, None),)
        try:
            refs = documents_for(self._store, subject, as_of=as_of)
        except DocumentUnavailableError as error:
            return ((str(error), None, None, None),)
        return tuple(
            (
                ref.source_system,
                ref.retrieved_at.date().isoformat(),
                ref.fact_count,
                "restricted" if ref.redistribution_restricted else "",
            )
            for ref in refs
        )

    #: The order `SWPM` lists §12.1 products in. Only the order lives here
    #: now: the readiness of each is asked of the store by
    #: `products.product_readiness`, because it used to be asserted here as
    #: a fixed string and one of those strings read "priceable — HICP
    #: stored" on an install holding no inflation facts at all. A claim
    #: about a user's own data that is written rather than measured is the
    #: same defect as a test that cannot fail.
    _PRODUCT_ORDER: tuple[str, ...] = (
        "CAP / FLOOR",
        "CMS",
        "CANCELLABLE",
        "ASSET SWAP",
        "INFLATION ZC",
        "CROSS-CURRENCY",
        "TOTAL RETURN",
    )

    def _swpm_products(
        self, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """What `SWPM` can price today, and what each product still needs.

        A catalogue rather than a price, because the products need
        different caller inputs and inventing them is what this repository
        spent a long time learning not to do. A screen that offered
        "cross-currency" and then priced it off a zero basis would be
        asserting the basis is zero on an instrument whose entire purpose
        is that it is not.

        The status column is measured against this store, so a product
        whose data has not been ingested — or whose source stopped flowing
        — says so instead of promising a price the pricer would refuse.
        """
        from treble.tapi.products import product_readiness

        readiness = {r.product: r for r in product_readiness(self._store, as_of=as_of)}
        # The catalogue and the readiness set must not drift. A product the
        # service knows about and the screen omits reads as unsupported
        # rather than as unlisted.
        missing = sorted(set(readiness) - set(self._PRODUCT_ORDER))
        if missing:
            raise RuntimeError(
                f"products.py reports readiness for {', '.join(missing)} and SWPM does "
                "not list them. A product the service knows about and the screen omits "
                "reads as unsupported rather than as unlisted"
            )
        rows: list[tuple[str | float | int | None, ...]] = []
        for name in self._PRODUCT_ORDER:
            entry = readiness[name]
            status = (
                f"priceable — {entry.detail}" if entry.ready else f"not priceable — {entry.detail}"
            )
            rows.append((name, entry.user_input, status))
        return tuple(rows)

    def _fa_ratios(
        self, security: SecurityQuery | None, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """Fundamental ratios for `FA`, with the tag each rests on (§14.1).

        The tag is on the row, not in a footnote. Filers do not agree on a
        revenue tag — 2,349 report `Revenues` and 3,184 report
        `RevenueFromContractWithCustomerExcludingAssessedTax` — so two
        margins on two screens can be built from different measurements. A
        column showing only the percentage would present them as comparable.

        Concepts the period does not supply are listed rather than omitted:
        a ratio that is missing and one nobody asked for look identical in
        a table, and only the first is a data gap.
        """
        from treble.tapi.equity_ratios import RatiosUnavailableError, ratios_for

        if security is None:
            return (("No security selected.", None, None),)
        try:
            subject = self.resolve(security)
        except SecurityNotFoundError as error:
            return ((str(error), None, None),)
        try:
            found = ratios_for(self._store, subject, as_of=as_of)
        except RatiosUnavailableError as error:
            return ((str(error), None, None),)

        # Which tag fed each ratio, so the row can name it rather than
        # leaving the reader to assume every filer measured the same thing.
        feeds = {
            "gross_margin": "revenue",
            "operating_margin": "revenue",
            "net_margin": "revenue",
            "return_on_equity": "equity",
            "return_on_assets": "assets",
            "leverage": "equity",
        }
        rows: list[tuple[str | float | int | None, ...]] = [
            ("PERIOD", found.period.isoformat(), None)
        ]
        for name, value in sorted(found.ratios.items()):
            tag = found.sources.get(feeds[name], "")
            rows.append((name.upper().replace("_", " "), value, tag.removeprefix("us-gaap:")))
        if found.missing:
            rows.append(("NOT REPORTED THIS PERIOD", ", ".join(sorted(found.missing)), None))
        return tuple(rows)

    def _entity(
        self, security: SecurityQuery | None, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """Ownership for the security on screen (§9.5).

        The LEI comes from the instrument's own stored facts rather than
        from the resolver: an instrument's issuer is a fact somebody filed,
        and inferring it from a ticker would attribute a bond to whichever
        entity happens to share its name.

        Direct and ultimate parent are shown as separate rows because GLEIF
        states them separately and they disagree often — three of six
        entities sampled from this store. A screen showing one "parent"
        would have to pick, and picking is what `core/entity_graph.py`
        refuses to do.
        """
        from treble.tapi.entity import EntityUnknownError, ancestry_of, children_of

        if security is None:
            return (("No security selected.", None),)
        try:
            subject = self.resolve(security)
        except SecurityNotFoundError as error:
            return ((str(error), None),)

        leis = [
            str(f.value)
            for f in self._store.subject_facts(subject, as_of=as_of)
            if f.field in ("nport:lei", "gleif:lei") and isinstance(f.value, str)
        ]
        if not leis:
            return (
                (
                    f"{subject} carries no LEI, so no ownership can be shown. An "
                    "instrument with no filed issuer and one whose issuer is unknown "
                    "render alike and are not alike.",
                    None,
                ),
            )
        lei = TUID(f"lei:{leis[0]}")

        if binding == "sys:entity_children":
            kids = children_of(self._store, lei, as_of=as_of)
            if not kids:
                return ((f"{lei} consolidates no filed subsidiaries.", None),)
            return tuple((str(k), None) for k in kids[:200])

        try:
            found = ancestry_of(self._store, lei, as_of=as_of)
        except EntityUnknownError as error:
            return ((str(error), None),)
        return (
            ("ENTITY", str(lei)),
            ("DIRECT PARENT", str(found.direct_parent) if found.direct_parent else "—"),
            ("ULTIMATE PARENT", str(found.ultimate_parent) if found.ultimate_parent else "—"),
            ("PARENTS AGREE", "yes" if found.parents_agree else "no — see both above"),
            ("EDGES FILED", len(found.edges)),
        )

    def _tval(
        self, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The three `TVAL` panes, off one set of fitted issuer curves.

        A failure to fit returns the reason as a row rather than an empty
        table: a bond with nothing to say about it and a model that could not
        be built must not look the same.
        """
        from collections import Counter

        from treble.analytics.tval.relative import (
            AVAILABLE_DIMENSIONS,
            FAIR_BAND_BP,
            MIN_CURVE_BONDS,
            REQUIRED_DIMENSIONS,
        )
        from treble.tapi.issuer_curves import (
            ASSUMED_DAY_COUNT,
            ASSUMED_FREQUENCY,
            IssuerCurvesUnavailableError,
            build_issuer_curves,
        )

        try:
            fitted = build_issuer_curves(self._store, as_of=as_of)
        except IssuerCurvesUnavailableError as error:
            return ((f"no issuer curves: {error}",),)

        if binding == "sys:tval_curves":
            return tuple(
                (
                    fitted.names[lei][:28],
                    len(fitted.curves[lei].bonds),
                    round(fitted.curves[lei].intercept * 100, 3),
                    round(fitted.curves[lei].slope_bp_per_year, 1),
                    round(fitted.curves[lei].residual_rms_bp, 1),
                )
                for lei in fitted.issuers
            )

        if binding == "sys:tval_values":
            calls = [
                (fitted.names[lei][:22], value)
                for lei in fitted.issuers
                for value in fitted.values_for(lei)
            ]
            # Significant first, then by size. A screen ordered by residual
            # alone puts the noisiest curves at the top, which is the
            # opposite of what a reader wants.
            calls.sort(key=lambda pair: (not pair[1].is_significant, -abs(pair[1].residual_bp)))
            return tuple(
                (
                    name,
                    value.identifier.removeprefix("isin:"),
                    round(value.observed_yield * 100, 3),
                    round(value.curve_yield * 100, 3),
                    round(value.residual_bp, 1),
                    value.verdict if value.is_significant else f"{value.verdict} (in noise)",
                )
                for name, value in calls
            )

        # Per-issuer messages name the LEI, so each is unique and would fill
        # the table with a hundred separate counts of one.
        def _bucket(reason: str) -> str:
            if "usable bond" in reason:
                return f"issuer had under {MIN_CURVE_BONDS} usable bonds"
            if "span" in reason:
                return "issuer spanned too little maturity"
            return reason

        reasons = Counter(_bucket(reason) for _, reason in fitted.excluded)
        # Every value is kept short enough to survive the pane's truncation.
        # A row reading "absent from" with the rest cut off states the
        # opposite of what it means, which is worse than not showing it.
        return (
            ("Report date", fitted.report_date.isoformat()),
            ("Date chosen by", "most fittable issuers, not most recent"),
            ("Why not most recent", "N-PORT coverage thins until funds file"),
            *(
                (f"Coverage {day.isoformat()}", f"{count} issuer(s), {MIN_CURVE_BONDS}+ bonds")
                for day, count in fitted.coverage
            ),
            ("Issuer curves fitted", len(fitted.curves)),
            ("Price source", "N-PORT value / face balance"),
            ("Price is", "an implied mark, NOT a traded level"),
            ("Assumed frequency", ASSUMED_FREQUENCY.name.title()),
            ("Assumed day count", ASSUMED_DAY_COUNT.value),
            ("Matched on", ", ".join(AVAILABLE_DIMENSIONS)),
            ("NOT matched on", ", ".join(REQUIRED_DIMENSIONS)),
            ("Those three are", "absent; similarity is incomplete"),
            ("Fair band", f"max({FAIR_BAND_BP:.0f}bp, the curve's RMS)"),
            *((f"Excluded: {reason}"[:36], count) for reason, count in reasons.most_common(2)),
        )

    # -- PORT (spec §16.3) ----------------------------------------------

    def _port(
        self, binding: str, *, as_of: datetime
    ) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The three `PORT` panes, off one fitted risk model.

        A failure to fit returns the reason as a row rather than an empty
        table, for the reason that governs every screen here: a portfolio
        with no risk and a portfolio whose model could not be built must not
        look the same, and zero volatility is the more believable of the two.
        """
        from treble.analytics.risk.factors import portfolio_risk
        from treble.tapi.factor_model import (
            FactorModelUnavailableError,
            build_factor_model,
            template_portfolio,
        )

        try:
            fitted = build_factor_model(self._store, as_of=as_of)
        except FactorModelUnavailableError as error:
            return ((f"no risk model: {error}",),)

        if binding == "sys:port_exposures":
            return tuple(
                (
                    name,
                    *(round(fitted.exposures.beta(name, factor), 4) for factor in FACTORS),
                    round(float(fitted.exposures.r_squared[index]), 3),
                    round(fitted.exposures.specific_volatility(name) * 100, 2),
                )
                for index, name in enumerate(fitted.exposures.assets)
            )

        weights = template_portfolio(fitted)
        risk = portfolio_risk.__wrapped__(  # type: ignore[attr-defined]
            weights, fitted.exposures, fitted.covariance
        )

        if binding == "sys:port_factors":
            total = risk.total_volatility**2
            return tuple(
                (
                    factor,
                    round(fitted.covariance.volatility(factor) * 100, 2),
                    # Portfolio beta to the factor: the weighted sum of the
                    # holdings' betas, which is what the contribution below
                    # is driven by.
                    round(
                        sum(
                            weights[name] * fitted.exposures.beta(name, factor)
                            for name in fitted.exposures.assets
                        ),
                        4,
                    ),
                    round(contribution * 1e4, 2),
                    round(contribution / total * 100, 2) if total else None,
                )
                for factor, contribution in risk.factor_contributions
            )

        largest = sorted(risk.marginal_contributions, key=lambda pair: -pair[1])[:5]
        return (
            ("Window", f"{fitted.first_date.isoformat()} to {fitted.last_date.isoformat()}"),
            ("Observations", fitted.observations),
            ("Assets", len(fitted.exposures.assets)),
            ("Factors", len(fitted.covariance.factors)),
            ("Weighting", "equal — a template, not a holding"),
            ("Total volatility %", round(risk.total_volatility * 100, 2)),
            ("Factor volatility %", round(risk.factor_volatility * 100, 2)),
            ("Specific volatility %", round(risk.specific_volatility * 100, 2)),
            ("Factor share of variance %", round(risk.factor_share * 100, 1)),
            *((f"Marginal: {name}", round(value * 100, 3)) for name, value in largest),
        )

    def _swpm_basis(self, market: SwapMarket) -> tuple[tuple[str | float | int | None, ...], ...]:
        """The EURIBOR 3M/6M tenor basis, per tenor.

        Shows the basis-swap par spread beside the difference in the two
        curves' own input quotes. They are not equal and should not be: the
        spread is quoted on a quarterly ACT/360 leg while the quotes are par
        rates against an annual 30/360 fixed leg, so the ratio between them
        is the annuity ratio. Printing both puts that where a reader can
        check it, and a ratio that stopped being flat across tenors would be
        visible at once.
        """
        import QuantLib as ql

        from treble.analytics import _ql
        from treble.analytics.derivatives.basis import BasisSwapSpec, price_basis_swap
        from treble.analytics.derivatives.csa import CsaTerms
        from treble.tapi.swap_market import (
            CALENDAR,
            DISCOUNT_CURVE,
            FORECAST_CURVE,
            SHORT_FORECAST_CURVE,
            _tenor_years,
        )

        if not market.short_rates:
            return (
                (
                    f"no {SHORT_FORECAST_CURVE} curve on {market.report_date}: a tenor basis "
                    "needs two real curves, and interpolating the short one from the long "
                    "one would make the basis a property of the interpolator",
                ),
            )

        csa = CsaTerms(collateral_currency="EUR", discount_curve=DISCOUNT_CURVE)
        calendar = _ql.calendar(CALENDAR)
        start = _ql.to_ql_date(market.report_date)
        rows: list[tuple[str | float | int | None, ...]] = []
        for tenor in sorted(set(market.short_rates) & set(market.forecast_rates), key=_tenor_years):
            maturity = _ql.from_ql_date(calendar.advance(start, ql.Period(tenor)))
            spec = BasisSwapSpec(
                notional=100_000_000.0,
                effective=market.report_date,
                maturity=maturity,
                pay_curve=SHORT_FORECAST_CURVE,
                receive_curve=FORECAST_CURVE,
                calendar=CALENDAR,
            )
            priced = price_basis_swap.__wrapped__(spec, market.curves, csa)  # type: ignore[attr-defined]
            quote_gap = (market.forecast_rates[tenor] - market.short_rates[tenor]) * 1e4
            rows.append(
                (
                    tenor,
                    round(market.short_rates[tenor] * 100, 4),
                    round(market.forecast_rates[tenor] * 100, 4),
                    round(quote_gap, 2),
                    round(priced.par_spread_bp, 2),
                    round(priced.par_spread_bp / quote_gap, 3) if quote_gap else None,
                )
            )
        return tuple(rows)

    def _swpm_ois(self, market: SwapMarket) -> tuple[tuple[str | float | int | None, ...], ...]:
        """A spot-starting 10-year ESTR OIS, struck at its own par rate.

        The floating leg is the overnight index *compounded daily*, not a
        discrete forward. The pane reports the identity that makes that leg
        exact — a self-discounted compounded leg is worth
        `N x (D(start) - D(end))` — beside the computed value, because a
        reader who can see the two agree can see the compounding is right
        rather than taking it on trust.
        """
        from treble.analytics.derivatives.csa import CsaTerms
        from treble.analytics.derivatives.swap import price_swap, swap_dv01, swap_par_rate
        from treble.tapi.swap_market import DISCOUNT_CURVE

        spec = self._swpm_trade(market).model_copy(
            update={
                # The OIS curve forecasts its own compounded leg. Annual on
                # both legs is the market convention, and it is stated on the
                # trade rather than assumed by the pricer.
                "forecast_curve": DISCOUNT_CURVE,
                "float_frequency": 1,
                "float_day_count": DayCount.ACT_360,
            }
        )
        csa = CsaTerms(collateral_currency="EUR", discount_curve=DISCOUNT_CURVE)
        par = swap_par_rate.__wrapped__(spec, market.curves, csa)  # type: ignore[attr-defined]
        at_par = spec.model_copy(update={"fixed_rate": par})
        priced = price_swap.__wrapped__(at_par, market.curves, csa)  # type: ignore[attr-defined]
        dv01 = swap_dv01.__wrapped__(at_par, market.curves, csa)  # type: ignore[attr-defined]

        curve = market.curves.curve(DISCOUNT_CURVE)
        floating = [flow for flow in priced.cashflows if flow.leg == "float"]
        identity = spec.notional * (
            curve.discount_at(min(flow.accrual_start for flow in floating))
            - curve.discount_at(max(flow.accrual_end for flow in floating))
        )
        return (
            ("Curve date", market.report_date.isoformat()),
            ("Index", f"{DISCOUNT_CURVE} compounded daily"),
            ("Discount curve", DISCOUNT_CURVE),
            ("Both legs pay", "annually"),
            ("Notional", round(spec.notional, 2)),
            ("Effective", spec.effective.isoformat()),
            ("Maturity", spec.maturity.isoformat()),
            ("Par rate %", round(par * 100, 6)),
            ("PV at par", round(priced.pv, 6)),
            ("Annuity", round(priced.annuity, 2)),
            ("DV01 (+1bp)", round(dv01, 2)),
            ("Compounded payments", len(floating)),
            ("Float leg PV", round(priced.float_leg_pv, 4)),
            ("N x (D(0) - D(T))", round(identity, 4)),
            ("Identity residual", round(abs(priced.float_leg_pv - identity), 8)),
        )

    def _swpm_trade(self, market: SwapMarket) -> SwapSpec:
        """The template trade: a spot-starting 10-year par swap.

        A template, not a position. `SWPM` structures trades and this
        system books none, so the screen shows what a 10-year swap struck
        at today's par rate looks like — cash flows, annuity, DV01 — and
        says on its face that it is a template. Presenting it as a holding
        would be inventing a position.
        """
        import QuantLib as ql

        from treble.analytics import _ql
        from treble.tapi.swap_market import CALENDAR, FORECAST_CURVE

        calendar = _ql.calendar(CALENDAR)
        start = _ql.to_ql_date(market.report_date)
        spot = _ql.from_ql_date(calendar.advance(start, ql.Period(2, ql.Days)))
        tenor = "10Y" if "10Y" in market.tenors else market.tenors[-1]
        maturity = _ql.from_ql_date(calendar.advance(_ql.to_ql_date(spot), ql.Period(tenor)))
        return SwapSpec(
            notional=100_000_000.0,
            fixed_rate=market.forecast_rates[tenor],
            effective=spot,
            maturity=maturity,
            forecast_curve=FORECAST_CURVE,
            fixed_frequency=1,
            fixed_day_count=DayCount.THIRTY_360,
            float_day_count=DayCount.ACT_360,
            calendar=CALENDAR,
        )

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
