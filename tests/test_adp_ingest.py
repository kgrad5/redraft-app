"""The two ADP ingesters (issue #7): Yahoo for location, FFC for dispersion.

The fixtures are miniatures of the real payloads, shaped so every rule is observable.
Both sources are represented by the players they actually disagree about — Puka Nacua,
whose team FFC writes `LAR` and nflverse writes `LA`; Ashton Jeanty, a rookie no
nflverse roster row gives a `yahoo_id`; Kyle Pitts, whom FFC calls `Kyle Pitts Sr.` —
so a regression shows up as the specific player it would cost on draft night.

The Yahoo fixture's key order is load-bearing. `INJURED` carries `status`, `status_full`
and `injury_note` *ahead* of `display_position`, which is exactly what shifts the index
in the array form specs/draft-assistant.md §4.2 warns about. Under `format=json_f` there
is no array to shift (ADR-45), and the test is what says so out loud.

No live network anywhere: every fetch goes through httpx2.MockTransport.
"""

import httpx2
import pytest
import sqlalchemy as sa

from redraft.ingest.adp import DuplicateResolutionError, EmptyAdpError, FfcADP, YahooADP
from redraft.providers.ffc import PayloadShapeError as FfcPayloadShapeError
from redraft.providers.yahoo import PayloadShapeError as YahooPayloadShapeError

GAME_KEY = 470
SEASON = 2026

# --- the crosswalk -------------------------------------------------------------------
#
# Jeanty carries no yahoo_num_id: nflverse's roster_weekly_2026 supplies one for 549 of
# 1,004 fantasy-position rows and for none of the 2026 rookies, which is the hole
# ADR-46's name tier exists to cover. Nacua's team is nflverse's `LA`, not FFC's `LAR`.
FIXTURE_PLAYERS = [
    {"full_name": "Jahmyr Gibbs", "team": "DET", "position": "RB", "yahoo_num_id": 40059},
    {"full_name": "Ashton Jeanty", "team": "LV", "position": "RB", "yahoo_num_id": None},
    {"full_name": "Puka Nacua", "team": "LA", "position": "WR", "yahoo_num_id": 33474},
    {"full_name": "Kyle Pitts", "team": "ATL", "position": "TE", "yahoo_num_id": 33418},
]

# --- Yahoo ---------------------------------------------------------------------------

# Healthy: no status keys at all, which is the shorter of the 13 key-sets the live
# response carries. Resolves on yahoo_num_id, tier one.
GIBBS = {
    "player_key": "470.p.40059",
    "player_id": "40059",
    "name": {"full": "Jahmyr Gibbs", "first": "Jahmyr", "last": "Gibbs"},
    "editorial_team_abbr": "Det",
    "display_position": "RB",
    "primary_position": "RB",
    "draft_analysis": {
        "average_pick": "1.4",
        "average_round": "1.0",
        "percent_drafted": "1.00",
        "preseason_average_pick": "1.4",
        "preseason_average_round": "1.0",
    },
}

# The reason this test module exists. Four keys the healthy record does not have, all
# of them ahead of display_position — the shift that moves it from [12] to [14] in the
# array form. Resolves only on (full_name, position): no yahoo_num_id for a rookie.
INJURED = {
    "player_key": "470.p.100001",
    "player_id": "100001",
    "name": {"full": "Ashton Jeanty", "first": "Ashton", "last": "Jeanty"},
    "status": "Q",
    "status_full": "Questionable",
    "injury_note": "Ankle",
    "player_notes_last_timestamp": 1756600000,
    "editorial_team_abbr": "LV",
    "display_position": "RB",
    "primary_position": "RB",
    "draft_analysis": {
        "average_pick": "17.1",
        "average_round": "2.0",
        "percent_drafted": "0.99",
        "preseason_average_pick": "16.9",
        "preseason_average_round": "2.0",
    },
}

