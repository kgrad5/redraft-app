"""Snake arithmetic and the event-stream reduction (specs/draft-assistant.md §8.1, ADR-27, ADR-32).

Pure: no database. The reduction is the only way to read the board, so these are the
tests that decide whether the board is right.
"""

import pytest

from redraft.draft.snake import slot_on_the_clock, total_picks
from redraft.draft.state import (
    DraftComplete,
    DraftSettings,
    Event,
    MalformedEventStream,
    NothingToUndo,
    PlayerAlreadyDrafted,
    PositionCapExceeded,
    UnknownPlayer,
    reduce_events,
)

TEAMS_12 = tuple(f"team-{i:02d}" for i in range(1, 13))
# 1 QB, 2 RB, 3 WR, 1 TE, 1 W/R/T, 1 DEF and 6 bench — fifteen slots, no kicker.
ROUNDS = 15


def settings_12(caps=None) -> DraftSettings:
    """This league: twelve teams, fifteen rounds because the roster is fifteen deep."""
    return DraftSettings(
        draft_id="d-12", team_ids=TEAMS_12, rounds=ROUNDS, position_caps=caps or {"RB": 6}
    )


def settings_2(caps=None) -> DraftSettings:
    """A two-team draft, so a cap can be reached in a handful of hand-built events."""
    return DraftSettings(
        draft_id="d-2", team_ids=("team-01", "team-02"), rounds=8, position_caps=caps or {}
    )


def pick(event_id: int, pick_no: int, team_id: str, player_id: int) -> Event:
    return Event(
        event_id=event_id,
        event_type="pick",
        pick_no=pick_no,
        team_id=team_id,
        player_id=player_id,
    )


def undo(event_id: int, pick_no: int, team_id: str, player_id: int) -> Event:
    return Event(
        event_id=event_id,
        event_type="undo",
        pick_no=pick_no,
        team_id=team_id,
        player_id=player_id,
    )


# ---------------------------------------------------------------- snake arithmetic


def test_total_picks():
    assert total_picks(12, ROUNDS) == 180


@pytest.mark.parametrize(
    ("pick_no", "expected_slot"),
    [
        # Round 1 runs forwards, round 2 back, round 3 forwards again.
        (1, 1),
        (6, 6),
        (12, 12),
        (13, 12),
        (18, 7),
        (24, 1),
        (25, 1),
        (36, 12),
        # Round 15 is an odd round, so the draft runs forwards and ends on slot 12.
        (169, 1),
        (180, 12),
    ],
)
def test_slot_on_the_clock(pick_no, expected_slot):
    assert slot_on_the_clock(pick_no, 12) == expected_slot


def test_the_turn_gives_a_slot_two_picks_in_a_row():
    """The whole point of a snake: slot 12 picks at 12 and 13, slot 1 at 24 and 25."""
    assert slot_on_the_clock(12, 12) == slot_on_the_clock(13, 12) == 12
    assert slot_on_the_clock(24, 12) == slot_on_the_clock(25, 12) == 1


def test_every_slot_picks_once_per_round():
    for round_index in range(ROUNDS):
        first = round_index * 12 + 1
        slots = [slot_on_the_clock(n, 12) for n in range(first, first + 12)]
        assert sorted(slots) == list(range(1, 13))


@pytest.mark.parametrize("pick_no", [0, -1])
def test_slot_on_the_clock_rejects_a_pick_before_the_draft(pick_no):
    with pytest.raises(ValueError):
        slot_on_the_clock(pick_no, 12)


# ------------------------------------------------------------------- the reduction


def test_an_empty_stream_is_the_start_of_the_draft():
    state = reduce_events(settings_12(), [], {})
    assert state.next_pick_no == 1
    assert state.team_on_the_clock == "team-01"
    assert state.drafted_player_ids == frozenset()
    assert not state.is_complete


def test_picks_seat_players_and_advance_the_clock():
    events = [pick(1, 1, "team-01", 100), pick(2, 2, "team-02", 200)]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR"})

    assert state.next_pick_no == 3
    assert state.team_on_the_clock == "team-03"
    assert state.roster("team-01") == (100,)
    assert state.roster("team-02") == (200,)
    assert state.drafted_player_ids == frozenset({100, 200})


def test_undo_lifts_the_last_pick_only():
    events = [
        pick(1, 1, "team-01", 100),
        pick(2, 2, "team-02", 200),
        undo(3, 2, "team-02", 200),
    ]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR"})

    assert state.next_pick_no == 2
    assert state.team_on_the_clock == "team-02"
    assert state.roster("team-01") == (100,)
    assert state.roster("team-02") == ()
    assert state.drafted_player_ids == frozenset({100})


def test_undo_then_redraft_reuses_the_slot():
    """ADR-27's reason for a surrogate key: (draft_id, pick_no) is not unique."""
    events = [
        pick(1, 1, "team-01", 100),
        pick(2, 2, "team-02", 200),
        undo(3, 2, "team-02", 200),
        pick(4, 2, "team-02", 201),
    ]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR", 201: "TE"})

    assert state.next_pick_no == 3
    assert state.roster("team-02") == (201,)
    assert state.drafted_player_ids == frozenset({100, 201})


def test_consecutive_undos_walk_back_in_order():
    events = [
        pick(1, 1, "team-01", 100),
        pick(2, 2, "team-02", 200),
        undo(3, 2, "team-02", 200),
        undo(4, 1, "team-01", 100),
    ]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR"})

    assert state.next_pick_no == 1
    assert state.drafted_player_ids == frozenset()


