"""FINRA TRACE adapters (spec §8.1.1, §23.1; CLAUDE.md §6).

**Access findings, probed 2026-07-26** (recorded so nobody re-derives them):

- ``https://api.finra.org/data/group/otcMarket/name/weeklySummary`` serves
  CSV with no credentials — the equity OTC surface is genuinely open.
- ``https://api.finra.org/data/group/fixedIncomeMarket/name/...`` returns
  **401 Unauthorized**, not 404. The TRACE datasets exist on the same API
  and require a (free) FINRA API account. Registration is a human step.

- ``treasuryDailyAggregates`` **works** with credentials and is parsed here
  against a recorded fixture (25 rows, schema observed 2026-07-26).
- ``trace`` — the individual corporate transaction dataset — returns **404
  even with a valid token** (GET and POST). It is entitlement-gated and
  sold as a subscription (TRACE Data Feeds / End-Of-Day Transaction File /
  Enhanced Historical). **There is no free route to per-trade corporate
  prints.** The free Gateway lookup's User Agreement separately forbids
  automated access, so it is not an alternative. Per-bond *valuations* come
  from N-PORT instead (see ``treble.ingest.nport``).

Per CLAUDE.md §6 and the spec's honest-positioning rule (§23.1), every
dataset other than the one observed raises ``NotImplementedError`` rather
than being parsed on a guessed schema: a bond number that is confidently
wrong is the worst failure mode this system has.

Credentials come from ``FINRA_API_CLIENT_ID`` / ``FINRA_API_CLIENT_SECRET``
(gitignored ``.env``); constructing the adapter without them raises.

Dissemination caps (CLAUDE.md §6): TRACE prints cap large trades — a $5MM+
IG print shows as "5MM+". The cap must never be read as the actual size;
:data:`SIZE_CAPPED_FIELD` carries the flag through to TVAL weighting, and
the parser must set it when it is written.
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Iterator
from datetime import date

import httpx

from treble.core.facts import Fact
from treble.core.identifiers import TUID
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import (
    ParsedBatch,
    RawPayload,
    SourceAdapter,
    SourceMeta,
    utcnow,
)
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

FINRA_DATA_URL = "https://api.finra.org/data/group/{group}/name/{dataset}"
FINRA_TOKEN_URL = "https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"  # noqa: S105

#: The one dataset whose schema has been observed and parsed.
TREASURY_DAILY_AGGREGATES = "treasuryDailyAggregates"

#: Set on every trade fact whose reported size hit the dissemination cap.
SIZE_CAPPED_FIELD = "trace:size_capped"

# As-published column names (schema observed 2026-07-26). No coined mnemonics.
_TREASURY_MEASURES = (
    "atsInterdealerCount",
    "atsInterdealerVolume",
    "dealerCustomerCount",
    "dealerCustomerVolume",
    "volumeWeightedAveragePrice",
)


def treasury_aggregate_subject(
    *, product_category: str, years_to_maturity: str, benchmark: str
) -> TUID:
    """Stable key for one Treasury aggregate series (replay-stable, I5)."""
    return TUID(f"trace:ust:{product_category}:{years_to_maturity or '-'}:{benchmark or '-'}")


class TraceCredentialsMissingError(Exception):
    """FINRA fixed-income datasets require a (free) API account."""


class TraceApiAdapter(SourceAdapter):
    """FINRA TRACE over the credentialed public API.

    Disabled by default: constructing it without credentials raises, rather
    than silently returning nothing.
    """

    meta = SourceMeta(
        source_id="trace-api",
        description="FINRA TRACE fixed income datasets (credentialed public API)",
        licence="FINRA API terms; redistribution conditions apply per dataset",
        redistribution_restricted=True,
        rate_limit_per_second=2.0,
    )
    parser_version = "0-unimplemented"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        dataset: str,
        client_id: str | None = None,
        client_secret: str | None = None,
    ) -> None:
        super().__init__(payloads, log)
        self._dataset = dataset
        self._client_id = client_id or os.environ.get("FINRA_API_CLIENT_ID")
        self._client_secret = client_secret or os.environ.get("FINRA_API_CLIENT_SECRET")
        if not (self._client_id and self._client_secret):
            raise TraceCredentialsMissingError(
                "FINRA fixed-income datasets returned 401 without credentials "
                "(probed 2026-07-26). Create a free FINRA API account and set "
                "FINRA_API_CLIENT_ID / FINRA_API_CLIENT_SECRET."
            )

    def _access_token(self, client: httpx.Client) -> str:
        response = client.post(
            FINRA_TOKEN_URL,
            params={"grant_type": "client_credentials"},
            auth=(str(self._client_id), str(self._client_secret)),
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise TraceCredentialsMissingError("FINRA token endpoint returned no access_token")
        return str(token)

    def fetch(self) -> Iterator[RawPayload]:
        url = FINRA_DATA_URL.format(group="fixedIncomeMarket", dataset=self._dataset)
        with httpx.Client(timeout=90.0) as client:
            headers = {"Authorization": f"Bearer {self._access_token(client)}"}
            self._throttle()
            response = client.get(url, headers=headers)
            response.raise_for_status()
            yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        """Parse a TRACE aggregate CSV.

        Implemented for ``treasuryDailyAggregates`` (schema observed
        2026-07-26). Other datasets raise rather than being guessed at:
        each needs its own recorded fixture first.
        """
        if self._dataset != TREASURY_DAILY_AGGREGATES:
            raise NotImplementedError(
                f"no recorded fixture for TRACE dataset {self._dataset!r} "
                f"(spec §8.1.1); record one before parsing it"
            )
        text = payload.data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames is None or "tradeDate" not in reader.fieldnames:
            raise ValueError("not a TRACE treasuryDailyAggregates payload")
        provenance = Provenance(
            source_system=self.meta.source_id,
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.API,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        facts: list[Fact] = []
        for row in reader:
            trade_date_raw = (row.get("tradeDate") or "").strip()
            if not trade_date_raw:
                continue
            trade_date = date.fromisoformat(trade_date_raw)
            subject = treasury_aggregate_subject(
                product_category=(row.get("productCategory") or "").strip(),
                years_to_maturity=(row.get("yearsToMaturity") or "").strip(),
                benchmark=(row.get("benchmark") or "").strip(),
            )
            for column in _TREASURY_MEASURES:
                raw = (row.get(column) or "").strip()
                # Blank means the source published no value (e.g. VWAP on
                # bills). Null with provenance — never a substituted zero.
                value = None if raw == "" else float(raw)
                facts.append(
                    Fact(
                        subject=subject,
                        field=f"trace:{column}",
                        value=value,
                        effective_from=trade_date,
                        effective_to=trade_date,
                        # The payload carries no publication timestamp, so
                        # fetch time is the provable knowledge bound (I2).
                        knowledge_from=payload.fetched_at,
                        provenance_id=provenance.id,
                    )
                )
        return ParsedBatch(provenance=(provenance,), facts=tuple(facts))
