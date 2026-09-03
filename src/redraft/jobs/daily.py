"""The daily run: every ingester once, each on its own terms (issue #9).

specs/draft-assistant.md §4 asks for a scheduled daily job plus an on-demand "refresh
now" button, and specs/draft-assistant.md §3 says what the job is *for*: "If Yahoo, the
network, or the tap dies, the draft continues on manual entry with no degradation in
recommendation quality." Those two sentences decide the whole shape. ADR-37 had already
narrowed the work — "#9 shrinks to a loop and a failure policy" — because each ingester
fetches, snapshots, parses and writes its own rows.

So a run is four independent attempts, not one transaction (ADR-53). Each source gets its
own `engine.begin()`, a source that raises is recorded and the loop continues, and the
list of outcomes is both what this module prints and what `POST /refresh` answers with.
One shared transaction would invert the property specs/draft-assistant.md §3 asks for —
FFC raising last would discard Sleeper's and Yahoo's committed work — and would make
"record and continue" unimplementable for the whole DBAPI half of the failure surface,
because a Postgres transaction that has aborted raises `PendingRollbackError` on every
later statement.

nflverse runs first and outside the loop: it writes no snapshot (ADR-40) and it is the
only thing that writes `players`, which the other three resolve every record against.

The unmatched-player report specs/draft-assistant.md §4.3 requires on every run is printed
here rather than by the callers, so the refresh path emits one too — a run that can be
silent is a run the obligation does not reach.
"""

import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import httpx2
from sqlalchemy import Engine

from redraft.db.session import engine
from redraft.http.client import Source
from redraft.identity.report import Unmatched, unmatched_report
from redraft.ingest.adp import FfcADP, YahooADP
from redraft.ingest.players import ingest_players
from redraft.ingest.projections import SleeperProjections
from redraft.providers.base import ADPProvider, ProjectionProvider
from redraft.providers.ffc import ffc_client
from redraft.providers.nflverse import nflverse_client
from redraft.providers.sleeper import sleeper_client
from redraft.providers.yahoo import yahoo_client
from redraft.settings import settings

# One client per source rather than one shared: they are not interchangeable. nflverse's
# sets follow_redirects=True for GitHub's asset 302s (ADR-39, ADR-40), and turning that on
# for Yahoo would follow a redirect this ingester has no reason to trust. A dict rather
# than a dataclass because the only thing done with it is a lookup by source.
Clients = dict[Source, httpx2.Client]


@dataclass(frozen=True, slots=True)
class SourceRun:
    """What one source did on one run — the line the CLI prints and the entry the
    endpoint returns.

    `snapshot_id` is None for nflverse on a *successful* run, because ADR-40 gives it no
    `snapshots` row to name. `failed` is the field that says whether a source worked, and
    reading a null id as a failure would misreport the one source that can never have one.

    `rows_written` carries three units in one field: `projections` stat rows for Sleeper,
    `adp` player rows for Yahoo and FFC, and `players` rows upserted for nflverse.

    The unmatched *records* are deliberately not here. `IngestResult` carries them and
    `unresolved` derives from them (ADR-49) precisely so a count and a report cannot
    disagree; storing both again one layer up would reinstate the drift that entry
    removed, and would put a document global to the run inside a per-source structure.
    """

    source: Source
    snapshot_id: int | None
    rows_written: int
    unresolved: int
    failed: bool
    error: str | None


@contextmanager
def live_clients() -> Iterator[Clients]:
    """The four real clients for one run, closed on the way out.

    Closed because the refresh button of specs/draft-assistant.md §4 is pressed
    repeatedly in the minutes before a draft, and each press would otherwise strand four
    connection pools for the life of the uvicorn process. No timeouts are set here: all
    four factories already set `httpx2.Timeout(30.0)` and each records that its path
    belongs to this job and never to the pick clock of specs/draft-assistant.md §2.2.
    """
    # Built inside the `try` and one at a time, not as a dict literal: a literal binds
    # `clients` only once all four factories have returned, so a raise from the fourth
    # would strand the three already open — the leak this context manager exists to close.
    clients: Clients = {}
    try:
        clients["nflverse"] = nflverse_client()
        clients["sleeper"] = sleeper_client()
        clients["yahoo"] = yahoo_client()
        clients["ffc"] = ffc_client()
        yield clients
    finally:
        for client in clients.values():
            client.close()


def _failed(source: Source, failure: Exception) -> SourceRun:
    """Record a source that raised, and put the traceback where it can be read.

    The class name is fully qualified because three unrelated modules export a
    `PayloadShapeError` and the module is the half that says whose shape gate fired. The
    message is collapsed onto one line because httpx2's `raise_for_status` writes two, and
    four sources have to read as a column. The traceback goes to stderr rather than into
    this string: the catch below is `Exception`, so it will also catch a bug in this
    module, and a class name alone would not say which of `ingest_players`' three CSV
    parses died.
    """
    traceback.print_exc()
    kind = type(failure)
    detail = " ".join(str(failure).split())
    return SourceRun(source, None, 0, 0, True, f"{kind.__module__}.{kind.__qualname__}: {detail}")


