"""Sleeper's component projections into `projections` (issue #6).

The first `ProjectionProvider`, and the first writer of the table that
specs/draft-assistant.md §4.4 marks "components, never points". Nothing here decides
what a component is —
`providers.sleeper` classifies the keys and raises on one it cannot place — so the work
in this module is the part that touches the database: resolve each Sleeper player to an
internal id, and write a row per stat.

Resolution is tier one of specs/draft-assistant.md §4.3 and nothing else. `players.sleeper_id`
is what issue #5 populated from the nflverse crosswalk; the name-matching and exception-file
tiers are issue #8's. A record that does not resolve writes nothing and is counted, because
Sleeper's pool is deliberately wider than any roster and raising would mean this ingester
never completes a run.
"""

import httpx2
from sqlalchemy import Connection, text

from redraft.http.client import Source, fetch_json
from redraft.providers.base import IngestResult
from redraft.providers.sleeper import (
    GAMES_PLAYED,
    PayloadShapeError,
    component_stats,
    projections_params,
    projections_url,
)

# sleeper_id is UNIQUE, so the mapping is injective and two Sleeper records can never
# collide on one player. The NULL filter keeps a player issue #5 left without an id
# from becoming a crosswalk entry keyed on nothing.
SELECT_CROSSWALK = text("SELECT sleeper_id, player_id FROM players WHERE sleeper_id IS NOT NULL")

INSERT_PROJECTION = text(
    "INSERT INTO projections (snapshot_id, player_id, stat_key, value) "
    "VALUES (:snapshot_id, :player_id, :stat_key, :value)"
    # No ON CONFLICT: the snapshot is new on every run, so the only way to collide is a
    # player appearing twice in one payload — which should raise rather than be absorbed.
)


class EmptyProjectionsError(Exception):
    """The fetch succeeded but nothing survived to write.

    The analogue of `nflverse.EmptyTableError`, and the same reflex: an ingester that
    writes nothing and reports success hands issue #9 a green run and hands draft night
    an empty board. It fires on a season whose projections are not yet published, on a
    filter that stops matching, and on a crosswalk that resolves nobody — a total
    narrowing rather than the partial one `unresolved` is for.

    It raises on the caller's transaction, so ADR-38's rollback takes the snapshot with
    it and the payload that would say which of those three fired is gone. Diagnosing one
    means re-fetching. `nflverse.EmptyTableError` has the same property; making it
    otherwise would commit the snapshot ahead of the parse, which ADR-38 leaves to each
    ingester to decide rather than to this exception.
    """


class SleeperProjections:
    """The `ProjectionProvider` of specs/draft-assistant.md §1.1, ingest-shaped (ADR-37)."""

    source: Source = "sleeper"

    def __init__(self, *, client: httpx2.Client, season: int) -> None:
        self.client = client
        self.season = season

    def ingest(self, connection: Connection) -> IngestResult:
        """Fetch, snapshot, resolve and write this source's projections.

        The caller owns the transaction and the commit (ADR-38), so a parse that raises
        below takes the snapshot with it. Nothing commits it ahead of the parse: Sleeper
        is re-fetchable under a non-commercial 1000/min grant, which is the case
        ADR-38 leaves to each ingester and the opposite of Yahoo's.
        """
        snapshot_id, payload = fetch_json(
            connection,
            self.source,
            projections_url(self.season),
            client=self.client,
            params=projections_params(),
        )
        # Heard at the boundary rather than downstream: iterating a dict would walk its
        # keys and read as missing data instead of as a changed shape.
        if not isinstance(payload, list):
            raise PayloadShapeError(
                f"expected an array of projection records, got {type(payload).__name__}"
            )

        crosswalk = dict(connection.execute(SELECT_CROSSWALK).all())
        rows: list[dict[str, object]] = []
        unresolved = 0
        for record in payload:
            components = component_stats(record["stats"])
            if not components.keys() - {GAMES_PLAYED}:
                continue
            player_id = crosswalk.get(record["player_id"])
            if player_id is None:
                unresolved += 1
                continue
            rows.extend(
                {
                    "snapshot_id": snapshot_id,
                    "player_id": player_id,
                    "stat_key": stat_key,
                    "value": value,
                }
                for stat_key, value in components.items()
            )

        if not rows:
            raise EmptyProjectionsError(
                f"{len(payload)} records yielded no projections "
                f"({unresolved} named a player the crosswalk could not place)"
            )
        connection.execute(INSERT_PROJECTION, rows)
        return IngestResult(snapshot_id, len(rows), unresolved)
