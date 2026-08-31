# ADR-40 — nflverse ingests without snapshots

**Status:** Accepted · 2026-08-31 · issue #5

**Context**
ADR-25 made `snapshots.raw_payload` JSONB NOT NULL and left #5 an explicit either/or:
convert nflverse's CSV rows to JSON records before snapshotting, or skip raw snapshotting
for that source. ADR-36 restated the debt. Live measurement (2026-08-31): the three
artifacts this issue ingests total ~10MB of CSV per pull, they are re-downloadable at
stable URLs, and their parse target — `players` — is a dimension table with no
`snapshot_id` column, so there is nothing snapshot-keyed to replay a parser change into.

**Decision**
The nflverse ingester writes no `snapshots` row. It fetches CSV directly with its own
httpx2 client (`follow_redirects=True`; GitHub release assets 302 to a storage host) and
bypasses `fetch_json` entirely. `players` is upserted in place, keyed on `nflverse_id`.

**Consequences**
- specs/draft-assistant.md §4's sentence "Every pull is written as a **dated snapshot —
  never an overwrite**" is now false for nflverse: a re-run overwrites `team`, `position`
  and `bye_week` in place, and what the model saw on an earlier day is not reconstructible
  for the player dimension. No `fetched_at` records when a pull happened, either — which
  specs/draft-assistant.md §4.2 also asks for. The specs/draft-assistant.md §4.4 schema
  sketch already implied this — `players` was never snapshot-keyed — but this decision
  makes it explicit for the whole source.
- Replay-without-refetch (specs/draft-assistant.md §4.2) does not exist for nflverse: a
  parser change re-downloads. The artifacts are overwritten upstream nightly, so the exact
  bytes of an earlier day are unrecoverable. Accepted — nothing downstream reconstructs
  historical player-dimension state.
- `'nflverse'` stays legal in `snapshots.source` (ADR-31's closed set is untouched) but
  nothing writes it; a query grouping snapshots by source sees three sources, not four.
- The nflverse path never touches `fetch_json`, so ADR-36 and ADR-38's snapshot semantics
  do not bind it. `http/client.py`'s claim of being the fetch layer "every ingester goes
  through" narrows to the JSON sources and is reworded in this change.
- #9's verification ("two runs, two `snapshots` rows") must lean on the three JSON
  sources.

**Alternatives rejected**
- **Convert rows to JSON records and snapshot** (ADR-25's other arm). Keeps
  specs/draft-assistant.md §4's promise, at ~10MB+ of JSONB per day for payloads that are
  re-downloadable and have no snapshot-keyed table to replay into. The replay column
  exists to serve re-parsing; for nflverse there is nothing to re-parse into.
- **Snapshot a metadata stub** (`{url, byte_count}`). `raw_payload` would no longer hold
  the body exactly as it arrived, which is the same misrepresentation ADR-36 rejected for
  999 bodies.