# 969 of the live pool's 1,195 records look like this. `float("-")` raises, so a parser
# that assumes a number dies on 81% of the board (ADR-47).
SENTINEL = {
    "player_key": "470.p.33474",
    "player_id": "33474",
    "name": {"full": "Puka Nacua", "first": "Puka", "last": "Nacua"},
    "editorial_team_abbr": "LA",
    "display_position": "WR",
    "primary_position": "WR",
    "draft_analysis": {
        "average_pick": "-",
        "average_round": "-",
        "percent_drafted": "-",
        "preseason_average_pick": "-",
        "preseason_average_round": "-",
    },
}

# A real ADP for a player no roster row mentions. This is what `unresolved` counts.
UNRESOLVED = {
    "player_key": "470.p.100002",
    "player_id": "100002",
    "name": {"full": "Oronde Gadsden", "first": "Oronde", "last": "Gadsden"},
    "editorial_team_abbr": "LAC",
    "display_position": "TE",
    "primary_position": "TE",
    "draft_analysis": {"average_pick": "126.4", "preseason_average_pick": "126.6"},
}

# `players` holds QB/RB/WR/TE only, so 42 of Yahoo's records can never resolve. They are
# skipped before resolution rather than counted, or the tripwire reads 44 on a good run.
DEFENSE = {
    "player_key": "470.p.100003",
    "player_id": "100003",
    "name": {"full": "Rams", "first": "Los Angeles", "last": "Rams"},
    "editorial_team_abbr": "LAR",
    "display_position": "DEF",
    "primary_position": "DEF",
    "draft_analysis": {"average_pick": "88.1", "preseason_average_pick": "88.3"},
}

YAHOO_PLAYERS = [GIBBS, INJURED, SENTINEL, UNRESOLVED, DEFENSE]


def yahoo_payload(players=None):
    """The `fantasy_content` envelope — not the `service` one, which this host never
    sends (ADR-44)."""
    records = YAHOO_PLAYERS if players is None else players
    return {
        "fantasy_content": {
            "xml:lang": "en-US",
            "league": {
                "league_key": f"{GAME_KEY}.l.public",
                "players": [{"player": record} for record in records],
            },
        }
    }


# What `format=json` returns instead: `league` is a list and every player is a
# positional array. Nothing parses it — it raises at the boundary (ADR-45).
YAHOO_ARRAY_PAYLOAD = {
    "fantasy_content": {
        "league": [
            {"league_key": f"{GAME_KEY}.l.public"},
            {"settings": {}},
            {"players": {"0": {"player": [[{"player_key": "470.p.40059"}]]}, "count": 1}},
        ]
    }
}

# --- FFC -----------------------------------------------------------------------------

FFC_GIBBS = {
    "player_id": 5672,
    "name": "Jahmyr Gibbs",
    "position": "RB",
    "team": "DET",
    "adp": 1.5,
    "times_drafted": 2120,
    "high": 1,
    "low": 4,
    "stdev": 0.7,
    "bye": 6,
}
# FFC writes LAR where nflverse writes LA. Including team in the key would drop the
# third pick in the draft, which is why ADR-46 leaves it out.
FFC_NACUA = {
    "player_id": 5714,
    "name": "Puka Nacua",
    "position": "WR",
    "team": "LAR",
    "adp": 2.9,
    "times_drafted": 378,
    "high": 1,
    "low": 6,
    "stdev": 0.9,
    "bye": 11,
}
# One of the seven suffix variants exact matching cannot place. Issue #8 closes it.
FFC_SUFFIX = {
    "player_id": 5218,
    "name": "Kyle Pitts Sr.",
    "position": "TE",
    "team": "ATL",
    "adp": 77.4,
    "times_drafted": 210,
    "high": 55,
    "low": 96,
    "stdev": 9.1,
    "bye": 5,
}
# 51 of FFC's 271 rows are DEF or PK. Skipped, not counted.
FFC_DEFENSE = {
    "player_id": 1331,
    "name": "Washington Defense",
    "position": "DEF",
    "team": "WAS",
    "adp": 196.1,
    "times_drafted": 20,
    "high": 144,
    "low": 210,
    "stdev": 22.5,
    "bye": 7,
}

