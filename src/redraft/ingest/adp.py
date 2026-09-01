"""Yahoo and FFC into `adp` (issue #7).

Two `ADPProvider` implementations, ingest-shaped (ADR-37): each fetches, snapshots,
parses and writes its own rows, and each produces its own snapshot, because a snapshot
is one source's fetch and ADR-30 forbids two sharing one. What they split is
specs/draft-assistant.md Appendix A entry 9: **Yahoo is the location** — the ADP of an
actual Yahoo room — and **FFC is the dispersion shape**, which Yahoo publishes none of.
So a Yahoo row carries `adp` and four NULLs, and an FFC row carries all five.

Both resolve through `_player_index` below rather than through a crosswalk column,
because neither source has a usable one on its own (ADR-46). Yahoo's numeric id covers
143 of its 184 fantasy-position records — nflverse supplies no `yahoo_id` for a 2026
rookie, so Ashton Jeanty at ADP 16.9 is among the missing — and FFC publishes no
crosswalk id at all. An exact `(full_name, position)` match closes all but nine. Exact
means exact: the suffix and punctuation normalization of specs/draft-assistant.md §4.3,
the exception file and the unmatched-player report are issue #8's, and the helper here
is what issue #8 replaces.
"""

from dataclasses import dataclass

import httpx2
from sqlalchemy import Connection, text

from redraft.http.client import Source, fetch_json
from redraft.providers import ffc, yahoo
from redraft.providers.base import IngestResult

SELECT_PLAYERS = text("SELECT player_id, full_name, position, yahoo_num_id FROM players")

INSERT_ADP = text(
    "INSERT INTO adp (snapshot_id, player_id, source, adp, stdev, high, low, times_drafted) "
    "VALUES (:snapshot_id, :player_id, :source, :adp, :stdev, :high, :low, :times_drafted)"
    # No ON CONFLICT: the snapshot is new on every run, so the only way to collide is
    # two source records resolving to one player — which should raise rather than let
    # one silently overwrite the other.
)


class DuplicateResolutionError(Exception):
    """Two records from one source resolved to the same player.

    ADR-46 anticipates this landing on `adp`'s primary key rather than silently
    overwriting, and it would — but as an `IntegrityError` raised by an executemany
    that names neither record, on a transaction whose rollback then takes the snapshot
    with it (ADR-38). The payload that would say which two collided is gone at exactly
    the moment it is wanted. Catching it before the insert costs one dict and turns the
    whole-run failure into a message naming both names.

    It is reachable because resolution has two tiers: one record can match on
    `yahoo_num_id` while a second, sharing its name and position, falls through to the
    name tier and lands on the same player. Neither source contains such a pair today.
    """


class EmptyAdpError(Exception):
    """The fetch succeeded but nothing survived to write.

    The sibling of `projections.EmptyProjectionsError` and `nflverse.EmptyTableError`,
    and the same reflex: an ingester that writes nothing and reports success hands
    issue #9 a green run and hands draft night an empty board. It fires on a source
    that stopped publishing, on a filter that stopped matching, and on a crosswalk that
    resolves nobody.

    It raises on the caller's transaction, so ADR-38's rollback takes the snapshot with
    it and the payload that would say which of those fired is gone. Diagnosing one
    means re-fetching.
    """


@dataclass(frozen=True, slots=True)
class _PlayerIndex:
    """The two lookups every ADP row resolves through, in priority order."""

    by_yahoo_id: dict[int, int]
    by_name: dict[tuple[str, str], int]

    def resolve(self, source_id: int | None, full_name: str, position: str) -> int | None:
        """This record's internal player_id, or None if nothing places it.

        The id is tried first where the source has one, so a name that happens to
        collide can never override a positive identification.
        """
        if source_id is not None:
            player_id = self.by_yahoo_id.get(source_id)
            # Explicitly against None: a surrogate key is an integer, and `or` would
            # fall through on a legitimate zero.
            if player_id is not None:
                return player_id
        return self.by_name.get((full_name, position))


def _player_index(connection: Connection) -> _PlayerIndex:
    """Build both lookups in one pass over `players`.

    A `(full_name, position)` pair naming more than one player is dropped rather than
    resolved arbitrarily. It maps to exactly one across the whole 1,020-player universe
    today, but "pick whichever row came back last" is the kind of silent wrongness that
    specs/draft-assistant.md §4.3 exists to prevent; an ambiguous name is reported as
    unresolved and left to issue #8.
    """
    by_yahoo_id: dict[int, int] = {}
    by_name: dict[tuple[str, str], int] = {}
    ambiguous: set[tuple[str, str]] = set()
    for player_id, full_name, position, yahoo_num_id in connection.execute(SELECT_PLAYERS):
        if yahoo_num_id is not None:
            by_yahoo_id[yahoo_num_id] = player_id
        key = (full_name, position)
        if key in by_name:
            ambiguous.add(key)
        by_name[key] = player_id
    for key in ambiguous:
        del by_name[key]
    return _PlayerIndex(by_yahoo_id, by_name)


