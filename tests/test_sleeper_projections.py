"""The Sleeper projection ingester (issue #6): components only, and now four tiers.

The fixture payload is a six-record miniature of the real one, shaped so every rule is
observable: a QB and a WR that resolve on their sleeper_id, a player carrying real
projections whose sleeper_id no roster row ever supplied, one that only the name fold can
place, and two ADP shells — one resolvable, one not. Each carries the `pts_` and `adp_`
keys the live response carries, so a regression that stops excluding them shows up as
extra rows rather than as a silent change of meaning.

Resolution was `players.sleeper_id` alone until issue #8 (ADR-42, superseded by ADR-49);
it is now `redraft.identity`, which is why every record carries the nested `player` object
the name tiers read. The matching rules themselves are tested in tests/test_identity.py —
what this module asserts is that this ingester goes through them.

No live network anywhere: every fetch goes through httpx2.MockTransport.
"""

import httpx2
import pytest
import sqlalchemy as sa

from redraft.ingest.projections import EmptyProjectionsError, SleeperProjections
from redraft.providers.sleeper import (
    PayloadShapeError,
    UnknownStatKeyError,
    component_stats,
    projections_url,
)

SEASON = 2026

# Nine components: the six pass_ keys, cmp_pct, two rush_ keys and gp. The three
# pts_ totals and the three adp_ keys are what must not survive.
MAHOMES = {
    "player_id": "4046",
    "team": "KC",
    "company": "rotowire",
    "player": {"first_name": "Patrick", "last_name": "Mahomes", "position": "QB"},
    "stats": {
        "pass_att": 560.0,
        "pass_cmp": 372.0,
        "pass_yd": 4180.0,
        "pass_td": 30.0,
        "pass_int": 11.0,
        "cmp_pct": 66.4,
        "rush_att": 61.0,
        "rush_yd": 305.0,
        "gp": 17.0,
        "pts_ppr": 324.9,
        "pts_std": 294.9,
        "pts_half_ppr": 309.9,
        "adp_ppr": 42.1,
        "adp_dynasty": 999.0,
        "adp_rookie": 999.0,
    },
}

# Eight components, including the bare `rec` that no prefix covers and a bonus_ key.
ST_BROWN = {
    "player_id": "7547",
    "team": "DET",
    "company": "rotowire",
    "player": {"first_name": "Amon-Ra", "last_name": "St. Brown", "position": "WR"},
    "stats": {
        "rec": 115.0,
        "rec_yd": 1263.0,
        "rec_td": 9.0,
        "rec_fd": 62.0,
        "rec_40p": 6.4,
        "bonus_rec_wr": 115.0,
        "fum_lost": 1.0,
        "gp": 17.0,
        "pts_ppr": 291.3,
        "adp_ppr": 12.4,
        "adp_dynasty": 999.0,
    },
}

# Sleeper's pool is deliberately wider than any roster. This one carries real
# projections, but no 2026 roster row ever gave nflverse his sleeper_id, so tier
# one of specs/draft-assistant.md §4.3 cannot place him and issue #8 must.
UNRESOLVED = {
    "player_id": "3321",
    "team": None,
    "player": {"first_name": "Tyreek", "last_name": "Hill", "position": "WR"},
    "stats": {"rec": 42.0, "rec_yd": 511.0, "gp": 12.0, "adp_ppr": 227.2},
}

# The case issue #8 exists for, and the only one inside the top 200: no roster row gave
# nflverse his sleeper_id, so tier one misses him, and `players` spells him with a suffix
# Sleeper omits. Only the fold of specs/draft-assistant.md §4.3 places him.
SUFFIX_ONLY = {
    "player_id": "77777",
    "team": "LV",
    "player": {"first_name": "Mike", "last_name": "Washington", "position": "RB"},
    "stats": {"rush_yd": 458.0, "rush_td": 3.0, "rec": 16.0, "gp": 18.0, "adp_ppr": 158.9},
}

