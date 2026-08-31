# ADR-34 — The `event_type` migration's downgrade is conditional

**Status:** Accepted · 2026-08-30 · issue #3

**Context**
ADR-32 made `event_type` the only thing that distinguishes an undo from the pick it reverses.
Dropping that column in a downgrade therefore does not lose a flag — it *reseats* every reversed
pick, silently returning to a roster a player who was deliberately taken off the board. ADR-33's
downgrade then recreates the append-only triggers over exactly those rows, putting the damage
beyond `DELETE`. ADR-24 adopted Alembic so that "a bad migration is not a manual repair under a
draft clock"; this down path is precisely that.

**Decision**
`downgrade()` counts `event_type = 'undo'` rows and raises before issuing any DDL. A database
holding undo history cannot be rolled back past this revision until those rows are resolved or
removed by hand.

**Consequences**
- **The rollback path is now conditional.** ADR-24 recorded that `downgrade base` then
  `upgrade head` is a testable property rather than a claim. That holds only for a database with
  no undo history; on one that has run a real draft, `downgrade base` raises instead of rolling
  back.
- The guard runs before any DDL, so a refusal leaves the schema untouched rather than half
  migrated. `tests/test_schema.py::test_downgrade_refuses_while_undo_rows_exist` asserts both the
  raise and that `event_type` survives it.
- The escape hatch is deliberately manual: delete the undo rows — which ADR-33 now permits — and
  accept that the picks they reversed become ordinary picks. Making that automatic would be the
  silent reseating this entry exists to prevent.
- Every later migration that drops a column carrying event semantics inherits the same question.
  This is the first case, not a special one.

**Alternatives rejected**
- **Let the downgrade proceed and lose the distinction**, which is what issue #3's approved plan
  described. It costs nothing to write and corrupts a real draft's history silently — the one
  failure mode a rollback exists to protect against.
- **Convert undo rows to deletions on the way down**, reconstructing the pre-undo board. It
  reverses more than the migration did, cannot be undone by re-upgrading, and would have to guess
  at reversed-then-redrafted slots (ADR-27). A rollback that rewrites history is worse than one
  that refuses.