def summary_line(outcome: SourceRun) -> str:
    """One source's outcome, fixed-width so four of them read as a column."""
    if outcome.failed:
        return f"  {outcome.source:<9} FAILED  {outcome.error}"
    snapshot = "-" if outcome.snapshot_id is None else str(outcome.snapshot_id)
    return (
        f"  {outcome.source:<9} ok      snapshot {snapshot:<8} "
        f"{outcome.rows_written:6d} rows  unresolved {outcome.unresolved}"
    )


def run(engine: Engine, *, season: int, game_key: int, clients: Clients) -> list[SourceRun]:
    """Run every source once and return what each one did, in the order they ran.

    Takes an `Engine` and not a `Connection`: ADR-53 gives each source its own
    transaction, and a handed-in connection would be exactly the run-wide one that
    decision rejects.

    `clients` has no default. A default of `live_clients()` would let a test that forgot
    the argument fetch Sleeper's 2.9MB body for real, and nothing in this repo's suite
    touches the network.
    """
    runs: list[SourceRun] = []
    unmatched: list[Unmatched] = []

    # nflverse first, and outside the loop below. ADR-40 leaves it a function returning a
    # row count with no snapshot to report, so it has neither the shape nor the result the
    # other three share. First because the three JSON ingesters resolve every record
    # against `players`, and this is the only thing that writes it. Its failure is not
    # fatal: `players` is upserted in place, so a dead nflverse leaves yesterday's pool
    # standing and the other three resolve against it.
    try:
        with engine.begin() as connection:
            upserted = ingest_players(connection, client=clients["nflverse"], season=season)
    # The blanket catch is the decision rather than an oversight: ADR-53 records why an
    # honest exception tuple is not writable here, and why one that goes stale would go
    # stale by aborting the run. `BaseException` is still not caught, so an interrupt
    # stops the process instead of being recorded as four failed sources.
    except Exception as failure:  # noqa: BLE001
        runs.append(_failed("nflverse", failure))
    else:
        runs.append(SourceRun("nflverse", None, upserted, 0, False, None))
    print(summary_line(runs[-1]), flush=True)

    # specs/draft-assistant.md §4.1's own order. Nothing measured distinguishes these
    # three — Yahoo's body is the largest, Sleeper's is the one with a published rate
    # ceiling, FFC publishes once daily — so inventing a draft-night priority would be a
    # claim the record cannot support.
    providers: tuple[ProjectionProvider | ADPProvider, ...] = (
        SleeperProjections(client=clients["sleeper"], season=season),
        YahooADP(client=clients["yahoo"], game_key=game_key),
        FfcADP(client=clients["ffc"], season=season),
    )
    for provider in providers:
        # The `begin()` is inside the `try` on purpose: a COMMIT that fails is that
        # source's failure like any other, and leaving it outside would let it end the
        # run through the back door.
        try:
            with engine.begin() as connection:
                result = provider.ingest(connection)
        except Exception as failure:  # noqa: BLE001 — ADR-53, as above
            runs.append(_failed(provider.source, failure))
        else:
            unmatched.extend(result.unmatched)
            runs.append(
                SourceRun(
                    provider.source,
                    result.snapshot_id,
                    result.rows_written,
                    result.unresolved,
                    False,
                    None,
                )
            )
        print(summary_line(runs[-1]), flush=True)

    # One report across every source that returned, not one per source: `unmatched_report`
    # sorts globally by published rank and tallies per source, which is the reviewable
    # shape specs/draft-assistant.md §4.3 asks for. A source that *raised* contributes
    # nothing — its `Resolver` is a local inside `ingest()` and died with the exception —
    # so the four outcome lines above are printed first and the report is never the run's
    # whole verdict.
    #
    # Guarded because it runs after every source has already committed, and ADR-53 promises
    # a `SourceRun` per source "whatever happened", at 200 either way. `rank` reaches
    # `Unmatched` untyped: FFC checks its `adp` is present but not that it is a number, and
    # Sleeper passes `adp_ppr` straight through, while only the *write* path coerces — which
    # an unmatched record by definition never reaches. A string there raises in the report's
    # sort or its `:6.1f` format, and unguarded that would take four committed snapshot ids
    # down with the printout, minutes before a draft. The failure is printed, not swallowed;
    # what it must not do is destroy the run's result.
    try:
        print(unmatched_report(unmatched), flush=True)
    except Exception:  # noqa: BLE001 — same reasoning as the per-source catches, ADR-53
        traceback.print_exc()
        print("  unmatched players: REPORT FAILED — traceback above", flush=True)
    return runs


def main() -> int:
    """The repo's first `__main__`. `make snapshot` is the only caller.

    Dailiness is a crontab line running that target (ADR-53); nothing here schedules
    anything.
    """
    with live_clients() as clients:
        runs = run(
            engine, season=settings.season, game_key=settings.yahoo_game_key, clients=clients
        )
    return 1 if any(one.failed for one in runs) else 0


if __name__ == "__main__":
    raise SystemExit(main())
