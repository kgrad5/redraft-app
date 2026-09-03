"""The daily run and the refresh button (issue #9), end to end against a migrated database.

This module carries issue #9's verification check: two runs on one day leave two snapshots
per JSON source and overwrite nothing, and `POST /refresh` answers with the ids a
newest-snapshot-per-source query finds a moment later.

The fixtures are a four-team mini-league in the shape `tests/test_nflverse_ingest.py`
proved, extended with a Sleeper, a Yahoo and an FFC payload. Two records are deliberately
unplaceable — Tyreek Hill in Sleeper's payload and Oronde Gadsden in Yahoo's — so the
unmatched-player report of specs/draft-assistant.md §4.3 has two names under two sources to
tally, which is what makes "one report across every source" observable rather than assumed.

The module does **not** use the shared `player_pool` fixture. Its rows carry no crosswalk
ids and a NULL `nflverse_id`, so they would resolve nobody *and* `ingest_players` would
insert a disjoint second universe beside them. `players` is populated by the job's own
nflverse step, which is what makes ADR-53's ordering decision observable rather than
asserted in prose.

No live network anywhere: every fetch goes through httpx2.MockTransport, and nothing
sleeps.
"""

import collections
import contextlib
import inspect

import httpx2
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from redraft.jobs import daily
from redraft.jobs.daily import run
from redraft.providers.nflverse import PLAYERS_URL, SCHEDULE_URL, roster_url
from redraft.providers.sleeper import projections_url
from redraft.settings import settings

# Derived rather than pinned. `ingest_players` reads the season out of the CSV *body* as
# well as the URL — `bye_weeks` filters `g["season"]` and the free-agent tail filters
# `row["last_season"]` — so the fixtures below are f-strings on it. A developer whose .env
# says SEASON=2027 still passes, and the endpoint reads `settings.season` from a module
# global no dependency override can reach.
SEASON = settings.season
GAME_KEY = settings.yahoo_game_key

# The six fields ADR-53 fixes as the wire format. Asserted as a set so a field added
# without a decision fails here rather than reaching issue #19 as a contract.
WIRE_FIELDS = {"source", "snapshot_id", "rows_written", "unresolved", "failed", "error"}

# --- nflverse -------------------------------------------------------------------------
#
# REG weeks 1-3 over four teams: each plays twice and sits once, so every bye is
# derivable. KC and BUF sit week 3, DET and PHI sit week 2.
GAMES_CSV = f"""\
season,game_type,week,away_team,home_team
{SEASON},REG,1,KC,DET
{SEASON},REG,1,BUF,PHI
{SEASON},REG,2,KC,BUF
{SEASON},REG,3,DET,PHI
"""

# Goedert's blank sleeper_id is the hole ADR-49's name tiers cover, and the reason FFC
# places him on a name where Sleeper never sees him at all.
ROSTER_CSV = """\
team,position,status,full_name,gsis_id,sleeper_id,yahoo_id,week
KC,QB,ACT,Patrick Mahomes,00-0033873,4046,30123,1
DET,WR,ACT,Amon-Ra St. Brown,00-0036973,7547,31688,1
PHI,TE,ACT,Dallas Goedert,00-0034351,,31019,1
BUF,RB,ACT,James Cook,00-0037248,8138,33555,1
"""

# The free-agent tail: one player no roster row mentions. A header-only table would raise
# EmptyTableError instead, which is issue #5's test rather than this one's.
PLAYERS_CSV = f"""\
gsis_id,display_name,position,last_season
00-0035676,Brandon Aiyuk,WR,{SEASON}
"""

PLAYER_COUNT = 5  # four rostered, one free agent

# --- Sleeper --------------------------------------------------------------------------
#
# Hill carries real components and no `players` row, so he is the one record no tier can
# place — the same shape as the live pool, where Sleeper's is deliberately wider than any
# roster. `gp` is a component but never the only one: a record whose components are `gp`
# alone is an ADP shell and is skipped before it can reach the report.


def sleeper_record(player_id, first, last, position, stats):
    return {
        "player_id": player_id,
        "player": {"first_name": first, "last_name": last, "position": position},
        "stats": stats,
    }


