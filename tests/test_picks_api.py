"""The manual pick entry endpoints, end to end against a migrated database.

This module carries issue #3's verification check: a full 15-round, 12-team snake draft
entered pick by pick, undo, the position cap, and reset.
"""

import collections
import math

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

TEAMS = 12
# 1 QB, 2 RB, 3 WR, 1 TE, 1 W/R/T, 1 DEF and 6 bench — fifteen slots, no kicker
# (specs/draft-assistant.md §2.2), which is the 180 draft slots its §2.3 measures
# FFC's marginals against.
ROUNDS = 15
TOTAL_PICKS = TEAMS * ROUNDS
CAPS = {"RB": 6}


@pytest.fixture(scope="module")
def client(engine, player_pool):
    from redraft.api.picks import get_connection
    from redraft.main import app

    def override_connection():
        with engine.begin() as conn:
            yield conn

    app.dependency_overrides[get_connection] = override_connection
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def board(client, draft_id: str) -> dict:
    response = client.get(f"/draft/{draft_id}")
    assert response.status_code == 200, response.text
    return response.json()


def take(client, draft_id: str, player_id: int):
    return client.post(f"/draft/{draft_id}/picks", json={"player_id": player_id})


def event_rows(engine, draft_id: str) -> list[tuple]:
    with engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT event_type, pick_no, player_id, team_id, source FROM draft_events "
                "WHERE draft_id = :draft_id ORDER BY event_id"
            ),
            {"draft_id": draft_id},
        ).all()


def expected_team_order() -> list[str]:
    """Built round by round rather than by modular arithmetic, so this is an independent
    statement of the snake and not a mirror of the implementation."""
    order = []
    for round_index in range(ROUNDS):
        slots = list(range(1, TEAMS + 1))
        if round_index % 2:
            slots.reverse()
        order.extend(f"team-{slot:02d}" for slot in slots)
    return order


def test_the_fixture_pool_matches_the_league(player_pool):
    """No kicker: the roster has no K slot, so a K on the board is a K nobody can start."""
    positions = collections.Counter(p["position"] for p in player_pool)
    assert "K" not in positions
    assert set(positions) == {"QB", "RB", "WR", "TE", "DEF"}
    assert len(player_pool) > TOTAL_PICKS


def test_full_snake_draft_reports_the_right_team_at_every_pick(client, engine, player_pool):
    draft_id = "full-walk"
    expected = expected_team_order()
    assert len(expected) == 180

    taken: set[int] = set()
    rosters: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    for pick_no in range(1, TOTAL_PICKS + 1):
        state = board(client, draft_id)
        assert state["pick_no"] == pick_no
        assert state["team_on_the_clock"] == expected[pick_no - 1]
        assert state["is_complete"] is False

        team = state["team_on_the_clock"]
        # First available player this team may legally take — the cap is a real rule,
        # so a walk that ignored it would be refused partway through round 7.
        player = next(
            p
            for p in player_pool
            if p["player_id"] not in taken
            and rosters[team][p["position"]] < CAPS.get(p["position"], math.inf)
        )
        response = take(client, draft_id, player["player_id"])
        assert response.status_code == 201, response.text

        taken.add(player["player_id"])
        rosters[team][player["position"]] += 1

    final = board(client, draft_id)
    assert final["pick_no"] == TOTAL_PICKS + 1
    assert final["is_complete"] is True
    assert final["team_on_the_clock"] is None
    assert final["picks_made"] == TOTAL_PICKS
    assert all(len(roster) == ROUNDS for roster in final["rosters"].values())

    spare = next(p["player_id"] for p in player_pool if p["player_id"] not in taken)
    refused = take(client, draft_id, spare)
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "draft_complete"

    rows = event_rows(engine, draft_id)
    assert len(rows) == TOTAL_PICKS, "the refused 181st pick must have written nothing"
    assert {r.event_type for r in rows} == {"pick"}


