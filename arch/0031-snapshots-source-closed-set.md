# ADR-31 — `snapshots.source` is a closed set

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§4.1 names exactly four sources. Nothing in the spec says that list is closed, so closing it is a
decision rather than a transcription.

**Decision**
`CHECK (source IN ('sleeper', 'yahoo', 'ffc', 'nflverse'))` on `snapshots.source`.

**Consequences**
- A typo'd or unregistered source is rejected at write time rather than silently partitioning the
  data. §4.2's throttling and retry paths are where a wrong literal would otherwise slip in, and
  a `WHERE source = 'ffc'` that quietly returns nothing is the worst version of that failure.
- A fifth source needs a migration, not a config change. That is the intended cost.
- **The constraint is not applied consistently.** `adp.source` and `id_exceptions.source` carry no
  such check, so the same typo is accepted there. That inconsistency is unresolved and is a
  follow-on issue, not something this one settles.

**Alternatives rejected**
- No constraint — nothing else in the ingestion path catches a misspelled source, and §4.2 offers
  no other place to put the check.