SLEEPER_PLACEABLE = [
    sleeper_record(
        "4046",
        "Patrick",
        "Mahomes",
        "QB",
        {"pass_yd": 4500.0, "pass_td": 35.0, "gp": 17.0, "pts_ppr": 300.0, "adp_ppr": 45.0},
    ),
    sleeper_record(
        "7547",
        "Amon-Ra",
        "St. Brown",
        "WR",
        {"rec": 100.0, "rec_yd": 1200.0, "gp": 17.0, "pts_ppr": 250.0, "adp_ppr": 20.0},
    ),
]
SLEEPER_UNPLACEABLE = sleeper_record(
    "3321",
    "Tyreek",
    "Hill",
    "WR",
    {"rec": 90.0, "rec_yd": 1100.0, "gp": 16.0, "pts_ppr": 230.0, "adp_ppr": 30.0},
)
SLEEPER_PAYLOAD = [*SLEEPER_PLACEABLE, SLEEPER_UNPLACEABLE]
# Three stat keys survive per placeable record: two components plus `gp`.
SLEEPER_ROWS = 3 * len(SLEEPER_PLACEABLE)

# --- Yahoo ----------------------------------------------------------------------------


def yahoo_record(player_id, full, position, adp):
    return {
        "player_key": f"{GAME_KEY}.p.{player_id}",
        "player_id": player_id,
        "name": {"full": full},
        "primary_position": position,
        "draft_analysis": {"preseason_average_pick": adp},
    }


YAHOO_PLACEABLE = [
    yahoo_record("30123", "Patrick Mahomes", "QB", "45.1"),
    yahoo_record("31688", "Amon-Ra St. Brown", "WR", "20.3"),
]
YAHOO_UNPLACEABLE = yahoo_record("100002", "Oronde Gadsden", "TE", "150.0")
YAHOO_RECORDS = [*YAHOO_PLACEABLE, YAHOO_UNPLACEABLE]
YAHOO_ROWS = len(YAHOO_PLACEABLE)


def yahoo_payload(records=None):
    """The json_f envelope: keyed objects all the way down, never a positional array."""
    chosen = YAHOO_RECORDS if records is None else records
    return {
        "fantasy_content": {
            "league": {"players": [{"player": record} for record in chosen]},
        }
    }


# --- FFC ------------------------------------------------------------------------------
#
# FFC publishes no crosswalk id, so every row resolves on the name it prints. All three
# are placeable, which is what leaves the report naming exactly two sources.


def ffc_record(name, position, adp, stdev, high, low, times_drafted):
    return {
        "name": name,
        "position": position,
        "adp": adp,
        "stdev": stdev,
        "high": high,
        "low": low,
        "times_drafted": times_drafted,
    }


FFC_PLACEABLE = [
    ffc_record("Patrick Mahomes", "QB", 46.2, 12.1, 20, 80, 1400),
    ffc_record("Amon-Ra St. Brown", "WR", 19.8, 6.4, 8, 35, 1520),
    ffc_record("Dallas Goedert", "TE", 98.5, 18.0, 60, 140, 640),
]
FFC_UNPLACEABLE = [
    ffc_record("Nobody At All", "WR", 120.0, 20.0, 80, 160, 300),
    ffc_record("Also Nobody", "RB", 140.0, 22.0, 95, 180, 210),
]
FFC_ROWS = len(FFC_PLACEABLE)


def ffc_payload(records=None):
    chosen = FFC_PLACEABLE if records is None else records
    return {"status": "Success", "meta": {"type": "ppr"}, "players": chosen}


# --- clients --------------------------------------------------------------------------


