# ADR-50 — The exception file is checked in and names players by name, not by id

**Status:** Accepted · 2026-08-31 · issue #8

**Context**
specs/draft-assistant.md §4.3 calls the third tier "a small hand-maintained exception file ...
checked into the repo", while specs/draft-assistant.md §4.4 sketches an `id_exceptions (source, source_key, player_id, note)`
table, which `src/redraft/db/models.py` declares and the live database holds, empty, unread by any
code. Both cannot be the source of truth. Naming the target is a second question: `player_id` is an
`Identity()` surrogate reissued on any rebuild, and `tests/conftest.py`'s `player_pool` already says
why that matters — "a fixture that pinned them would encode exactly the assumption the ADR rejects".

**Decision**
`data/id_exceptions.yaml`, checked into the repo, is the source of truth. Each entry keys on
`(source, source_key)` and names its target by `(full_name, position)` — the canonical `players`
spelling. `note` is required. The `id_exceptions` table is dropped by migration. The file is
consulted before the automatic tiers, because a tier running after them sees only what they already
failed on and so could never override anything.

**Consequences**
- **specs/draft-assistant.md §4.4's schema sketch is now wrong**: `id_exceptions` is not a table.
- The file is reviewable in a git diff, which is what makes "no fuzzy matching without a review
  step" enforceable, and it survives a database rebuild where the table would not.
- Naming the target by `(full_name, position)` rather than `nflverse_id` keeps the file usable
  against every fixture pool in the repo — all three insert a NULL `nflverse_id` — and against a
  manually-created player, which ADR-29 says tiers two and three exist for. The cost is that an
  ambiguous target is possible; it raises, naming the entry, rather than picking one.
- A stale entry whose `(source, source_key)` no longer appears is a silent no-op. FFC's key is a
  display name, so a spelling fix upstream retires the entry that corrected it with nothing said.
- The file ships empty. Every one of today's 28 unmatched records names a player with no `players`
  row, and an entry cannot point at a row that does not exist.
- Adds `pyyaml`. `tomllib` would have cost nothing, but the issue names a `.yaml` file and the
  format is the one an operator edits under time pressure.

**Alternatives rejected**
- **Keep the table.** Matches specs/draft-assistant.md §4.4 and needs no dependency. Rejected: it
  cannot be reviewed in a
  diff or edited before the database exists, and specs/draft-assistant.md §4.3's "checked into
  the repo" is explicit.
- **Target `nflverse_id`.** The most stable key, carried by 1,020 of 1,021 players. Rejected: every
  fixture pool inserts NULL, so the first real entry breaks every ingest test, and it cannot name a
  player who has no external id at all.
- **Target `player_id`, as specs/draft-assistant.md §4.4 sketches.** Rejected outright: a git-tracked file naming a
  reissued surrogate points at a different player after a rebuild, silently — the exact failure
  specs/draft-assistant.md §4.3 exists to prevent.
- **Leave the table in place as dead schema** and file the drop separately. Rejected: the spec
  names it as authoritative, so a reader who finds it will use it.
