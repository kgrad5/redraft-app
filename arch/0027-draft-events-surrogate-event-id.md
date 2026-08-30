# ADR-27 — `draft_events` keyed by a surrogate `event_id`

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§4.4's sketch implies a natural key on the draft and pick number. §8.2 defines a `PICK_UNDONE`
opcode, and ADR-26 makes the table append-only, so an undo can neither delete nor amend the row
it reverses.

**Decision**
A surrogate `event_id` identity primary key. `(draft_id, pick_no)` carries no uniqueness
constraint.

**Consequences**
- An undo is recordable as a further row, which is what append-only requires.
- Reading the current board stops being a plain select: consumers must reduce the event stream in
  order. #3's state machine owns that reduction.
- §4.4's schema sketch is now incomplete — it lists no `event_id`.

**Alternatives rejected**
- `(draft_id, pick_no)` as the primary key — makes an undo unrepresentable without breaking
  append-only, so the two requirements cannot both hold.