FFC_PLAYERS = [FFC_GIBBS, FFC_NACUA, FFC_SUFFIX, FFC_DEFENSE]


def ffc_payload(players=None):
    return {
        "status": "Success",
        "meta": {"type": "PPR", "teams": 12, "rounds": 15},
        "players": FFC_PLAYERS if players is None else players,
    }


# --- fixtures ------------------------------------------------------------------------

SELECT_ROWS = sa.text(
    "SELECT p.full_name, a.source, a.adp, a.stdev, a.high, a.low, a.times_drafted "
    "FROM adp a JOIN players p ON p.player_id = a.player_id"
)
COUNT_ROWS = sa.text("SELECT count(*) FROM adp")
COUNT_SNAPSHOTS = sa.text("SELECT count(*) FROM snapshots")


@pytest.fixture(scope="module")
def players(migrated, engine):
    """The crosswalk both ingesters resolve against, committed once for the module."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO players (full_name, team, position, yahoo_num_id) "
                "VALUES (:full_name, :team, :position, :yahoo_num_id)"
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


def make_client(payload) -> tuple[httpx2.Client, list[str]]:
    """A client serving `payload`, and the list of URLs it was asked for.

    The URL list is what makes the request assertable. `format=json_f` and the
    `.l.public` path are decisions (ADR-44, ADR-45), and a decision pinned by a comment
    is one a later edit can contradict silently.
    """
    requested: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requested.append(str(request.url))
        return httpx2.Response(200, json=payload)

    return httpx2.Client(transport=httpx2.MockTransport(handler)), requested


def ingest_yahoo(connection, payload=None):
    client, requested = make_client(yahoo_payload() if payload is None else payload)
    return YahooADP(client=client, game_key=GAME_KEY).ingest(connection), requested


def ingest_ffc(connection, payload=None):
    client, requested = make_client(ffc_payload() if payload is None else payload)
    return FfcADP(client=client, season=SEASON).ingest(connection), requested


def written(connection) -> dict[str, dict]:
    rows = connection.execute(SELECT_ROWS).all()
    return {
        full_name: {
            "source": source,
            "adp": adp,
            "stdev": stdev,
            "high": high,
            "low": low,
            "times_drafted": times_drafted,
        }
        for full_name, source, adp, stdev, high, low, times_drafted in rows
    }


# --- tests ---------------------------------------------------------------------------


def test_yahoo_writes_the_location_and_no_dispersion(connection):
    result, _ = ingest_yahoo(connection)

    rows = written(connection)
    assert result.rows_written == len(rows) == 2
    assert rows["Jahmyr Gibbs"]["adp"] == 1.4
    assert rows["Ashton Jeanty"]["adp"] == 16.9
    assert rows["Jahmyr Gibbs"]["source"] == "yahoo"
    # Yahoo publishes no dispersion. That is the whole reason FFC is ingested at all.
    for row in rows.values():
        assert (row["stdev"], row["high"], row["low"], row["times_drafted"]) == (
            None,
            None,
            None,
            None,
        )


def test_ffc_writes_the_dispersion(connection):
    result, _ = ingest_ffc(connection)

    rows = written(connection)
    assert result.rows_written == len(rows) == 2
    gibbs = rows["Jahmyr Gibbs"]
    assert gibbs["source"] == "ffc"
    assert (gibbs["adp"], gibbs["stdev"], gibbs["times_drafted"]) == (1.5, 0.7, 2120)
    assert (gibbs["high"], gibbs["low"]) == (1, 4)


def test_the_injury_shift_does_not_move_anything(connection):
    """The issue's named check.

    INJURED carries status, status_full, injury_note and player_notes_last_timestamp
    ahead of display_position — the shift that breaks positional indexing on exactly
    the players draft night most needs. Keyed access cannot see it.
    """
    result, _ = ingest_yahoo(connection)

    rows = written(connection)
    assert "Ashton Jeanty" in rows, "the injured record did not resolve"
    assert rows["Ashton Jeanty"]["adp"] == 16.9
    # And the healthy record, which has four fewer keys, resolves identically.
    assert rows["Jahmyr Gibbs"]["adp"] == 1.4
    assert result.rows_written == 2


def test_the_array_form_raises_rather_than_reading_a_wrong_index(connection):
    """If json_f is ever withdrawn, the parse must fail loudly.

    `format=json` nests `league` as a list, so the failure is a raise at the boundary
    rather than a position read off the wrong offset.
    """
    with pytest.raises(YahooPayloadShapeError):
        ingest_yahoo(connection, YAHOO_ARRAY_PAYLOAD)

    assert connection.execute(COUNT_ROWS).scalar_one() == 0


def test_the_yahoo_request_is_pinned_on_the_wire(connection):
    """format=json_f and .l.public are decisions, not spellings (ADR-44, ADR-45)."""
    _, requested = ingest_yahoo(connection)

    assert len(requested) == 1
    url = requested[0]
    assert f"/league/{GAME_KEY}.l.public/" in url
    assert "format=json_f" in url
    assert "position=ALL" in url
    assert url.endswith("/draft_analysis?format=json_f")
    # The carrier league id the spec describes does not exist here and must not reappear.
    assert "players/nfl/" not in url


def test_the_ffc_request_pins_the_year_and_omits_teams(connection):
    """`year` is honoured; `teams` is display-only and returns byte-identical data."""
    _, requested = ingest_ffc(connection)

    assert len(requested) == 1
    url = requested[0]
    assert f"year={SEASON}" in url
    assert "teams=" not in url
    assert "/api/v1/adp/ppr" in url


def test_the_dash_sentinel_writes_no_row_and_is_not_counted(connection):
    """969 of 1,195 live records carry it. It is not a missing player (ADR-47)."""
    result, _ = ingest_yahoo(connection)

    assert "Puka Nacua" not in written(connection)
    # Nacua resolves perfectly well; he simply has no ADP. Counting him would put 969
    # records on a tripwire meant to show which players the board is missing.
    assert result.unresolved == 1


def test_unresolvable_records_are_counted_and_unholdable_positions_are_not(connection):
    """The count is a tripwire, so it must read 1 here rather than 2.

    Gadsden has a real ADP and no row in `players` — that is the one to report. The DEF
    record can never resolve, because `players` holds QB/RB/WR/TE only.
    """
    yahoo, _ = ingest_yahoo(connection)
    assert yahoo.unresolved == 1
    assert "Rams" not in written(connection)


def test_ffc_matches_on_name_and_position_never_on_team(connection):
    """FFC writes LAR where nflverse writes LA. Team is the volatile field (ADR-46)."""
    result, _ = ingest_ffc(connection)

    rows = written(connection)
    assert rows["Puka Nacua"]["adp"] == 2.9
    # The suffix variant is the one exact matching cannot place; issue #8 closes it.
    assert "Kyle Pitts" not in rows
    assert result.unresolved == 1


def test_two_sources_land_under_two_snapshots(connection):
    """ADR-30: a snapshot is one source's fetch, so they can never share one."""
    yahoo, _ = ingest_yahoo(connection)
    ffc, _ = ingest_ffc(connection)

    assert yahoo.snapshot_id != ffc.snapshot_id
    assert connection.execute(COUNT_SNAPSHOTS).scalar_one() == 2

    pairs = connection.execute(
        sa.text(
            "SELECT s.source, a.source, count(*) FROM adp a "
            "JOIN snapshots s USING (snapshot_id) GROUP BY 1, 2 ORDER BY 1"
        )
    ).all()
    assert pairs == [("ffc", "ffc", 2), ("yahoo", "yahoo", 2)]