def csv_client(roster=ROSTER_CSV, players=PLAYERS_CSV, games=GAMES_CSV) -> httpx2.Client:
    """A client whose transport serves the three fixture tables; None means 404.

    A dict lookup with no consumed state, so one instance serves two full runs. The
    popping handler of `tests/test_http_layer.py` would IndexError on run two, which is
    exactly the run this module's acceptance check exists to make.
    """
    mapping = {roster_url(SEASON): roster, PLAYERS_URL: players, SCHEDULE_URL: games}

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = mapping.get(str(request.url))
        if body is None:
            return httpx2.Response(404, text="Not Found")
        return httpx2.Response(200, text=body)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def json_client(payload, status: int = 200) -> tuple[httpx2.Client, list[str]]:
    """A client serving `payload` for any URL, and the list of URLs it was asked for.

    The URL list is what makes the request assertable, the recorder idiom of
    `tests/test_adp_ingest.py`. `status` is how a dead source is simulated: 500 raises in
    `raise_for_status` before the snapshot INSERT, which is ADR-36's rule and what test
    four reads back out of `snapshots`.
    """
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        if status != 200:
            return httpx2.Response(status, text="upstream is having a day")
        return httpx2.Response(200, json=payload)

    return httpx2.Client(transport=httpx2.MockTransport(handler)), requested


def mock_clients(
    *,
    sleeper=None,
    yahoo=None,
    ffc=None,
    sleeper_status=200,
    yahoo_status=200,
    ffc_status=200,
    roster=ROSTER_CSV,
) -> tuple[dict, dict]:
    """The four-key `Clients` dict, and `{source: the URLs it was asked for}`."""
    sleeper_client, sleeper_urls = json_client(
        SLEEPER_PAYLOAD if sleeper is None else sleeper, sleeper_status
    )
    yahoo_client, yahoo_urls = json_client(
        yahoo_payload() if yahoo is None else yahoo, yahoo_status
    )
    ffc_client, ffc_urls = json_client(ffc_payload() if ffc is None else ffc, ffc_status)
    clients = {
        "nflverse": csv_client(roster=roster),
        "sleeper": sleeper_client,
        "yahoo": yahoo_client,
        "ffc": ffc_client,
    }
    return clients, {"sleeper": sleeper_urls, "yahoo": yahoo_urls, "ffc": ffc_urls}


# --- reading the database back --------------------------------------------------------


def snapshot_rows(engine) -> list[tuple]:
    """Every snapshot as a hashable tuple, read on a fresh connection.

    `md5(raw_payload::text)` rather than the payload: psycopg returns JSONB as a `dict`,
    and a tuple containing one cannot go in a set — which is how "zero overwritten" is
    asserted. The digest also makes the assertion stronger than a count, because a row
    whose payload changed in place would still be counted and would not still match.
    """
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT snapshot_id, source, fetched_at, md5(raw_payload::text) AS digest "
                "FROM snapshots ORDER BY snapshot_id"
            )
        ).all()