# An ADP shell: Sleeper returns one for every player in its pool so the adp_ fields
# have somewhere to live. gp is the only component and there is nothing projected —
# 2,559 of the 3,114 filtered records look like this. One resolvable, one not, so
# neither can be mistaken for the other's case.
SHELL_RESOLVABLE = {
    "player_id": "1339",
    "player": {"first_name": "Zach", "last_name": "Ertz", "position": "TE"},
    "stats": {"gp": 0.0, "adp_ppr": 999.0, "adp_dynasty": 999.0},
}
SHELL_UNRESOLVABLE = {
    "player_id": "99999",
    "player": {"first_name": "Nobody", "last_name": "Atall", "position": "WR"},
    "stats": {"gp": 0.0, "adp_ppr": 999.0},
}

PAYLOAD = [MAHOMES, ST_BROWN, UNRESOLVED, SUFFIX_ONLY, SHELL_RESOLVABLE, SHELL_UNRESOLVABLE]

MAHOMES_COMPONENTS = 9
ST_BROWN_COMPONENTS = 8
SUFFIX_ONLY_COMPONENTS = 4

# Chase carries no sleeper_id, which is the state issue #5 leaves a player in when
# no roster row supplies one. He must not become a crosswalk entry keyed by NULL.
FIXTURE_PLAYERS = [
    {"full_name": "Patrick Mahomes", "team": "KC", "position": "QB", "sleeper_id": "4046"},
    {"full_name": "Amon-Ra St. Brown", "team": "DET", "position": "WR", "sleeper_id": "7547"},
    {"full_name": "Zach Ertz", "team": None, "position": "TE", "sleeper_id": "1339"},
    {"full_name": "Ja'Marr Chase", "team": "CIN", "position": "WR", "sleeper_id": None},
    # Held with the suffix Sleeper omits, and with no sleeper_id — exactly as the live
    # crosswalk holds him.
    {"full_name": "Mike Washington Jr.", "team": "LV", "position": "RB", "sleeper_id": None},
]

SELECT_ROWS = sa.text(
    "SELECT p.sleeper_id, j.stat_key, j.value FROM projections j "
    "JOIN players p ON p.player_id = j.player_id"
)
COUNT_ROWS = sa.text("SELECT count(*) FROM projections")


@pytest.fixture(scope="module")
def players(migrated, engine):
    """The crosswalk this ingester joins on, committed once for the module."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO players (full_name, team, position, sleeper_id) "
                "VALUES (:full_name, :team, :position, :sleeper_id)"
            ),
            FIXTURE_PLAYERS,
        )


@pytest.fixture
def connection(players, engine):
    """One transaction per test, always rolled back, so every test starts empty."""
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def make_client(payload=PAYLOAD) -> tuple[httpx2.Client, list[str]]:
    """A client serving `payload`, and the list of URLs it was asked for.

    The URL list is what makes the query string assertable: `position[]` is encoded
    by httpx2 rather than by this repo, so the only honest check is what went out.
    """
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, json=payload)

    return httpx2.Client(transport=httpx2.MockTransport(handler)), requested


def ingest(connection, payload=PAYLOAD):
    client, requested = make_client(payload)
    result = SleeperProjections(client=client, season=SEASON).ingest(connection)
    return result, requested


def written(connection) -> dict[tuple[str, str], float]:
    rows = connection.execute(SELECT_ROWS).all()
    return {(sleeper_id, stat_key): value for sleeper_id, stat_key, value in rows}


def test_happy_path_writes_one_row_per_component(connection):
    result, _ = ingest(connection)

    rows = written(connection)
    assert (
        result.rows_written
        == len(rows)
        == MAHOMES_COMPONENTS + ST_BROWN_COMPONENTS + SUFFIX_ONLY_COMPONENTS
    )

    assert rows[("4046", "pass_td")] == 30.0
    assert rows[("4046", "rush_att")] == 61.0
    assert rows[("4046", "cmp_pct")] == 66.4
    assert rows[("4046", "gp")] == 17.0
    # The bare `rec`, which no component prefix covers.
    assert rows[("7547", "rec")] == 115.0
    assert rows[("7547", "rec_yd")] == 1263.0
    assert rows[("7547", "bonus_rec_wr")] == 115.0
    assert rows[("7547", "fum_lost")] == 1.0


def test_no_fantasy_point_total_is_ingested_anywhere(connection):
    """The issue's check. The fixture carries all three totals on Mahomes."""
    ingest(connection)

    stat_keys = {key for _, key in written(connection)}
    assert stat_keys, "nothing was written, so this would pass vacuously"
    offenders = [key for key in stat_keys if "pts" in key or "point" in key.lower()]
    assert not offenders, f"a fantasy-point total reached projections: {offenders}"

    # And no column is named for points either — the other half of the check.
    columns = connection.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'projections'"
        )
    ).scalars()
    assert set(columns) == {"snapshot_id", "player_id", "stat_key", "value"}


