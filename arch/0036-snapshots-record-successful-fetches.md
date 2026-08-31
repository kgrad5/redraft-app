# ADR-36 — `snapshots` records successful fetches, not an HTTP transcript

**Status:** Accepted · 2026-08-30 · issue #4

**Context**
Issue #4 asks that every response be persisted raw before parsing. ADR-25 made
`snapshots.raw_payload` `JSONB NOT NULL`, and specs/draft-assistant.md §2.1 establishes that the
throttle response — HTTP 999 — carries a plain-text body, not JSON. A plain-text body cannot be
stored in a JSONB column, so "every response" and ADR-25 cannot both hold as written.

**Decision**
Only a response that is about to be parsed is snapshotted. HTTP 999 is retried and writes no
`snapshots` row, and neither does any other non-success status. `raw_payload` holds the decoded
body exactly as it arrived, `service` envelope included. JSON decoding is what makes the column's
type possible and is not parsing; unwrapping and row extraction are, and both happen after the
write.

**Consequences**
- specs/draft-assistant.md §4.2 asks for the raw response "alongside the parsed rows". A throttled
  request produces no rows, so it correctly produces no snapshot either.
- **A throttling episode leaves no trace in the database.** How often 999 fired, and for how long,
  cannot be reconstructed by query — it exists only in process logs. specs/draft-assistant.md §10
  schedules "999 backoff exercised" for days 8–10, so #26's rehearsal harness has to measure it
  from the process rather than from `snapshots`.
- Storing the body enveloped keeps an envelope change replayable, which is the bug class
  specs/draft-assistant.md §2.1 warns about. A snapshot taken after unwrapping would have hidden
  exactly that.
- ADR-25's open consequence for nflverse — convert its rows to JSON records or skip raw
  snapshotting — is untouched and still owed by #5.

**Alternatives rejected**
- **Wrap the 999 text in a JSON object** so it fits the column. It preserves the transcript, but
  seeds `snapshots` with rows no parser will ever read and that every source-filtered query must
  then exclude — and ADR-31 closed `snapshots.source` to the four real sources, so such a row would
  have to claim to be one of them.
- **Widen `raw_payload` to TEXT.** ADR-25 rejected this for the replay-queryability the column
  exists for; reversing it to store error bodies trades that purpose for a diagnostic the process
  log already carries.