def newest_snapshot_ids(engine) -> dict[str, int]:
    """The newest snapshot per source — the query issue #9's check names.

    One spelling only: Postgres rejects a DISTINCT ON whose expressions do not lead the
    ORDER BY.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT DISTINCT ON (source) source, snapshot_id FROM snapshots "
                "ORDER BY source, snapshot_id DESC"
            )
        ).all()
    return {source: snapshot_id for source, snapshot_id in rows}


def dependent_counts(engine) -> dict[int, int]:
    """Rows hanging off each snapshot, so a run that emptied one is not read as a pass."""
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT snapshot_id, count(*) FROM ("
                "  SELECT snapshot_id FROM projections UNION ALL"
                "  SELECT snapshot_id FROM adp"
                ") t GROUP BY snapshot_id"
            )
        ).all()
    return {snapshot_id: count for snapshot_id, count in rows}


def player_rows(engine) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT player_id, full_name, team, position, bye_week, nflverse_id, "
                "sleeper_id, yahoo_num_id FROM players ORDER BY player_id"
            )
        ).all()


def by_source(runs) -> dict:
    return {one.source: one for one in runs}


@contextlib.contextmanager
def api(engine, clients):
    """A TestClient whose refresh route runs against `engine` with `clients`.

    Overriding `redraft.api.picks.get_connection` instead would silently do nothing and
    the run would write to the developer's real database — the reason ADR-53 records that
    a test must override the exact function object its own router depends on.
    """
    from redraft.api.refresh import get_clients, get_engine
    from redraft.main import app

    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_clients] = lambda: clients
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def clean(migrated, engine):
    """The module shares one database and the job commits, so every test starts empty.

    DELETE and in this order: `adp` and `projections` cascade from `snapshots` but
    RESTRICT to `players`, so a bare TRUNCATE of either parent errors outright.
    """
    with engine.begin() as conn:
        for table in ("adp", "projections", "snapshots", "players"):
            conn.execute(sa.text(f"DELETE FROM {table}"))


# --- the run --------------------------------------------------------------------------


def test_a_run_writes_one_snapshot_per_json_source_and_none_for_nflverse(engine):
    """ADR-40: nflverse upserts `players` in place and writes no snapshot at all."""
    clients, _ = mock_clients()
    run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    counts = collections.Counter(row.source for row in snapshot_rows(engine))
    assert counts == {"sleeper": 1, "yahoo": 1, "ffc": 1}
    assert "nflverse" not in counts
    assert len(player_rows(engine)) == PLAYER_COUNT


def test_every_source_reports_its_own_outcome(engine):
    """The number reported is the number written, for all four sources."""
    clients, _ = mock_clients()
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    assert [one.source for one in runs] == ["nflverse", "sleeper", "yahoo", "ffc"]
    assert not any(one.failed for one in runs)
    assert all(one.error is None for one in runs)

    outcomes = by_source(runs)
    # nflverse has no snapshot to name, and `failed` is what says whether it worked.
    assert outcomes["nflverse"].snapshot_id is None
    assert outcomes["nflverse"].rows_written == PLAYER_COUNT

    written = dependent_counts(engine)
    sources = {row.snapshot_id: row.source for row in snapshot_rows(engine)}
    for source, expected in (("sleeper", SLEEPER_ROWS), ("yahoo", YAHOO_ROWS), ("ffc", FFC_ROWS)):
        one = outcomes[source]
        assert sources[one.snapshot_id] == source
        assert one.rows_written == expected == written[one.snapshot_id]


def test_two_runs_on_one_day_add_rows_and_change_nothing(engine):
    """Issue #9's acceptance check, at the level it is written: rows, not a board."""
    clients, _ = mock_clients()

    first = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)
    before = snapshot_rows(engine)
    dependents_before = dependent_counts(engine)
    players_before = player_rows(engine)

    second = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)
    after = snapshot_rows(engine)

    # Two distinct rows per JSON source, and still nothing for nflverse.
    counts = collections.Counter(row.source for row in after)
    assert counts == {"sleeper": 2, "yahoo": 2, "ffc": 2}
    assert "nflverse" not in counts

    for source in ("sleeper", "yahoo", "ffc"):
        rows = [row for row in after if row.source == source]
        assert len({row.snapshot_id for row in rows}) == 2
        # clock_timestamp() advances inside a transaction as well as across one.
        assert len({row.fetched_at for row in rows}) == 2

    # Zero overwritten: every row run one wrote is still present and byte-identical,
    # not merely still counted.
    assert set(before) <= set(after)
    assert len(after) == len(before) * 2

    # ...and so are the rows hanging off run one's snapshots.
    assert {
        snapshot_id: count
        for snapshot_id, count in dependent_counts(engine).items()
        if snapshot_id in dependents_before
    } == dependents_before

    # The ids reported are the ids written, and the two runs' ids are disjoint.
    reported = [one.snapshot_id for one in first + second if one.snapshot_id is not None]
    assert sorted(reported) == sorted(row.snapshot_id for row in after)
    first_ids = {one.snapshot_id for one in first if one.snapshot_id is not None}
    second_ids = {one.snapshot_id for one in second if one.snapshot_id is not None}
    assert first_ids.isdisjoint(second_ids)

    # `players` is ADR-40's documented exception: upserted in place, same surrogate ids.
    assert player_rows(engine) == players_before