def test_an_empty_run_raises_rather_than_reporting_success(connection):
    """A green run with an empty board is the worst outcome issue #9 could be handed."""
    with pytest.raises(EmptyAdpError):
        ingest_yahoo(connection, yahoo_payload([SENTINEL, UNRESOLVED, DEFENSE]))

    with pytest.raises(EmptyAdpError):
        ingest_ffc(connection, ffc_payload([FFC_SUFFIX, FFC_DEFENSE]))


def test_a_reshaped_ffc_payload_is_heard_at_the_boundary(connection):
    with pytest.raises(FfcPayloadShapeError):
        ingest_ffc(connection, {"status": "Success", "meta": {}})


# --- what the review found -----------------------------------------------------------
#
# None of the four below is reachable against today's live payloads: neither source
# carries a duplicate (name, position), no comma-joined position carries an ADP, every
# FFC row is complete, and `"-"` is the only non-numeric ADP Yahoo sends. They are here
# because each one's failure mode is a whole lost run or a quietly missing player, and
# because "not reachable today" is a fact about Yahoo's roster, not about this code.


@pytest.mark.parametrize(
    "envelope", [[], "error", None, 42], ids=["list", "string", "null", "number"]
)
def test_a_non_object_envelope_raises_a_shape_error_not_an_attribute_error(connection, envelope):
    """A Yahoo error page puts a string or a list where the league goes.

    `.get` on either is an AttributeError, which no caller can catch as a shape problem
    — and shape problems are the entire reason this boundary raises its own exception.
    """
    with pytest.raises(YahooPayloadShapeError):
        ingest_yahoo(connection, {"fantasy_content": envelope})


