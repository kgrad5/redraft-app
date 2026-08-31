# ADR-37 — The provider seams are ingest-shaped; `PickFeed` deliberately is not

**Status:** Accepted · 2026-08-30 · issue #4

**Context**
specs/draft-assistant.md §1.1 requires three seams — `ProjectionProvider`, `ADPProvider` and
`PickFeed` — so that a licensed source is later "a swap, not a rewrite". It says nothing about what
they do, and #5, #6, #7 and #9 all bind to the answer before any of them exists.

**Decision**
`ProjectionProvider` and `ADPProvider` each expose one method, `ingest(connection) -> IngestResult`.
The implementation fetches, snapshots, parses and writes its own rows; #9's daily job only chooses
which providers to run and what to do when one fails. `PickFeed` is not ingest-shaped: it exposes
`events() -> Iterable[PickEvent]` and writes nothing.

**Consequences**
- A source keeps its own key space all the way to the write, which is what ADR-29 requires. Nothing
  outside `players` is keyed by an external id, so identity resolution (#8) becomes a step inside an
  ingester rather than a shape imposed on all four sources at the seam.
- A provider cannot be unit-tested without a database. That is already this repo's house pattern —
  `tests/conftest.py` migrates a throwaway Postgres per module — so it costs no new machinery.
- #9 shrinks to a loop and a failure policy. It cannot batch two sources into one snapshot, which
  ADR-30 forbids in any case.
- **`PickFeed`'s asymmetry mirrors ADR-35's.** An ingester decides what a fetch means; a feed reports
  what Yahoo already did, so a `PickEvent` carries its own `pick_no` and `team_id` rather than
  deriving them. The two ingest seams own their writes and `PickFeed` owns none, because #24 has to
  reconcile before it writes.
- `PickFeed.events()` is synchronous, so #24's WebSocket receiver drains frames behind a sync
  boundary rather than exposing an async iterator. #24 is the only implementer, so this is cheap to
  revisit — but it is a constraint #24 inherits rather than one it chooses.

**Alternatives rejected**
- **Row-returning seams** — `fetch(connection) -> Sequence[AdpRow]`, with the caller writing. Every
  INSERT lands in one place and providers test without a database, but it forces four sources into
  one row shape before any of them exists and moves each source's key handling into #9, which is the
  coupling ADR-29 pushes downstream.
- **A marker `PickFeed` with no methods**, leaving every question to #24. Honest about what is
  unknown, but a seam with no method is a comment rather than a seam, and
  specs/draft-assistant.md §1.1 asks for three of them in place from day one.
