"""Yahoo and FFC into `adp` (issue #7).

Two `ADPProvider` implementations, ingest-shaped (ADR-37): each fetches, snapshots,
parses and writes its own rows, and each produces its own snapshot, because a snapshot
is one source's fetch and ADR-30 forbids two sharing one. What they split is
specs/draft-assistant.md Appendix A entry 9: **Yahoo is the location** — the ADP of an
actual Yahoo room — and **FFC is the dispersion shape**, which Yahoo publishes none of.
So a Yahoo row carries `adp` and four NULLs, and an FFC row carries all five.

Both resolve through `redraft.identity` (ADR-49), which replaced the exact-match helper
this module carried for issue #7 — ADR-46 said in as many words that issue #8 would.
Neither source has a usable crosswalk column on its own: Yahoo's numeric id covers 143 of
its 184 fantasy-position records, nflverse supplying none for a 2026 rookie, so Ashton
Jeanty at ADP 16.9 was among the missing, and FFC publishes no crosswalk id at all. The
name tiers close the nine the exact match left, all of them the suffix and punctuation
variants specs/draft-assistant.md §4.3 names, and take FFC to zero. Yahoo keeps one:
Tyreek Hill, drafted at 129.2 and absent from `players` entirely, whom no tier can place
and issue #43 is about.
"""

import httpx2
from sqlalchemy import Connection, text

from redraft.http.client import Source, fetch_json
from redraft.identity.resolve import Resolver
from redraft.providers import ffc, yahoo
from redraft.providers.base import IngestResult

INSERT_ADP = text(
    "INSERT INTO adp (snapshot_id, player_id, source, adp, stdev, high, low, times_drafted) "
    "VALUES (:snapshot_id, :player_id, :source, :adp, :stdev, :high, :low, :times_drafted)"
    # No ON CONFLICT: the snapshot is new on every run, so the only way to collide is
    # two source records resolving to one player — which should raise rather than let
    # one silently overwrite the other.
)


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
        # "raise" rather than "report": `adp`'s primary key would catch a collision
        # anyway, but as an IntegrityError naming neither record on a transaction whose
        # rollback takes the snapshot with it (ADR-46).
        resolver = Resolver(connection, self.source, on_duplicate="raise")

        # Keyed by player_id, not appended: a record that outranks an earlier one takes
        # the player (ADR-51), and writing it here discards the row the displaced record
        # left behind. A list would keep both and violate `adp`'s primary key.
        rows: dict[int, dict[str, object]] = {}
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
            # The bare numeric id as text (specs/draft-assistant.md §2.1), never the
            # 470.p.{id} form. `yahoo_num_id` is an Integer column and this arrives as a
            # string, so the resolver compares both as text.
            player_id = resolver.resolve(record["player_id"], name, position, rank=adp)
            if player_id is None:
                continue
            rows[player_id] = {
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

        # Dropped before the emptiness check, not after: a player two records reached on
        # one tier belongs to neither (ADR-52), and `resolve` had already handed him to
        # the first of them by the time the second arrived.
        for withdrawn in resolver.withdrawn:
            rows.pop(withdrawn, None)

        if not rows:
            raise EmptyAdpError(
                f"{len(records)} Yahoo records yielded no ADP rows "
                f"({len(resolver.unmatched)} named a player nothing could place)"
            )
        connection.execute(INSERT_ADP, list(rows.values()))
        return IngestResult(snapshot_id, len(rows), resolver.unmatched)


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
        resolver = Resolver(connection, self.source, on_duplicate="raise")

        # Keyed by player_id, not appended: a record that outranks an earlier one takes
        # the player (ADR-51), and writing it here discards the row the displaced record
        # left behind. A list would keep both and violate `adp`'s primary key.
        rows: dict[int, dict[str, object]] = {}
        for record in records:
            position = record["position"]
            if position not in ffc.FANTASY_POSITIONS:
                continue
            # FFC publishes no crosswalk id, so it has no id tier — the name it prints
            # is its key, which is also what an exception entry is written against
            # (ADR-50). Team is not in the key: FFC writes LAR where nflverse writes LA,
            # which would drop Puka Nacua at ADP 2.9 (ADR-46).
            player_id = resolver.resolve(
                record["name"], record["name"], position, rank=record["adp"]
            )
            if player_id is None:
                continue
            rows[player_id] = {
                "snapshot_id": snapshot_id,
                "player_id": player_id,
                "source": self.source,
                "adp": record["adp"],
                "stdev": record["stdev"],
                "high": record["high"],
                "low": record["low"],
                "times_drafted": record["times_drafted"],
            }

        for withdrawn in resolver.withdrawn:
            rows.pop(withdrawn, None)

        if not rows:
            raise EmptyAdpError(
                f"{len(records)} FFC records yielded no ADP rows "
                f"({len(resolver.unmatched)} named a player nothing could place)"
            )
        connection.execute(INSERT_ADP, list(rows.values()))
        return IngestResult(snapshot_id, len(rows), resolver.unmatched)
