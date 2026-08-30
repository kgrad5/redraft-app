# ADR-30 — `adp` is keyed by snapshot and player, not by source

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§4.4 sketches `adp (snapshot_id, source, player_id, ...)`, which reads as a three-part key. But a
snapshot is one fetch from one source, and `snapshots.source` already pins it, so `source` on an
`adp` row can only ever repeat its snapshot's.

**Decision**
The primary key is `(snapshot_id, player_id)`. `adp.source` is kept as a column because §4.4
names it, but it carries no key role.

**Consequences**
- Two ADP sources cannot be written into one snapshot's rows. They are two snapshots, which is
  what §4.1's per-source fetch already produces.
- `adp.source` is redundant with `snapshots.source` and nothing enforces that they agree. A
  reader should trust `snapshots.source`.

**Alternatives rejected**
- `(snapshot_id, source, player_id)`, the literal sketch — `source` is functionally dependent on
  `snapshot_id`, so the wider key admits rows that contradict their own snapshot.
- Dropping `adp.source` entirely — cleaner, but deviates from a column §4.4 names, and matching
  the spec beat a silent deviation.
