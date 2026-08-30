# ADR-26 — `draft_events` is append-only by trigger, not by permission

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§4.4 states that `draft_events` is append-only but not how. The guarantee has to hold against
every connection, including a `psql` session opened by hand mid-draft to fix something.

**Decision**
A `BEFORE UPDATE OR DELETE` row trigger and a `BEFORE TRUNCATE` statement trigger, both raising,
installed by the migration. Downgrade drops both triggers and the function before the tables.

**Consequences**
- The guarantee travels with the migration and binds every connection and every role. `TRUNCATE`
  is covered, which a permission grant would have missed.
- `DROP TABLE` is unaffected, so downgrade still works. The function is not dropped with the
  table that uses it, so downgrade must drop it explicitly — a missed drop surfaces as a failed
  re-upgrade, since the `CREATE FUNCTION` carries no `OR REPLACE`.

**Alternatives rejected**
- `REVOKE UPDATE, DELETE` — needs a second database role this single-user tool does not have,
  and would not bind the owner connection the app actually uses.
