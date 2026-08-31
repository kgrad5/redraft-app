# ADR-38 — `fetch_json` writes on the caller's transaction and never commits

**Status:** Accepted · 2026-08-30 · issue #4

**Context**
specs/draft-assistant.md §4.2 requires the raw response recorded "so a parser change can be
replayed without re-fetching". `fetch_json` inserts the snapshot on the `Connection` its caller
passes and issues no commit. That is the ordinary SQLAlchemy idiom and what #4's approved plan
specified, but its interaction with the replay promise was left unrecorded.

**Decision**
The caller owns the transaction. `fetch_json` writes the snapshot on the connection it is handed
and never commits it, rolls it back, or opens one of its own. A caller needing the payload kept
regardless of what happens next commits it itself.

**Consequences**
- **The replay guarantee does not survive a failed parse.** A parse that raises inside the
  caller's transaction rolls the snapshot back with it, so the payload is missing in exactly the
  case specs/draft-assistant.md §4.2's replay-without-refetch exists to serve — and the re-fetch it
  forces may meet the 999 of specs/draft-assistant.md §2.1. #9 owns the answer: an ingester wanting
  the payload durable must commit the snapshot before parsing.
- ADR-36 records what a *throttle* leaves behind. This records what a *crash* leaves behind, which
  is the larger omission.
- A sustained throttle holds the caller's transaction open for up to `(attempts - 1) * backoff` —
  six minutes on the defaults — along with any locks it has taken. The same blocking call from an
  async handler would stall the event loop.
- One transaction spans the fetch and the write, so no snapshot outlives an ingest whose rows were
  rolled back. Snapshot and rows stay consistent, which is precisely the property the alternative
  gives up.

**Alternatives rejected**
- **Open a second connection and commit the snapshot immediately.** The payload then survives any
  later failure, which is what specs/draft-assistant.md §4.2 wants most. Rejected for #4: it puts
  an independent connection and commit inside a function whose caller already holds one, and
  decouples the snapshot from the rows it belongs to, so a snapshot could exist for an ingest that
  never landed. #9 can still choose this per provider — the seam does not forbid it.
- **Leave it undocumented**, on the grounds that "caller owns the transaction" is what the
  signature already says to a SQLAlchemy reader. Rejected: the idiom is obvious, but its collision
  with specs/draft-assistant.md §4.2's replay promise is not, and that collision is the whole
  reason the column exists.
