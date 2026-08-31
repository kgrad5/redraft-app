# ADR-32 — An undo is a further `draft_events` row, typed by `event_type`

**Status:** Accepted · 2026-08-30 · issue #3

**Context**
specs/draft-assistant.md §7.3 requires manual pick entry to support undo, and its §8.2 defines a
`PICK_UNDONE` opcode. ADR-27 gives `draft_events` a surrogate key precisely so an undo can be
recorded as a further row. But specs/draft-assistant.md §4.4's sketch names no column that tells
one kind of event from another, so as landed by #2 the table cannot express an undo at all: every
row reads as a pick.

**Decision**
Add `event_type TEXT NOT NULL CHECK (event_type IN ('pick', 'undo'))`. An undo is a new row
carrying the `draft_id`, `pick_no`, `player_id` and `team_id` of the pick it reverses. Undo is
last-in-first-out — the only reversible pick is the last one still standing — so the row needs no
reference to the event it reverses. The migration backfills existing rows to `'pick'` and then
drops the server default, so every later insert names its type.

**Consequences**
- specs/draft-assistant.md §4.4's schema sketch is now incomplete a second way: it lists neither
  `event_id` (ADR-27) nor `event_type`.
- The current board is a reduction over the ordered stream, never a plain select.
  `src/redraft/draft/state.py` owns that reduction, and #24's tap must write `event_type` on
  every frame it lands or its picks reduce wrongly.
- The undo row's `player_id` and `team_id` duplicate the reversed pick's, and nothing in the
  database enforces that they agree. The reducer checks it and raises on a stream that disagrees.
- ADR-33 removes the append-only triggers, so undo *could* have been a delete instead. It is not,
  because specs/draft-assistant.md §4.4 states the table exists to make tap-versus-manual
  reconciliation possible and #24 must flag a mis-captured pick rather than silently overwrite
  it. Deleting the reversed pick destroys that evidence before reconciliation runs.
- **ADR-28's open question is settled from a different direction.** A null `player_id` is now
  fillable by `UPDATE` (ADR-33), so no correction event type is needed and `event_type` stays a
  two-value set.

**Alternatives rejected**
- `undone_by` / `undone_at` on the pick row — now possible, since `UPDATE` is permitted, but it
  makes the current board depend on two columns read together rather than one ordered reduction,
  and gives a reversed-then-redrafted slot no clean representation.
- A `reverses_event_id` foreign key on the undo row — explicit, but derivable under
  last-in-first-out undo, and it must be null on every pick row, needing a further check
  constraint to stay honest. #24 may still want it; adding it then is additive.