def test_adp_keys_never_land(connection):
    """Including the 999.0 sentinels, which poison any ranking that reads them."""
    ingest(connection)

    stat_keys = {key for _, key in written(connection)}
    assert not [key for key in stat_keys if key.startswith("adp_")]
    assert 999.0 not in written(connection).values()


def test_unknown_stat_key_fails_loudly_naming_the_key(connection):
    """A key in no class stops the run rather than being guessed at either way."""
    surprise = {"player_id": "4046", "stats": {"rush_att": 61.0, "wibble_yd": 3.0}}

    with pytest.raises(UnknownStatKeyError, match="wibble_yd"):
        ingest(connection, [surprise])

    assert connection.execute(COUNT_ROWS).scalar_one() == 0


def test_a_points_total_under_a_new_name_would_stop_the_run():
    """Why the three totals are named exactly rather than excluded by prefix: a
    renamed total lands in no class, and `pts_allow_0` is a real component that a
    `pts_` denylist would have thrown away."""
    with pytest.raises(UnknownStatKeyError, match="fantasy_points"):
        component_stats({"rush_att": 61.0, "fantasy_points": 240.0})
    with pytest.raises(UnknownStatKeyError, match="pts_allow_0"):
        component_stats({"pts_allow_0": 4.0})


def test_unresolved_sleeper_id_writes_nothing_and_is_counted(connection):
    result, _ = ingest(connection)

    assert result.unresolved == 1
    assert not [sid for sid, _ in written(connection) if sid == UNRESOLVED["player_id"]]
    # The count is derived from the records, so the tripwire issue #9 reads and the
    # report an operator reads cannot disagree (ADR-49). Asserted at a non-zero value:
    # both sides are 0 on a clean run, where this would hold whether or not it was true.
    assert result.unresolved == len(result.unmatched) == 1
    assert result.unmatched[0].name == "Tyreek Hill"


def test_a_name_the_crosswalk_misses_still_resolves_through_the_fold(connection):
    """The record issue #8 exists for, through the ingester rather than the resolver.

    Sleeper publishes `Mike Washington`; `players` holds `Mike Washington Jr.` with no
    sleeper_id. Before ADR-49 he was one of 37 unwritten records and the only one inside
    the top 200, which is the bar this issue is measured against.
    """
    result, _ = ingest(connection)

    rows = written(connection)
    # Keyed by sleeper_id in `written`, and his `players` row has none — so the rows
    # land under a NULL key, which is itself the proof he resolved by name.
    assert result.rows_written == MAHOMES_COMPONENTS + ST_BROWN_COMPONENTS + SUFFIX_ONLY_COMPONENTS
    assert rows[(None, "rush_yd")] == 458.0
    assert rows[(None, "rec")] == 16.0
    assert [record.name for record in result.unmatched] == ["Tyreek Hill"]