def test_undo_reverses_exactly_one_pick(client, engine, player_pool):
    draft_id = "undo"
    first, second = player_pool[0], player_pool[1]

    assert take(client, draft_id, first["player_id"]).status_code == 201
    before = board(client, draft_id)
    assert before["pick_no"] == 2
    assert before["team_on_the_clock"] == "team-02"

    assert take(client, draft_id, second["player_id"]).status_code == 201
    assert board(client, draft_id)["pick_no"] == 3

    undone = client.post(f"/draft/{draft_id}/undo")
    assert undone.status_code == 200, undone.text

    after = board(client, draft_id)
    assert after["pick_no"] == before["pick_no"]
    assert after["team_on_the_clock"] == before["team_on_the_clock"]
    assert after["available_players"] == before["available_players"]
    assert after["rosters"] == before["rosters"]
    # Exactly one pick reversed: the first is still seated.
    assert after["rosters"]["team-01"] == [first["player_id"]]
    assert after["picks_made"] == 1

    # Undo is not a delete (ADR-32): both picks and the undo are still on the log.
    rows = event_rows(engine, draft_id)
    assert [r.event_type for r in rows] == ["pick", "pick", "undo"]
    assert [r.source for r in rows] == ["manual", "manual", "manual"]
    assert rows[2].pick_no == 2 and rows[2].player_id == second["player_id"]

    # The reversed slot is free, so the same team may take someone else at pick 2.
    third = player_pool[2]
    assert take(client, draft_id, third["player_id"]).status_code == 201
    assert board(client, draft_id)["rosters"]["team-02"] == [third["player_id"]]


def test_undo_with_nothing_to_undo_is_refused(client):
    refused = client.post("/draft/empty-draft/undo")
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "nothing_to_undo"


def test_seventh_capped_rb_is_refused(client, engine, player_pool):
    """The issue's check: with RB capped at 6, a seventh RB for that team is refused."""
    draft_id = "rb-cap"
    backs = [p for p in player_pool if p["position"] == "RB"]
    others = [p for p in player_pool if p["position"] == "WR"]
    capped_team = "team-01"

    backs_used, others_used = 0, 0
    # team-01 owns picks 1, 24, 25, 48, 49 and 72 — six RBs by the end of round 6.
    for pick_no in range(1, 73):
        state = board(client, draft_id)
        assert state["pick_no"] == pick_no
        if state["team_on_the_clock"] == capped_team:
            player, backs_used = backs[backs_used], backs_used + 1
        else:
            player, others_used = others[others_used], others_used + 1
        assert take(client, draft_id, player["player_id"]).status_code == 201, pick_no

    state = board(client, draft_id)
    assert state["pick_no"] == 73
    assert state["team_on_the_clock"] == capped_team
    assert len(state["rosters"][capped_team]) == 6

    before_rows = len(event_rows(engine, draft_id))
    seventh = backs[backs_used]
    refused = take(client, draft_id, seventh["player_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "position_cap_exceeded"

    # A refusal writes nothing at all.
    assert len(event_rows(engine, draft_id)) == before_rows
    assert seventh["player_id"] in board(client, draft_id)["available_players"]

    # The cap binds the position, not the pick: an uncapped position is still fine.
    assert take(client, draft_id, others[others_used]["player_id"]).status_code == 201


def test_a_player_cannot_be_drafted_twice(client, player_pool):
    draft_id = "duplicate"
    player = player_pool[0]
    assert take(client, draft_id, player["player_id"]).status_code == 201
    refused = take(client, draft_id, player["player_id"])
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "player_already_drafted"


def test_an_unknown_player_is_refused(client):
    refused = take(client, "unknown-player", 10_000_000)
    assert refused.status_code == 409
    assert refused.json()["detail"]["reason"] == "unknown_player"


def test_reset_clears_only_its_own_draft(client, engine, player_pool):
    kept, cleared = "reset-kept", "reset-cleared"

    for player in player_pool[:2]:
        assert take(client, kept, player["player_id"]).status_code == 201
    for player in player_pool[2:5]:
        assert take(client, cleared, player["player_id"]).status_code == 201

    kept_before = board(client, kept)
    assert kept_before["pick_no"] == 3
    assert len(event_rows(engine, cleared)) == 3

    reset = client.post(f"/draft/{cleared}/reset")
    assert reset.status_code == 200, reset.text

    emptied = board(client, cleared)
    assert emptied["pick_no"] == 1
    assert emptied["team_on_the_clock"] == "team-01"
    assert emptied["picks_made"] == 0
    assert len(emptied["available_players"]) == len(player_pool)
    assert event_rows(engine, cleared) == []

    # The other draft is untouched — the WHERE clause is the only thing guarding it.
    assert board(client, kept) == kept_before
    assert len(event_rows(engine, kept)) == 2

    # And the cleared draft is usable again, which is the point of resetting it.
    assert take(client, cleared, player_pool[0]["player_id"]).status_code == 201
    assert board(client, cleared)["pick_no"] == 2
