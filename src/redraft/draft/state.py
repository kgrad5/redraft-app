"""The draft state machine (specs/draft-assistant.md §8.1).

Manual entry is the substrate, not the fallback: every feed writes the same events, so
the board is always a reduction over the ordered event stream rather than a select
(ADR-27). A reset needs no handling here — its rows are deleted (ADR-33), so an empty
stream *is* the reset state.

State is rebuilt from the log on every read. At most a few hundred events, and it makes
divergence between memory and the log impossible; a cache belongs to #18 if the latency
budget ever needs one.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from redraft.draft.snake import slot_on_the_clock, total_picks


class DraftError(Exception):
    """Base for every refusal. `reason` is the machine-readable code the API puts on the wire."""

    reason = "draft_error"


class DraftComplete(DraftError):
    reason = "draft_complete"


class UnknownPlayer(DraftError):
    reason = "unknown_player"


class PlayerAlreadyDrafted(DraftError):
    reason = "player_already_drafted"


class PositionCapExceeded(DraftError):
    reason = "position_cap_exceeded"


class NothingToUndo(DraftError):
    reason = "nothing_to_undo"


class MalformedEventStream(DraftError):
    reason = "malformed_event_stream"


@dataclass(frozen=True, slots=True)
class DraftSettings:
    """The shape of one draft.

    Not stored: specs/draft-assistant.md §4.4 has no `drafts` table, so this is supplied
    by the caller. `position_caps` maps a position to the most a single team may roster
    (specs/draft-assistant.md §2.2).
    Yahoo reports these as strings (`{"RB": "6"}`); coercing is the config loader's job
    in #10, not this module's.
    """

    draft_id: str
    team_ids: tuple[str, ...]
    rounds: int
    position_caps: Mapping[str, int]

    @property
    def teams(self) -> int:
        return len(self.team_ids)

    @property
    def total_picks(self) -> int:
        return total_picks(self.teams, self.rounds)

    def team_for_pick(self, pick_no: int) -> str:
        return self.team_ids[slot_on_the_clock(pick_no, self.teams) - 1]


@dataclass(frozen=True, slots=True)
class Event:
    """One `draft_events` row, as far as the reduction is concerned."""

    event_id: int
    event_type: str
    pick_no: int
    team_id: str
    player_id: int | None


@dataclass(frozen=True, slots=True)
class Pick:
    pick_no: int
    team_id: str
    player_id: int


class DraftState:
    """The board after reducing a draft's events. Immutable; rebuild to advance."""

    def __init__(
        self,
        settings: DraftSettings,
        picks: Sequence[Pick],
        positions: Mapping[int, str],
    ) -> None:
        self.settings = settings
        self.picks = tuple(picks)
        self.positions = positions

    @property
    def picks_made(self) -> int:
        return len(self.picks)

    @property
    def next_pick_no(self) -> int:
        return self.picks_made + 1

    @property
    def is_complete(self) -> bool:
        return self.picks_made >= self.settings.total_picks

    @property
    def team_on_the_clock(self) -> str | None:
        if self.is_complete:
            return None
        return self.settings.team_for_pick(self.next_pick_no)

    @property
    def drafted_player_ids(self) -> frozenset[int]:
        return frozenset(pick.player_id for pick in self.picks)

    @property
    def available_player_ids(self) -> frozenset[int]:
        return frozenset(self.positions) - self.drafted_player_ids

    @property
    def last_pick(self) -> Pick | None:
        return self.picks[-1] if self.picks else None

    def roster(self, team_id: str) -> tuple[int, ...]:
        return tuple(pick.player_id for pick in self.picks if pick.team_id == team_id)

    def rosters(self) -> dict[str, list[int]]:
        return {team_id: list(self.roster(team_id)) for team_id in self.settings.team_ids}

    def validate_pick(self, player_id: int) -> None:
        """Raise if the team on the clock may not take this player. Silence means yes."""
        if self.is_complete:
            raise DraftComplete(
                f"all {self.settings.total_picks} picks of {self.settings.draft_id} are made"
            )
        if player_id not in self.positions:
            raise UnknownPlayer(f"no player {player_id} in the pool")
        if player_id in self.drafted_player_ids:
            raise PlayerAlreadyDrafted(f"player {player_id} is already on a roster")

        position = self.positions[player_id]
        cap = self.settings.position_caps.get(position)
        if cap is None:
            return

        team_id = self.team_on_the_clock
        held = sum(1 for pid in self.roster(team_id) if self.positions.get(pid) == position)
        if held >= cap:
            # Yahoo enforces position_draft_caps itself, so this refusal records reality
            # rather than overriding the drafter — ADR-16's soft guardrails are about
            # recommendations, not about picks the draft room will reject.
            raise PositionCapExceeded(
                f"{team_id} already holds {held} at {position}, capped at {cap}"
            )

    def validate_undo(self) -> None:
        if self.last_pick is None:
            raise NothingToUndo(f"no pick to undo in {self.settings.draft_id}")


def reduce_events(
    settings: DraftSettings,
    events: Iterable[Event],
    positions: Mapping[int, str],
) -> DraftState:
    """Fold the event stream into a board, in `event_id` order.

    Undo is last-in-first-out: an undo naming anything but the pick still standing means
    the stream is wrong, and failing here is better than reducing to a board that looks
    plausible.
    """
    picks: list[Pick] = []
    for event in sorted(events, key=lambda event: event.event_id):
        if event.event_type == "pick":
            picks.append(
                Pick(pick_no=event.pick_no, team_id=event.team_id, player_id=event.player_id)
            )
        elif event.event_type == "undo":
            standing = picks[-1] if picks else None
            if standing is None:
                raise MalformedEventStream(
                    f"event {event.event_id} undoes pick {event.pick_no} with nothing standing"
                )
            if standing.pick_no != event.pick_no:
                raise MalformedEventStream(
                    f"event {event.event_id} undoes pick {event.pick_no}, "
                    f"but pick {standing.pick_no} is the one standing"
                )
            picks.pop()
        else:
            raise MalformedEventStream(
                f"event {event.event_id} has unknown event_type {event.event_type!r}"
            )

    return DraftState(settings, picks, positions)