def test_a_failing_source_is_recorded_and_the_others_still_commit(engine):
    """One dead source must not deny the others (specs/draft-assistant.md §3)."""
    clients, _ = mock_clients(yahoo_status=500)
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    outcomes = by_source(runs)
    assert outcomes["yahoo"].failed is True
    assert outcomes["yahoo"].snapshot_id is None
    assert outcomes["yahoo"].rows_written == 0
    assert "HTTPStatusError" in outcomes["yahoo"].error

    # ADR-36: raise_for_status fires ahead of the INSERT, so a 500 leaves no row at all.
    counts = collections.Counter(row.source for row in snapshot_rows(engine))
    assert counts == {"sleeper": 1, "ffc": 1}

    # Read back on a fresh connection: they committed rather than merely returned.
    assert not outcomes["sleeper"].failed
    assert not outcomes["ffc"].failed
    assert dependent_counts(engine)[outcomes["sleeper"].snapshot_id] == SLEEPER_ROWS
    assert dependent_counts(engine)[outcomes["ffc"].snapshot_id] == FFC_ROWS


def test_a_failed_parse_rolls_back_only_its_own_snapshot(engine):
    """ADR-53's transaction boundary, made observable.

    FFC serves a well-formed payload naming nobody, so `EmptyAdpError` raises *after*
    `fetch_json` inserted the snapshot. Under one run-wide transaction Sleeper's and
    Yahoo's committed work would have gone with it.
    """
    clients, _ = mock_clients(ffc=ffc_payload(FFC_UNPLACEABLE))
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    outcomes = by_source(runs)
    assert outcomes["ffc"].failed is True
    assert outcomes["ffc"].error.startswith("redraft.ingest.adp.EmptyAdpError")

    counts = collections.Counter(row.source for row in snapshot_rows(engine))
    assert counts == {"sleeper": 1, "yahoo": 1}
    assert dependent_counts(engine)[outcomes["sleeper"].snapshot_id] == SLEEPER_ROWS
    assert dependent_counts(engine)[outcomes["yahoo"].snapshot_id] == YAHOO_ROWS


def test_nflverse_failure_does_not_stop_the_json_sources_on_a_populated_pool(engine):
    """ADR-40 upserts in place, so a dead nflverse leaves yesterday's pool standing."""
    clients, _ = mock_clients()
    run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)
    players_before = player_rows(engine)

    dead, _ = mock_clients(roster=None)
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=dead)

    outcomes = by_source(runs)
    assert outcomes["nflverse"].failed is True
    assert "HTTPStatusError" in outcomes["nflverse"].error
    assert not any(outcomes[source].failed for source in ("sleeper", "yahoo", "ffc"))

    counts = collections.Counter(row.source for row in snapshot_rows(engine))
    assert counts == {"sleeper": 2, "yahoo": 2, "ffc": 2}
    # Its own transaction rolled back without leaving a partial universe behind.
    assert player_rows(engine) == players_before


def test_a_virgin_pool_costs_every_json_source_its_run(engine, capsys):
    """The recorded cost of running nflverse first and not aborting on its failure.

    An empty `players` resolves nobody, so all three JSON sources raise after paying for
    a fetch and a snapshot INSERT that then rolls back. It happens once, on a database
    nobody has ingested into yet.
    """
    clients, _ = mock_clients(roster=None)
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    assert all(one.failed for one in runs)
    outcomes = by_source(runs)
    assert outcomes["sleeper"].error.startswith("redraft.ingest.projections.EmptyProjectionsError")
    assert outcomes["yahoo"].error.startswith("redraft.ingest.adp.EmptyAdpError")
    assert outcomes["ffc"].error.startswith("redraft.ingest.adp.EmptyAdpError")

    assert snapshot_rows(engine) == []
    assert player_rows(engine) == []

    # The pairing ADR-53 records: the report is never the run's whole verdict, so a run
    # that placed nobody can never be read from the report alone.
    printed = capsys.readouterr().out
    assert printed.count("FAILED") == 4
    assert "unmatched players: none" in printed


