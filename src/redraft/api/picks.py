"""Manual pick entry, undo and reset (specs/draft-assistant.md §7.3, §8.1).

Manual entry is the substrate every other feed accelerates, so these endpoints are the
door the tap (#24) will later write through as well — with `source='tap'` instead of
`'manual'`, and supplying its own pick number and team, because it reports what Yahoo
already did rather than deciding it.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from redraft.db.session import engine
from redraft.draft.state import DraftError, DraftSettings, DraftState, Event, reduce_events

router = APIRouter(prefix="/draft", tags=["draft"])

# Placeholder until #10 transcribes the real league settings from the league page.
# Fifteen rounds because the roster is fifteen deep (specs/draft-assistant.md §2.2):
# 1 QB, 2 RB, 3 WR, 1 TE, 1 W/R/T, 1 DEF and 6 bench, with no kicker.
# specs/draft-assistant.md Appendix B item 3 is still open, so the cap below is the one
# observed in a sampled live league, not necessarily this one's.
DEFAULT_TEAM_IDS = tuple(f"team-{slot:02d}" for slot in range(1, 13))
DEFAULT_ROUNDS = 15
DEFAULT_POSITION_CAPS = {"RB": 6}

INSERT_EVENT = text(
    "INSERT INTO draft_events (draft_id, pick_no, player_id, team_id, source, event_type) "
    "VALUES (:draft_id, :pick_no, :player_id, :team_id, 'manual', :event_type)"
)


def get_connection():
    """One transaction per request: the read that decides a pick and the write that
    records it commit together, or neither does.

    That is atomicity, not isolation. Under READ COMMITTED two overlapping requests can
    both read pick N and both insert it, and ADR-27 deliberately leaves
    `(draft_id, pick_no)` without a unique index, so nothing would reject the second.
    Harmless while the only writer is one person clicking. #24's tap writing while a
    manual entry is in flight is where it stops being harmless and wants an advisory
    lock on `draft_id`.
    """
    with engine.begin() as connection:
        yield connection


def draft_settings(draft_id: str) -> DraftSettings:
    """The draft's shape. specs/draft-assistant.md §4.4 has no `drafts` table, so this
    is supplied rather than stored; #10 will build it from `league_config` without
    changing a call site."""
    return DraftSettings(
        draft_id=draft_id,
        team_ids=DEFAULT_TEAM_IDS,
        rounds=DEFAULT_ROUNDS,
        position_caps=DEFAULT_POSITION_CAPS,
    )


ConnectionDep = Annotated[Connection, Depends(get_connection)]
SettingsDep = Annotated[DraftSettings, Depends(draft_settings)]


class PickRequest(BaseModel):
    """Only the player. The pick number and the team are derived from the board — a
    manual entry is someone clicking a player, and letting the caller name the team
    would give the draft two sources of truth."""

    player_id: int


def load_state(connection: Connection, settings: DraftSettings) -> DraftState:
    positions = {
        row.player_id: row.position
        for row in connection.execute(text("SELECT player_id, position FROM players"))
    }
    events = [
        Event(
            event_id=row.event_id,
            event_type=row.event_type,
            pick_no=row.pick_no,
            team_id=row.team_id,
            player_id=row.player_id,
        )
        for row in connection.execute(
            text(
                "SELECT event_id, event_type, pick_no, team_id, player_id FROM draft_events "
                "WHERE draft_id = :draft_id ORDER BY event_id"
            ),
            {"draft_id": settings.draft_id},
        )
    ]
    return reduce_events(settings, events, positions)


def view(state: DraftState) -> dict:
    return {
        "draft_id": state.settings.draft_id,
        "pick_no": state.next_pick_no,
        "team_on_the_clock": state.team_on_the_clock,
        "is_complete": state.is_complete,
        "picks_made": state.picks_made,
        "rosters": state.rosters(),
        "available_players": sorted(state.available_player_ids),
    }


def refusal(error: DraftError) -> HTTPException:
    return HTTPException(status_code=409, detail={"reason": error.reason, "message": str(error)})


def current_state(connection: Connection, settings: DraftSettings) -> DraftState:
    """`load_state`, but a stream the reducer refuses is a 409 carrying its reason
    rather than a bare 500.

    Reachable since ADR-33 removed the append-only triggers: a `DELETE` or `UPDATE`
    issued by hand in `psql` can leave a stream that no longer reduces. Mid-draft, a
    500 with no machine-readable cause is the worst possible way to find that out.
    """
    try:
        return load_state(connection, settings)
    except DraftError as error:
        raise refusal(error) from error


@router.get("/{draft_id}")
def read_draft(connection: ConnectionDep, settings: SettingsDep) -> dict:
    return view(current_state(connection, settings))


@router.post("/{draft_id}/picks", status_code=201)
def make_pick(body: PickRequest, connection: ConnectionDep, settings: SettingsDep) -> dict:
    state = current_state(connection, settings)
    try:
        state.validate_pick(body.player_id)
    except DraftError as error:
        raise refusal(error) from error

    connection.execute(
        INSERT_EVENT,
        {
            "draft_id": settings.draft_id,
            "pick_no": state.next_pick_no,
            "player_id": body.player_id,
            "team_id": state.team_on_the_clock,
            "event_type": "pick",
        },
    )
    return view(current_state(connection, settings))


@router.post("/{draft_id}/undo")
def undo_pick(connection: ConnectionDep, settings: SettingsDep) -> dict:
    state = current_state(connection, settings)
    try:
        state.validate_undo()
    except DraftError as error:
        raise refusal(error) from error

    # ADR-32: the undo is a further row naming the pick it reverses, not a delete. The
    # reversed pick stays on the log, which is what #24 reconciles a mis-capture against.
    reversed_pick = state.last_pick
    connection.execute(
        INSERT_EVENT,
        {
            "draft_id": settings.draft_id,
            "pick_no": reversed_pick.pick_no,
            "player_id": reversed_pick.player_id,
            "team_id": reversed_pick.team_id,
            "event_type": "undo",
        },
    )
    return view(current_state(connection, settings))


@router.post("/{draft_id}/reset")
def reset_draft(connection: ConnectionDep, settings: SettingsDep) -> dict:
    """Clear a draft so it can be run again (specs/draft-assistant.md §9).

    ADR-33 removed the trigger that used to refuse this outright, so the WHERE clause
    below is the only thing standing between one reset and every draft in the table.
    """
    connection.execute(
        text("DELETE FROM draft_events WHERE draft_id = :draft_id"),
        {"draft_id": settings.draft_id},
    )
    return view(current_state(connection, settings))