def test_a_players_element_that_wraps_no_player_raises_a_shape_error(connection):
    payload = {"fantasy_content": {"league": {"players": [{"not_a_player": {}}]}}}
    with pytest.raises(YahooPayloadShapeError):
        ingest_yahoo(connection, payload)


def test_two_records_resolving_to_one_player_name_both_rather_than_colliding(connection):
    """The id tier and the name tier can land on the same player.

    Gibbs resolves on yahoo_num_id; the impostor shares his name and position, carries
    an id `players` has never seen, and so falls through to the name tier onto the same
    row. Left alone that is an IntegrityError from an executemany naming neither record,
    on a transaction whose rollback then discards the snapshot too (ADR-38).
    """
    impostor = dict(GIBBS, player_key="470.p.999999", player_id="999999")
    impostor["draft_analysis"] = dict(GIBBS["draft_analysis"], preseason_average_pick="240.0")

    with pytest.raises(DuplicateResolutionError, match="Jahmyr Gibbs"):
        ingest_yahoo(connection, yahoo_payload([GIBBS, impostor]))

    assert connection.execute(COUNT_ROWS).scalar_one() == 0


def test_a_multi_eligibility_position_is_not_dropped_before_the_counter(connection):
    """display_position is comma-joined for a multi-eligibility player.

    Matching on it would drop the record ahead of the `unresolved` count — a player with
    a real ADP gone from the board with no signal anywhere, which is the failure
    specs/draft-assistant.md §4.3 names. primary_position is single-valued on every
    record.
    """
    flex = dict(INJURED, display_position="RB,TE")
    result, _ = ingest_yahoo(connection, yahoo_payload([flex]))

    assert result.rows_written == 1
    assert written(connection)["Ashton Jeanty"]["adp"] == 16.9


def test_an_unrecognised_adp_value_stops_the_run(connection):
    """`"-"` is the only non-numeric value the live pool holds, so a second one is a
    shape change, not a missing ADP — and `unresolved` would not move for it."""
    surprise = dict(GIBBS)
    surprise["draft_analysis"] = dict(GIBBS["draft_analysis"], preseason_average_pick="N/A")

    with pytest.raises(YahooPayloadShapeError, match="N/A"):
        ingest_yahoo(connection, yahoo_payload([surprise]))


@pytest.mark.parametrize("field", ["adp", "stdev", "times_drafted", "position"])
def test_an_ffc_row_missing_a_field_is_refused_at_the_boundary(connection, field):
    """Otherwise a KeyError from inside the write loop, or — for a null `adp`, which the
    column rejects — an IntegrityError at the insert. Both land after the snapshot."""
    broken = dict(FFC_GIBBS)
    broken[field] = None

    with pytest.raises(FfcPayloadShapeError, match=field):
        ingest_ffc(connection, ffc_payload([broken]))
