"""The nflverse ingester (issue #5): universe, teams, byes, crosswalk — and loud failure.

Fixtures are a four-team mini-league. Its schedule spans REG weeks 1–3, so every
team plays twice and sits once: KC and BUF bye week 3, DET and PHI bye week 2. The
off-season rows (a 2025 game, a POST game) are shaped so that forgetting either
filter changes the byes and fails the happy path, rather than passing by luck.

No live network anywhere: every fetch goes through httpx2.MockTransport, keyed on
the same URLs the provider builds.
"""

import httpx2
import pytest
import sqlalchemy as sa

from redraft.ingest.players import IngestError, ingest_players
from redraft.providers.nflverse import PLAYERS_URL, SCHEDULE_URL, EmptyTableError, roster_url

SEASON = 2026

# St. Brown's two rows disagree on team so the max-week rule is observable: a
# regression to min-week or first-row-wins lands him on BUF with BUF's bye.
# Odunze's same-week CUT-then-ACT pair pins the team-conferring tie-break in the
# row order that first-row-wins would get wrong.
ROSTER_CSV = """\
team,position,status,full_name,gsis_id,sleeper_id,yahoo_id,week
KC,QB,ACT,Patrick Mahomes,00-0033873,4046,30123,1
BUF,WR,ACT,Amon-Ra St. Brown,00-0036973,7547,,1
DET,WR,ACT,Amon-Ra St. Brown,00-0036973,7547,,2
BUF,RB,ACT,James Cook,00-0037248,8138,33555,1
PHI,TE,ACT,Dallas Goedert,00-0034351,,31019,1
PHI,TE,CUT,Zach Ertz,00-0031234,1339,28457,1
PHI,WR,CUT,Rome Odunze,00-0039893,11626,,1
PHI,WR,ACT,Rome Odunze,00-0039893,11626,40901,1
KC,TE,RET,Travis Kelce,00-0030506,1466,26686,1
KC,OL,ACT,Creed Humphrey,00-0036358,,,1
"""

# The re-run world: Cook traded BUF -> DET, and Mahomes's sleeper_id and Cook's
# yahoo_id went empty upstream — the COALESCE in the upsert must keep, for each
# column independently, the id already known.
ROSTER_RERUN_CSV = """\
team,position,status,full_name,gsis_id,sleeper_id,yahoo_id,week
KC,QB,ACT,Patrick Mahomes,00-0033873,,30123,1
DET,WR,ACT,Amon-Ra St. Brown,00-0036973,7547,,2
DET,RB,ACT,James Cook,00-0037248,8138,,1
PHI,TE,ACT,Dallas Goedert,00-0034351,,31019,1
PHI,TE,CUT,Zach Ertz,00-0031234,1339,28457,1
"""

# Mahomes duplicates a roster player (skipped), Aiyuk is the free-agent tail,
# Ryan's last_season is stale and the punter's position is out of universe.
PLAYERS_CSV = """\
gsis_id,display_name,position,last_season
00-0033873,Patrick Mahomes,QB,2026
00-0035676,Brandon Aiyuk,WR,2026
00-0026143,Matt Ryan,QB,2022
00-0023459,Johnny Punter,P,2026
"""

GAMES_CSV = """\
season,game_type,week,away_team,home_team
2026,REG,1,KC,DET
2026,REG,1,BUF,PHI
2026,REG,2,KC,BUF
2026,REG,3,DET,PHI
2025,REG,3,KC,BUF
2026,POST,4,KC,DET
"""

# KC plays every week; the other three each miss two, so no bye is derivable.
GAMES_NO_BYE_CSV = """\
season,game_type,week,away_team,home_team
2026,REG,1,KC,DET
2026,REG,2,KC,BUF
2026,REG,3,KC,PHI
"""


def csv_client(
    roster: str | None = ROSTER_CSV,
    players: str | None = PLAYERS_CSV,
    games: str | None = GAMES_CSV,
) -> httpx2.Client:
    """A client whose transport serves the three fixture tables; None means 404."""
    mapping = {roster_url(SEASON): roster, PLAYERS_URL: players, SCHEDULE_URL: games}

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = mapping.get(str(request.url))
        if body is None:
            return httpx2.Response(404, text="Not Found")
        return httpx2.Response(200, text=body)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


@pytest.fixture(autouse=True)
def clean_players(migrated, engine):
    """The module shares one database; every test starts from an empty table."""
    with engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM players"))


def all_players(engine) -> dict[str, dict]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT * FROM players")).mappings().all()
    return {row["nflverse_id"]: dict(row) for row in rows}


