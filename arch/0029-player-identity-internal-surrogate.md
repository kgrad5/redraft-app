# ADR-29 — Player identity is an internal surrogate, external ids are satellites

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
ADR-12 makes the nflverse crosswalk the identity backbone, which reads as though the nflverse id
were canonical. §4.3 concedes it is not: name matching and a hand-maintained exception file cover
the remainder, and a player can arrive from Sleeper or Yahoo before any nflverse id is known.

**Decision**
`players.player_id` is an internal BIGINT identity. `nflverse_id`, `sleeper_id` and
`yahoo_num_id` are nullable unique satellites. Every other table's foreign key points at
`player_id`.

**Consequences**
- A player can exist before any external id resolves, which is exactly what §4.3's second and
  third matching tiers require.
- An external id can be corrected without rewriting four tables of foreign keys.
- Nothing outside `players` can be keyed by an external id, so every ingest pays a lookup, and
  `id_exceptions` exists to carry the mappings the crosswalk misses.

**Alternatives rejected**
- `nflverse_id` as the primary key — leaves no row for a player the crosswalk does not cover,
  which §4.3 says happens every season, and a corrected id would rewrite every foreign key.
