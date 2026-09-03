"""The "refresh now" button of specs/draft-assistant.md §4, pressed before the draft.

The same run the daily job takes, on demand, answering with what each source did. There
is no separate code path: `jobs.daily.run` is the whole implementation, so the scheduled
pull and the manual one cannot diverge.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine

from redraft.db import session
from redraft.jobs.daily import Clients, SourceRun, live_clients, run
from redraft.settings import settings

router = APIRouter(tags=["refresh"])


def get_engine() -> Engine:
    """The engine, not a connection.

    `redraft.api.picks.get_connection` yields one request-scoped transaction, which is
    right for a pick and wrong here: ADR-53 gives each source its own, so the run has to
    open them itself. This exists as its own dependency so a test can point the run at a
    throwaway database by overriding one object — overriding `picks.get_connection`
    instead would silently do nothing and the run would write to the real database.
    """
    return session.engine


def get_clients() -> Iterator[Clients]:
    """The four live clients for one refresh, closed when the request ends."""
    with live_clients() as clients:
        yield clients


EngineDep = Annotated[Engine, Depends(get_engine)]
ClientsDep = Annotated[Clients, Depends(get_clients)]


@router.post("/refresh")
def refresh_now(engine: EngineDep, clients: ClientsDep) -> list[SourceRun]:
    """Run every source now and answer with what each one did.

    A plain `def`, so Starlette runs it in the threadpool. `fetch_json` blocks in
    `time.sleep` and in synchronous httpx2 for up to six minutes per source under a
    sustained throttle, and ADR-38 records what the same call from an `async def` would
    do: stall the event loop, taking `/health` and every manual pick down with it — the
    failure specs/draft-assistant.md §3's offline-first paragraph forbids.

    200 even when a source failed. The body already says, per source, what happened; a
    500 would discard a payload carrying two good snapshot ids, and no status code means
    "three of four".

    Nothing serialises two presses. Two runs write two snapshots per source, `adp`'s key
    is (snapshot_id, player_id) so they cannot collide, and `players` is an idempotent
    upsert — one operator, one machine.
    """
    return run(engine, season=settings.season, game_key=settings.yahoo_game_key, clients=clients)