def test_the_run_emits_one_unmatched_report_across_every_source(engine, capsys):
    """specs/draft-assistant.md §4.3 wants one short report a person reads, not four."""
    clients, _ = mock_clients()
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    outcomes = by_source(runs)
    assert outcomes["sleeper"].unresolved == 1
    assert outcomes["yahoo"].unresolved == 1
    assert outcomes["ffc"].unresolved == 0
    assert outcomes["nflverse"].unresolved == 0

    printed = capsys.readouterr().out
    assert printed.count("unmatched players:") == 1
    header = next(line for line in printed.splitlines() if "unmatched players:" in line)
    assert "sleeper 1" in header
    assert "yahoo 1" in header
    assert printed.count("Tyreek Hill") == 1
    assert printed.count("Oronde Gadsden") == 1


def test_a_clean_run_still_says_unmatched_players_none(engine, capsys):
    """A report that prints nothing is indistinguishable from a job that never ran."""
    clients, _ = mock_clients(sleeper=SLEEPER_PLACEABLE, yahoo=yahoo_payload(YAHOO_PLACEABLE))
    runs = run(engine, season=SEASON, game_key=GAME_KEY, clients=clients)

    assert not any(one.failed for one in runs)
    assert all(one.unresolved == 0 for one in runs)
    assert "unmatched players: none" in capsys.readouterr().out


def test_main_exits_zero_on_a_clean_run_and_one_when_any_source_failed(engine, monkeypatch):
    """The CLI path and its exit contract, covered rather than asserted in prose."""
    monkeypatch.setattr(daily, "engine", engine)

    def patched(clients):
        @contextlib.contextmanager
        def factory():
            yield clients

        monkeypatch.setattr(daily, "live_clients", factory)

    patched(mock_clients()[0])
    assert daily.main() == 0

    patched(mock_clients(yahoo_status=500)[0])
    assert daily.main() == 1


# --- the endpoint ---------------------------------------------------------------------


def test_refresh_returns_one_entry_per_source_with_its_new_snapshot_id(engine):
    """The second half of issue #9's acceptance check."""
    clients, _ = mock_clients()
    with api(engine, clients) as client:
        response = client.post("/refresh")

    assert response.status_code == 200, response.text
    body = response.json()

    assert [entry["source"] for entry in body] == ["nflverse", "sleeper", "yahoo", "ffc"]
    assert all(entry["failed"] is False for entry in body)
    assert all(set(entry) == WIRE_FIELDS for entry in body)
    # ADR-40 is the one place "one entry per provider" and "newest snapshot per source"
    # stop being 1:1, and `failed` is what says nflverse worked.
    assert body[0]["snapshot_id"] is None

    returned = {
        entry["source"]: entry["snapshot_id"] for entry in body if entry["snapshot_id"] is not None
    }
    # That this query can see them at all is what proves each source's transaction
    # committed before the response was built.
    assert newest_snapshot_ids(engine) == returned


def test_refresh_reports_a_failed_source_as_data_rather_than_a_500(engine):
    """No status code means "three of four", and a 500 would discard two good ids."""
    clients, _ = mock_clients(yahoo_status=500)
    with api(engine, clients) as client:
        response = client.post("/refresh")

    assert response.status_code == 200, response.text
    entries = {entry["source"]: entry for entry in response.json()}
    assert entries["yahoo"]["failed"] is True
    assert entries["yahoo"]["snapshot_id"] is None
    assert entries["yahoo"]["error"] is not None
    assert entries["sleeper"]["snapshot_id"] is not None
    assert entries["ffc"]["snapshot_id"] is not None


def test_refresh_uses_the_configured_season_and_game_key(engine):
    """The one path that reads `settings` through no injection seam."""
    clients, urls = mock_clients()
    with api(engine, clients) as client:
        assert client.post("/refresh").status_code == 200

    # Not endswith: fetch_json appends season_type and four position[] parameters.
    assert urls["sleeper"][0].startswith(projections_url(SEASON))
    assert f"year={SEASON}" in urls["ffc"][0]
    assert f"/league/{GAME_KEY}.l.public/" in urls["yahoo"][0]


def test_the_refresh_handler_is_not_async():
    """ADR-38: the same blocking call from an `async def` would stall the event loop.

    A decision pinned by a comment is one a later edit can contradict silently.
    """
    from redraft.api import refresh

    assert not inspect.iscoroutinefunction(refresh.refresh_now)