def test_happy_path_populates_universe_teams_byes_and_ids(engine):
    with engine.begin() as conn:
        count = ingest_players(conn, client=csv_client(), season=SEASON)

    players = all_players(engine)
    assert count == len(players) == 8  # seven fantasy roster players + one free agent

    mahomes = players["00-0033873"]
    assert mahomes["full_name"] == "Patrick Mahomes"
    assert mahomes["team"] == "KC"
    assert mahomes["bye_week"] == 3
    assert mahomes["sleeper_id"] == "4046"
    assert mahomes["yahoo_num_id"] == 30123

    # Two roster rows, one player — the max-week row wins, and the rows disagree
    # on team, so a min-week or first-row-wins regression lands on ("BUF", 3).
    st_brown = players["00-0036973"]
    assert (st_brown["team"], st_brown["bye_week"], st_brown["yahoo_num_id"]) == ("DET", 2, None)

    # Same week, CUT row first: the team-conferring row wins the tie.
    odunze = players["00-0039893"]
    assert (odunze["team"], odunze["bye_week"], odunze["yahoo_num_id"]) == ("PHI", 2, 40901)

    # The players.csv-only free agent: in the universe, on no team, with no bye.
    aiyuk = players["00-0035676"]
    assert (aiyuk["team"], aiyuk["bye_week"], aiyuk["position"]) == (None, None, "WR")

    # Filtered out: a lineman, a punter, and a player whose last season is stale.
    assert "00-0036358" not in players
    assert "00-0023459" not in players
    assert "00-0026143" not in players


def test_cut_and_ret_rows_contribute_ids_but_no_team(engine):
    with engine.begin() as conn:
        ingest_players(conn, client=csv_client(), season=SEASON)

    players = all_players(engine)
    ertz = players["00-0031234"]
    assert ertz["team"] is None
    assert ertz["bye_week"] is None
    assert ertz["sleeper_id"] == "1339"
    assert ertz["yahoo_num_id"] == 28457

    # RET is the set's other member, and the real 2026 file carries retired
    # fantasy-position players on real teams — they must not look draftable.
    kelce = players["00-0030506"]
    assert (kelce["team"], kelce["bye_week"]) == (None, None)
    assert (kelce["sleeper_id"], kelce["yahoo_num_id"]) == ("1466", 26686)


def test_missing_upstream_table_fails_loudly(engine):
    """The issue's named test: a table absent upstream raises, never returns empty."""
    with pytest.raises(httpx2.HTTPStatusError), engine.begin() as conn:
        ingest_players(conn, client=csv_client(roster=None), season=SEASON)

    assert all_players(engine) == {}


def test_header_only_table_fails_loudly(engine):
    header_only = ROSTER_CSV.splitlines()[0] + "\n"
    with pytest.raises(EmptyTableError), engine.begin() as conn:
        ingest_players(conn, client=csv_client(roster=header_only), season=SEASON)

    assert all_players(engine) == {}


def test_team_without_exactly_one_bye_fails_naming_the_team(engine):
    with pytest.raises(IngestError, match="DET"), engine.begin() as conn:
        ingest_players(conn, client=csv_client(games=GAMES_NO_BYE_CSV), season=SEASON)

    assert all_players(engine) == {}


def test_roster_team_absent_from_schedule_fails_naming_the_team(engine):
    """The other bye guard: a schedule that derives cleanly but never mentions a
    roster team must raise, not confer a silent NULL bye."""
    stray = ROSTER_CSV + "SEA,QB,ACT,Sam Darnold,00-0035264,5121,31007,1\n"
    with pytest.raises(IngestError, match="SEA"), engine.begin() as conn:
        ingest_players(conn, client=csv_client(roster=stray), season=SEASON)

    assert all_players(engine) == {}


def test_roster_row_without_gsis_id_fails_loudly(engine):
    """A row that cannot be keyed raises instead of being dropped — a silently
    dropped player is a player missing on draft night."""
    broken = ROSTER_CSV + "KC,RB,ACT,No Id,,9999,,1\n"
    with pytest.raises(IngestError, match="gsis_id"), engine.begin() as conn:
        ingest_players(conn, client=csv_client(roster=broken), season=SEASON)

    assert all_players(engine) == {}


def test_rerun_updates_in_place_and_keeps_known_ids(engine):
    with engine.begin() as conn:
        first = ingest_players(conn, client=csv_client(), season=SEASON)
    cook_id_before = all_players(engine)["00-0037248"]["player_id"]

    with engine.begin() as conn:
        second = ingest_players(conn, client=csv_client(roster=ROSTER_RERUN_CSV), season=SEASON)

    players = all_players(engine)
    assert (first, second) == (8, 6)
    # The upsert never deletes: players absent from the re-run keep their rows.
    assert len(players) == 8

    # The trade is a real change: team moves, the row does not.
    cook = players["00-0037248"]
    assert cook["team"] == "DET"
    assert cook["bye_week"] == 2
    assert cook["player_id"] == cook_id_before

    # Each crosswalk id column went empty upstream for a different player; the
    # ids already known survive the upsert independently.
    assert players["00-0033873"]["sleeper_id"] == "4046"
    assert cook["yahoo_num_id"] == 33555
