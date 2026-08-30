# ADR-25 — JSONB for `snapshots.raw_payload`

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§4.2 requires the raw response stored beside the parsed rows so a parser change can be replayed
without re-fetching. Three of the four sources in §4.1 are JSON; nflverse is CSV and parquet.

**Decision**
`raw_payload` is `JSONB NOT NULL`.

**Consequences**
- Sleeper, Yahoo v3 and FFC store natively and stay queryable, so a replay can select into the
  payload instead of reparsing an opaque blob.
- It closes a door for nflverse: #5 must either convert nflverse rows to JSON records before
  snapshotting, or skip raw snapshotting for that source. nflverse is the source where this
  costs least — it is a stable versioned artifact that can simply be downloaded again.

**Alternatives rejected**
- `TEXT` or `BYTEA` — accepts every source, but makes the payload opaque and defeats the
  replay-without-refetch purpose the column exists for.
