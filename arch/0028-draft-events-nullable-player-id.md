# ADR-28 — `draft_events.player_id` is a nullable foreign key

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§8.3 requires that recovery never fail hard. A tapped pick arrives carrying whatever identifier
the page had, and §4.3 already concedes that identity matching will not resolve every player.

**Decision**
`player_id` is a nullable foreign key to `players`.

**Consequences**
- An unresolved player never rejects a live pick. The row lands, and the pick is not lost.
- Every consumer must handle a null `player_id`. A null is a reconciliation task, not a missing
  pick, and the unmatched-player report of §4.3 is where it surfaces.
- **It cannot be reconciled in place.** ADR-26 rejects `UPDATE`, so the null can never be filled,
  and §8.2 defines only a `PICK_UNDONE` opcode — there is no correction opcode to express the fix
  as a further row. The same bind catches a duplicate-player merge from §4.3: `player_id` is
  `ON DELETE RESTRICT`, so a losing duplicate can be neither deleted nor re-pointed. Resolving a
  null therefore needs one of two things that do not exist yet — a correction opcode in the event
  stream, or a reconciliation projection built outside this table. **#3 and #24 must settle
  which; until then, a null is a pick that is recorded but not yet attributable.**

**Alternatives rejected**
- `NOT NULL` — would reject a pick mid-draft for exactly the players whose identity is hardest to
  resolve, which is the failure §8.3 forbids, at the moment it costs most.