def test_adp_shell_writes_nothing_and_is_not_counted_unresolved(connection):
    """A record whose only component is gp is not a projection. The unresolvable
    shell must not inflate `unresolved` either — a tripwire at 2,243 is not one."""
    result, _ = ingest(connection)

    rows = written(connection)
    assert not [sid for sid, _ in rows if sid == SHELL_RESOLVABLE["player_id"]]
    assert result.unresolved == 1


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param([], id="empty-response"),
        pytest.param([SHELL_RESOLVABLE, SHELL_UNRESOLVABLE], id="nothing-but-adp-shells"),
        pytest.param([UNRESOLVED], id="nobody-resolves"),
    ],
)
def test_a_run_that_writes_nothing_fails_rather_than_reporting_success(connection, payload):
    """A total narrowing, as against the partial one `unresolved` exists to count.

    Sleeper answering with no projections — a season not yet published, a filter that
    stopped matching, a crosswalk that resolves nobody — would otherwise hand issue #9
    a green run and draft night an empty board.
    """
    with pytest.raises(EmptyProjectionsError):
        ingest(connection, payload)

    assert connection.execute(COUNT_ROWS).scalar_one() == 0


def test_yahoo_id_is_never_a_join_key(connection):
    """The issue's third out-of-scope item, pinned rather than left to construction.

    specs/draft-assistant.md §2.3: Sleeper's yahoo_id is ~24% populated and null for
    essentially every player drafted since 2021. A record carrying one that matches a
    real player's yahoo_num_id must still not resolve — only sleeper_id may.
    """
    # Without this the test would pass vacuously: no player would carry the id, so a
    # crosswalk widened to yahoo_num_id would still resolve nobody.
    connection.execute(
        sa.text("UPDATE players SET yahoo_num_id = 30123 WHERE sleeper_id = :sid"),
        {"sid": MAHOMES["player_id"]},
    )
    poser = {
        "player_id": "not-a-sleeper-id",
        "yahoo_id": "30123",
        # A name no `players` row holds, so the name tiers cannot place him either and
        # the only thing left that could is the yahoo_id this test forbids.
        "player": {"first_name": "Nobody", "last_name": "Atall", "position": "WR"},
        "stats": {"rush_att": 99.0, "rec_yd": 99.0},
    }

    with pytest.raises(EmptyProjectionsError):
        ingest(connection, [poser])

    assert connection.execute(COUNT_ROWS).scalar_one() == 0


def test_snapshot_holds_the_array_exactly_as_it_arrived(connection):
    result, _ = ingest(connection)

    source, raw = connection.execute(
        sa.text("SELECT source, raw_payload FROM snapshots WHERE snapshot_id = :id"),
        {"id": result.snapshot_id},
    ).one()
    assert source == "sleeper"
    assert raw == PAYLOAD


def test_request_carries_the_bracketed_position_parameter(connection):
    """The unbracketed spelling answers 200 with WR only — 1,364 records of the
    3,114 (verified 2026-08-31). A silent 56% narrowing of the board is exactly
    what specs/draft-assistant.md §4.2 warns a parser must never accept quietly.
    """
    _, requested = ingest(connection)

    assert len(requested) == 1
    url = requested[0]
    assert url.startswith(projections_url(SEASON))
    assert url.count("position%5B%5D=") == 4
    assert "season_type=regular" in url


def test_non_list_payload_is_refused(connection):
    """Sleeper answers with a bare array. A shape change is heard here rather than
    read downstream as missing data."""
    with pytest.raises(PayloadShapeError, match="dict"):
        ingest(connection, {"error": "bad-request"})


def test_component_stats_classifies_every_family():
    """The classification itself, away from the database: one key per family."""
    components = {
        "pass_yd": 1.0,
        "rush_yd": 2.0,
        "rec_yd": 3.0,
        "rec": 4.0,
        "fum_lost": 5.0,
        "bonus_rec_te": 6.0,
        "cmp_pct": 7.0,
        "pr_td": 8.0,
        "def_kr_td": 9.0,
        "idp_tkl": 10.0,
        "gp": 11.0,
    }
    excluded = {"pts_ppr": 12.0, "pts_std": 13.0, "pts_half_ppr": 14.0, "adp_std": 15.0}

    assert component_stats(components | excluded) == components
