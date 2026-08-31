# ADR-33 — `draft_events` is no longer append-only

**Status:** Accepted · 2026-08-30 · issue #3
**Supersedes ADR-26.**

**Context**
specs/draft-assistant.md §9 makes repeated full drafts non-negotiable: the local simulator drives
them as the regression test, and the Yahoo mock rehearsals are the only end-to-end test of the tap
under a real clock.
Neither can re-run without clearing a draft. ADR-26 made `draft_events` reject `UPDATE`, `DELETE`
and `TRUNCATE` from every connection by trigger, so no clearing path existed at all.

ADR-26's stated threat model was an operator opening `psql` by hand mid-draft and amending
history. This is a single-operator tool run on one laptop for one league: the owner is that
operator, and the trigger protects them from nobody but themselves.

**Decision**
Drop both triggers and the `draft_events_append_only()` function. `UPDATE`, `DELETE` and
`TRUNCATE` become ordinary statements. A draft reset is
`DELETE FROM draft_events WHERE draft_id = :draft_id`.

**Consequences**
- **The table has no integrity protection left.** Any statement from any connection can rewrite
  or erase draft history, silently and without a migration. Nothing in the database will now
  catch a bug that issues a wider `DELETE` than it meant to.
- **A reset permanently destroys that draft's history** — the `source` (`tap` | `manual`) trail
  specs/draft-assistant.md §4.4 exists to make reconcilable, and the `received_at` stamps behind
  post-draft latency analysis. Anything to be learned from a rehearsal must be extracted before it
  is reset.
- The reset's `WHERE draft_id = …` is a safety property rather than a filter, and it is the only
  one. A test asserts a second draft survives a reset of the first.
- **ADR-28's blocking consequence is lifted.** It recorded that a null `player_id` could never be
  filled, because `UPDATE` was rejected and no correction opcode exists, and left #3 and #24 to
  settle it. An `UPDATE` is now simply allowed, so identity resolution can reconcile in place and
  no correction event type is needed. ADR-28's decision — the column stays nullable — is
  unchanged.
- ADR-27's surrogate `event_id` keeps its rationale: undo remains an event (ADR-32), so a
  reversed-then-redrafted slot still produces more than one row for a `(draft_id, pick_no)`.
- The downgrade recreates the function and both triggers, so a rollback restores the old
  guarantee rather than leaving a permissive table under a schema version that claims otherwise.
  ADR-34 makes that path conditional: it refuses outright while any undo row exists, because
  dropping `event_type` under restored triggers would reseat reversed picks permanently.
- specs/draft-assistant.md §4.4's "draft_events is append-only" is false as written and is
  corrected there.

**Alternatives rejected**
- **Narrowing the trigger** so only a flagged reset transaction may delete, keeping the
  accident protection for everything else. Rejected by the owner as machinery guarding against a
  second operator who does not exist.
- **A `reset` tombstone event**, with the reducer voiding everything before it. Keeps the table
  append-only and preserves rehearsal history for comparison, at the cost of `pick_no` and
  `team_id` becoming nullable. Rejected: the requirement was that the draft actually be cleared.
- **Reset as a new `draft_id`.** No schema change at all, since `draft_id` is opaque text, but it
  offers no way to re-run a draft that was botched and leaves stale rows with nothing marking
  them.
