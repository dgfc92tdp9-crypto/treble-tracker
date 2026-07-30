"""SEC Financial Statement Data Sets — bulk XBRL (spec §9.1, §14.1).

One quarterly archive carries every numeric XBRL fact filed by every filer.
The per-CIK ``companyfacts`` API needs one request per company, which is
what left the full 8,017-filer universe unrun; this is the same data in a
single 85 MB download.

Produces facts in exactly the shape ``EdgarCompanyFactsAdapter`` produces —
``taxonomy:Tag:unit`` fields, period-dated, provenance-stamped — so the two
adapters are interchangeable rather than parallel vocabularies.

Three things in this format will produce confidently wrong numbers if taken
at face value, all verified against a real filing rather than assumed:

**1. Over half the rows are dimensional breakdowns.** IBM's 2025 10-K has
721 consolidated-or-segmented rows, of which 370 carry a ``segments`` value:
revenue by business line, by product, by geography. They share the tag
``Revenues`` with the consolidated total. Ingest them together and the
store holds ten conflicting revenues for one date, and a screen picking the
latest would render IBM's revenue as $4.25bn of Infrastructure servers.
Only rows with empty ``segments`` and ``coreg`` are consolidated, and only
those are taken.

**2. ``qtrs`` carries the period, and the start date is not given.** ``0``
is an instant (balance sheet), ``1`` a quarter, ``4`` a year, all ending at
``ddate``. The start is derived (see :func:`_period_start`) and reconciled
against ``companyfacts``, which states it: both now produce 2025-01-01 to
2025-12-31 for the same annual figure, so a fact from either adapter is the
same fact rather than a near-duplicate. It stays approximate for 52/53-week
fiscal years, which no arithmetic on this file can resolve.

**3. ``accepted`` is US Eastern, not UTC.** Reading it as UTC shifts every
knowledge timestamp by four or five hours. It is converted explicitly. In
exchange this archive gives a real acceptance *time* where ``companyfacts``
gives only a filing date, so bitemporal ordering here is finer.
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx

from treble.core.facts import Fact
from treble.core.provenance import ExtractionMethod, Provenance
from treble.ingest.base import ParsedBatch, RawPayload, SourceAdapter, SourceMeta, utcnow
from treble.store.ingest_log import IngestLog
from treble.store.payloads import PayloadHash, PayloadStore

ARCHIVE_URL = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"

#: SEC acceptance timestamps are stamped in the filing agent's local time,
#: which is US Eastern. Nothing in the file says so, which is exactly why
#: it is named here.
SEC_TIMEZONE = ZoneInfo("America/New_York")


def _period_start(ddate: date, qtrs: int) -> date:
    """The first day of a flow period ending at ``ddate``.

    A quarter ending 30 June begins 1 April, and a year ending 31 December
    begins 1 January — the first day of the month ``3 * qtrs - 1`` months
    back, not the same day-of-month. Verified against ``companyfacts``,
    which states the start explicitly: it reports 2008-04-01 to 2008-06-30
    for IBM's Q2, and a naive same-day subtraction gives 2008-03-30.

    Getting this wrong by even a day matters more than it looks. The same
    underlying figure arriving from this archive and from the per-CIK API
    would carry different ``effective_from`` values, so the store would hold
    them as two distinct facts rather than one — and latest-knowledge-wins
    would stop reconciling the two adapters.

    **Limitation, inherent to the format:** the archive gives only ``ddate``
    and ``qtrs``, never the start. This rule is exact for filers on calendar
    month ends and approximate for 52/53-week fiscal years, where a quarter
    may end mid-month. Such a period start can be off by up to a few weeks,
    and no arithmetic on this file can fix it.
    """
    months = ddate.month - 3 * qtrs
    year = ddate.year + (months - 1) // 12
    month = (months - 1) % 12 + 1
    # The month after the one that ended the previous period.
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def _accepted_utc(raw: str) -> datetime | None:
    """Parse an ``accepted`` stamp (``2026-02-24 16:07:00.0``) into UTC."""
    text = raw.strip()
    if not text:
        return None
    try:
        # Naive by necessity: the file states no offset, and a fixed one
        # would be wrong for half the year. ZoneInfo resolves the DST rule
        # for this particular date, which is the whole point.
        naive = datetime.strptime(text.split(".")[0], "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007
    except ValueError:
        return None
    return naive.replace(tzinfo=SEC_TIMEZONE).astimezone(UTC)


def _field_name(tag: str, version: str, unit: str, cik: int) -> str:
    """``taxonomy:Tag:unit``, matching the companyfacts convention.

    A standard tag's ``version`` is ``us-gaap/2025``; a filer's own
    extension tag carries the accession number instead. Extensions are kept
    under the filer's own CIK namespace rather than discarded — CLAUDE.md
    requires unmapped extension tags to be surfaced, never dropped — and the
    namespace makes it obvious the tag is not standard.
    """
    taxonomy = version.split("/")[0] if "/" in version else f"cik{cik:010d}"
    return f"{taxonomy}:{tag}:{unit}"


class EdgarBulkFinancialsAdapter(SourceAdapter):
    """Every filer's numeric XBRL for a quarter, from one archive."""

    meta = SourceMeta(
        source_id="edgar-bulk",
        description="SEC Financial Statement Data Sets (quarterly bulk XBRL)",
        licence="US federal government work; public domain. SEC fair-access "
        "policy requires a declared User-Agent.",
        redistribution_restricted=False,
        rate_limit_per_second=10.0,
    )
    parser_version = "1.0"

    def __init__(
        self,
        payloads: PayloadStore,
        log: IngestLog,
        *,
        quarters: tuple[str, ...],
        contact_email: str,
        ciks: frozenset[int] | None = None,
    ) -> None:
        super().__init__(payloads, log)
        self._quarters = quarters
        self._contact_email = contact_email
        #: None means every filer in the archive. A subset keeps the dev
        #: universe cheap without changing what is parsed for the full run.
        self._ciks = ciks

    def fetch(self) -> Iterator[RawPayload]:
        from treble.ingest.edgar import edgar_user_agent

        for quarter in self._quarters:
            self._throttle()
            url = ARCHIVE_URL.format(quarter=quarter)
            response = httpx.get(
                url,
                headers={"User-Agent": edgar_user_agent(self._contact_email)},
                timeout=600.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            yield RawPayload(data=response.content, source_uri=url, fetched_at=utcnow())

    def parse(self, payload: RawPayload, payload_hash: PayloadHash) -> ParsedBatch:
        provenance = Provenance(
            source_system="edgar",
            source_uri=payload.source_uri,
            retrieved_at=payload.fetched_at,
            method=ExtractionMethod.BULK_FILE,
            extractor_version=self.parser_version,
            payload_hash=payload_hash,
        )
        with zipfile.ZipFile(io.BytesIO(payload.data)) as archive:
            submissions = self._read_submissions(archive)
            facts = tuple(self._read_numbers(archive, submissions, provenance.id))
        return ParsedBatch(provenance=(provenance,), facts=facts)

    def _read_submissions(self, archive: zipfile.ZipFile) -> dict[str, tuple[int, datetime]]:
        """Map accession -> (cik, acceptance time in UTC).

        The acceptance time is the knowledge date (I2): the moment the filing
        became public, not the period it describes.
        """
        submissions: dict[str, tuple[int, datetime]] = {}
        with archive.open("sub.txt") as handle:
            for row in csv.DictReader(
                io.TextIOWrapper(handle, "utf-8", errors="replace"), delimiter="\t"
            ):
                try:
                    cik = int(row["cik"])
                except (TypeError, ValueError):
                    continue
                if self._ciks is not None and cik not in self._ciks:
                    continue
                accepted = _accepted_utc(row.get("accepted", ""))
                if accepted is None:
                    # Without an acceptance time there is no defensible
                    # knowledge date, and inventing one would corrupt every
                    # point-in-time query against this filing.
                    continue
                submissions[row["adsh"]] = (cik, accepted)
        return submissions

    def _read_numbers(
        self,
        archive: zipfile.ZipFile,
        submissions: dict[str, tuple[int, datetime]],
        provenance_id: str,
    ) -> Iterator[Fact]:
        """Stream num.txt, yielding consolidated facts only.

        Streamed rather than loaded: num.txt is 559 MB uncompressed for a
        single quarter, and this runs over every quarter in a backfill.
        """
        with archive.open("num.txt") as handle:
            for row in csv.DictReader(
                io.TextIOWrapper(handle, "utf-8", errors="replace"), delimiter="\t"
            ):
                entry = submissions.get(row["adsh"])
                if entry is None:
                    continue
                # The filter that matters: a non-empty segments or coreg is a
                # dimensional or co-registrant breakdown sharing its tag with
                # the consolidated total.
                if row.get("segments") or row.get("coreg"):
                    continue
                value_text = row.get("value", "")
                if not value_text:
                    continue
                try:
                    value = float(value_text)
                    stamp = row["ddate"]
                    ddate = date(int(stamp[:4]), int(stamp[4:6]), int(stamp[6:8]))
                    qtrs = int(row["qtrs"])
                except (TypeError, ValueError):
                    continue

                cik, accepted = entry
                yield Fact(
                    subject=f"cik:{cik:010d}",
                    field=_field_name(row["tag"], row["version"], row["uom"], cik),
                    value=value,
                    # Instants start and end on ddate; flows are stepped back
                    # by qtrs, which the archive does not state directly.
                    effective_from=ddate if qtrs == 0 else _period_start(ddate, qtrs),
                    effective_to=ddate,
                    knowledge_from=accepted,
                    provenance_id=provenance_id,
                )