def test_an_undo_of_something_other_than_the_last_pick_raises():
    """A malformed stream must fail loudly, not reduce to a plausible wrong board."""
    events = [
        pick(1, 1, "team-01", 100),
        pick(2, 2, "team-02", 200),
        undo(3, 1, "team-01", 100),
    ]
    with pytest.raises(MalformedEventStream):
        reduce_events(settings_12(), events, {100: "RB", 200: "WR"})


def test_an_undo_with_nothing_standing_raises():
    with pytest.raises(MalformedEventStream):
        reduce_events(settings_12(), [undo(1, 1, "team-01", 100)], {100: "RB"})


def test_an_undo_naming_the_wrong_player_raises():
    """ADR-32: the undo row repeats the pick's player and no constraint makes them agree,
    so the reducer is the only thing that can catch a mis-captured undo."""
    events = [pick(1, 1, "team-01", 100), undo(2, 1, "team-01", 999)]
    with pytest.raises(MalformedEventStream, match="player 999"):
        reduce_events(settings_12(), events, {100: "RB", 999: "WR"})


def test_an_undo_naming_the_wrong_team_raises():
    events = [pick(1, 1, "team-01", 100), undo(2, 1, "team-07", 100)]
    with pytest.raises(MalformedEventStream, match="team-07"):
        reduce_events(settings_12(), events, {100: "RB"})


def test_an_unresolved_pick_holds_its_slot_without_taking_a_player():
    """ADR-28: a tapped pick whose player did not resolve is recorded but not
    attributable. It must occupy the slot and mark nobody unavailable — a None in the
    drafted set would leave the real player draftable and seat them twice once identity
    resolution fills the null in."""
    events = [
        pick(1, 1, "team-01", 100),
        Event(event_id=2, event_type="pick", pick_no=2, team_id="team-02", player_id=None),
    ]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR"})

    assert state.picks_made == 2
    assert state.next_pick_no == 3
    assert state.team_on_the_clock == "team-03"
    assert None not in state.drafted_player_ids
    assert state.drafted_player_ids == frozenset({100})
    assert state.available_player_ids == frozenset({200})


def test_events_are_reduced_in_event_id_order_not_arrival_order():
    events = [
        pick(2, 2, "team-02", 200),
        undo(3, 2, "team-02", 200),
        pick(1, 1, "team-01", 100),
    ]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR"})
    assert state.roster("team-01") == (100,)
    assert state.roster("team-02") == ()


# ------------------------------------------------------------------------ refusals


def test_unknown_player_is_refused():
    state = reduce_events(settings_12(), [], {100: "RB"})
    with pytest.raises(UnknownPlayer):
        state.validate_pick(999)


def test_an_already_drafted_player_is_refused():
    events = [pick(1, 1, "team-01", 100)]
    state = reduce_events(settings_12(), events, {100: "RB", 200: "WR"})
    with pytest.raises(PlayerAlreadyDrafted):
        state.validate_pick(100)


def full_stream(settings: DraftSettings) -> list[Event]:
    """Every pick of a draft, player n taken at pick n, each by the team on the clock."""
    teams = settings.team_ids
    return [
        pick(n, n, teams[slot_on_the_clock(n, len(teams)) - 1], n)
        for n in range(1, total_picks(len(teams), settings.rounds) + 1)
    ]


def test_a_pick_past_the_end_of_the_draft_is_refused():
    settings = settings_2()
    events = full_stream(settings)
    positions = {n: "WR" for n in range(1, len(events) + 2)}
    state = reduce_events(settings, events, positions)

    # A two-team, eight-round draft is 16 picks; the seventeenth has no slot.
    assert len(events) == 16
    assert state.is_complete
    assert state.team_on_the_clock is None
    with pytest.raises(DraftComplete):
        state.validate_pick(17)


def test_the_seventh_capped_rb_is_refused():
    caps = {"RB": 2}
    positions = {1: "RB", 2: "WR", 3: "WR", 4: "RB", 5: "RB"}
    # Two teams: team-01 owns picks 1 and 4, so it holds two RBs by pick 5.
    events = [
        pick(1, 1, "team-01", 1),
        pick(2, 2, "team-02", 2),
        pick(3, 3, "team-02", 3),
        pick(4, 4, "team-01", 4),
    ]
    state = reduce_events(settings_2(caps), events, positions)

    assert state.team_on_the_clock == "team-01"
    with pytest.raises(PositionCapExceeded):
        state.validate_pick(5)


def test_a_cap_binds_one_team_only():
    caps = {"RB": 1}
    positions = {1: "RB", 2: "RB"}
    state = reduce_events(settings_2(caps), [pick(1, 1, "team-01", 1)], positions)

    # team-02 is on the clock and holds no RB, so the cap does not bind it.
    assert state.team_on_the_clock == "team-02"
    state.validate_pick(2)


def test_an_uncapped_position_is_never_refused():
    positions = {n: "WR" for n in range(1, 6)}
    events = [pick(1, 1, "team-01", 1), pick(2, 2, "team-02", 2), pick(3, 3, "team-02", 3)]
    state = reduce_events(settings_2({"RB": 1}), events, positions)
    state.validate_pick(4)


def test_undo_with_nothing_standing_is_refused():
    state = reduce_events(settings_12(), [], {})
    with pytest.raises(NothingToUndo):
        state.validate_undo()
