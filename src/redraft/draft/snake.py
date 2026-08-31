"""Snake draft order (specs/draft-assistant.md §8.1).

Pure arithmetic over 1-based pick numbers. This league has no traded picks
(specs/draft-assistant.md §1), so a draft slot maps to one team for the whole draft and
the order never changes.
"""


def total_picks(teams: int, rounds: int) -> int:
    if teams < 1 or rounds < 1:
        raise ValueError(f"a draft needs at least one team and one round, got {teams}x{rounds}")
    return teams * rounds


def slot_on_the_clock(pick_no: int, teams: int) -> int:
    """The 1-based draft slot that owns `pick_no`.

    Odd rounds run forwards and even rounds back, so the slot at each turn picks twice
    in a row — slot 12 takes picks 12 and 13 in a twelve-team draft.

    Whether `pick_no` is past the *end* of a draft is not decidable here, since that
    depends on the round count; `DraftState` owns that check.
    """
    if teams < 1:
        raise ValueError(f"a draft needs at least one team, got {teams}")
    if pick_no < 1:
        raise ValueError(f"pick numbers are 1-based, got {pick_no}")

    round_index, slot_in_round = divmod(pick_no - 1, teams)
    if round_index % 2:
        return teams - slot_in_round
    return slot_in_round + 1
