"""The three provider seams of specs/draft-assistant.md §1.1.

They are in place from day one so that swapping a licensed source in later is a swap and
not a rewrite. Every source here is used under a non-commercial or personal-use grant,
and the seam is what keeps that boundary in one place.

Nothing is implemented in this module: #5, #6 and #7 supply the ingesters, #24 the pick
feed. The two ingest seams own their own writes; `PickFeed` owns none (ADR-37).
"""

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy import Connection

from redraft.http.client import Source


@dataclass(frozen=True, slots=True)
class IngestResult:
    """What one run of one provider did — the daily job in #9 reports on these."""

    snapshot_id: int
    rows_written: int


class ProjectionProvider(Protocol):
    """Component stats, never anyone's fantasy-point total (specs/draft-assistant.md §4.2)."""

    source: Source

    def ingest(self, connection: Connection) -> IngestResult:
        """Fetch, snapshot, parse and write this source's projections."""
        ...


class ADPProvider(Protocol):
    """Draft position from one source. One snapshot is one source's fetch (ADR-30)."""

    source: Source

    def ingest(self, connection: Connection) -> IngestResult:
        """Fetch, snapshot, parse and write this source's ADP rows."""
        ...


@dataclass(frozen=True, slots=True)
class PickEvent:
    """One pick or one undo, as a feed reports it.

    `pick_no` and `team_id` are the feed's own rather than the board's: a feed reports
    what Yahoo already did, where manual entry decides (ADR-35). Nothing validates them
    against the board's own count here — that reconciliation belongs to #24.

    `source_player_key` is the source's identifier, which for Yahoo is the bare numeric
    id and never the 470.p.{id} form (specs/draft-assistant.md §2.1). It is not a
    `player_id`, because nothing outside `players` is keyed by an external id (ADR-29);
    resolving it is #8's job.
    """

    pick_no: int
    team_id: str
    source_player_key: str
    event_type: Literal["pick", "undo"]


class PickFeed(Protocol):
    """A live source of picks (specs/draft-assistant.md §8.2).

    It writes nothing. #24 reconciles a tapped frame against the board before anything is
    recorded, and keeping the write out of the seam is what leaves room for that.
    """

    source: Literal["tap", "manual"]

    def events(self) -> Iterable[PickEvent]:
        """Yield picks as they arrive, oldest first."""
        ...
