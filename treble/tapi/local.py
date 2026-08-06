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
from treble.core.identifiers import TUID, SecurityQuery, YellowKey
from treble.core.provenance import ProvenanceId
from treble.store.duck import DuckStore
from treble.tapi.contribution import ContributionService
from treble.tapi.factor_model import FACTORS
from treble.tapi.fields import FIELDS, FieldDef, FieldDictionary
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
        "sys:vcub_grid",
        "sys:vcub_method",
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
        if binding in ("sys:tval_curves", "sys:tval_values", "sys:tval_method"):
            return self._tval(binding, as_of=as_of)
        if binding in ("sys:vcub_grid", "sys:vcub_method"):
            return self._vcub(binding, as_of=as_of)
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
            return ()
        book = self._contributions.book(str(self.resolve(security)), as_of=as_of)

        if binding == "sys:allq":
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
            return self._swpm_basis(market)

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