class YahooADP:
    """The ADP location: what a Yahoo room actually drafts to (ADR-44)."""

    source: Source = "yahoo"

    def __init__(self, *, client: httpx2.Client, game_key: int) -> None:
        self.client = client
        self.game_key = game_key

    def ingest(self, connection: Connection) -> IngestResult:
        """Fetch, snapshot, resolve and write Yahoo's ADP.

        The caller owns the transaction and the commit (ADR-38), so a parse that raises
        below takes the snapshot with it. Nothing commits ahead of the parse: this
        endpoint needs no authentication and has never been observed to throttle, so a
        re-fetch is cheap. Issue #9 owns revisiting that if 999 ever fires here.
        """
        snapshot_id, payload = fetch_json(
            connection,
            self.source,
            yahoo.players_url(self.game_key),
            client=self.client,
            params=yahoo.players_params(),
        )
        records = yahoo.player_records(payload)
        index = _player_index(connection)

        rows: list[dict[str, object]] = []
        claimed: dict[int, str] = {}
        unresolved = 0
        for record in records:
            # `primary_position` rather than `display_position`: the two agree on every
            # record carrying a numeric ADP, but display_position is comma-joined for a
            # multi-eligibility player ("RB,TE"), which matches no single position and
            # would drop that player before the unresolved counter ever sees him. A
            # silently dropped player is a player missing from the board
            # (specs/draft-assistant.md §4.3), and this is the one skip path that could
            # do it. Eight records are comma-joined today; none carries an ADP.
            position = record["primary_position"]
            # Before resolution, not after: `players` holds QB/RB/WR/TE only, so 42 of
            # these can never resolve and counting them would peg the tripwire at 44 on
            # a healthy run (ADR-46).
            if position not in yahoo.FANTASY_POSITIONS:
                continue
            adp = yahoo.adp_value(record)
            # Not a missing player — a player Yahoo publishes no ADP for (ADR-47).
            if adp is None:
                continue
            name = record["name"]["full"]
            player_id = index.resolve(int(record["player_id"]), name, position)
            if player_id is None:
                unresolved += 1
                continue
            if player_id in claimed:
                raise DuplicateResolutionError(
                    f"{name!r} and {claimed[player_id]!r} both resolved to player_id "
                    f"{player_id}; one of them is matching on a name it does not own"
                )
            claimed[player_id] = name
            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "player_id": player_id,
                    "source": self.source,
                    "adp": adp,
                    # Yahoo publishes no dispersion. FFC exists in this issue for it.
                    "stdev": None,
                    "high": None,
                    "low": None,
                    "times_drafted": None,
                }
            )

        if not rows:
            raise EmptyAdpError(
                f"{len(records)} Yahoo records yielded no ADP rows "
                f"({unresolved} named a player nothing could place)"
            )
        connection.execute(INSERT_ADP, rows)
        return IngestResult(snapshot_id, len(rows), unresolved)


class FfcADP:
    """The dispersion shape, and deliberately not the location
    (specs/draft-assistant.md Appendix A entry 9)."""

    source: Source = "ffc"

    def __init__(self, *, client: httpx2.Client, season: int) -> None:
        self.client = client
        self.season = season

    def ingest(self, connection: Connection) -> IngestResult:
        """Fetch, snapshot, resolve and write FFC's ADP and dispersion.

        `adp` is written even though Yahoo is the location this tool drafts against:
        the two disagreeing is the input to issue #21's disagreement panel, and a
        dispersion detached from the pick it disperses around means nothing.
        """
        snapshot_id, payload = fetch_json(
            connection,
            self.source,
            ffc.ADP_URL,
            client=self.client,
            params=ffc.adp_params(self.season),
        )
        records = ffc.player_records(payload)
        index = _player_index(connection)

        rows: list[dict[str, object]] = []
        claimed: dict[int, str] = {}
        unresolved = 0
        for record in records:
            position = record["position"]
            if position not in ffc.FANTASY_POSITIONS:
                continue
            # FFC publishes no crosswalk id, so there is no id tier to try first.
            # Team is not in the key: FFC writes LAR where nflverse writes LA, which
            # would drop Puka Nacua at ADP 2.9 (ADR-46).
            player_id = index.resolve(None, record["name"], position)
            if player_id is None:
                unresolved += 1
                continue
            if player_id in claimed:
                raise DuplicateResolutionError(
                    f"{record['name']!r} and {claimed[player_id]!r} both resolved to "
                    f"player_id {player_id}; one of them is matching on a name it does "
                    "not own"
                )
            claimed[player_id] = record["name"]
            rows.append(
                {
                    "snapshot_id": snapshot_id,
                    "player_id": player_id,
                    "source": self.source,
                    "adp": record["adp"],
                    "stdev": record["stdev"],
                    "high": record["high"],
                    "low": record["low"],
                    "times_drafted": record["times_drafted"],
                }
            )

        if not rows:
            raise EmptyAdpError(
                f"{len(records)} FFC records yielded no ADP rows "
                f"({unresolved} named a player nothing could place)"
            )
        connection.execute(INSERT_ADP, rows)
        return IngestResult(snapshot_id, len(rows), unresolved)
