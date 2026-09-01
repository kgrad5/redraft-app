# ADR-48 — A superseded entry moves to `arch/archive/` and keeps its number

**Status:** Accepted · 2026-08-31 · issue #7

**Context**
specs/draft-assistant.md Appendix A says entries from ADR-24 on live in `arch/`, that "`ls arch/`
is the index", and that "Entries are append-only — a reversal is a new file that supersedes the
old one, and the old one is never deleted." ADR-33 superseded ADR-26 on issue #3 and left the file
in `arch/`, where it sits beside live entries and reads as one; only its Status line says
otherwise. `tests/test_conventions.py` already keys its grandfathered exemption by basename across
both directories, anticipating the move, but nothing in `arch/` or in specs/draft-assistant.md
records the convention itself. This entry is written on issue #7 because that is the first unit of
work to perform the move, not because ADP has anything to do with it.

**Decision**
A file in `arch/` whose Status line reads `Superseded by ADR-<n>` moves to `arch/archive/`,
keeping its filename byte for byte and its contents unchanged. Numbers are never reused: the
archive counts toward the next entry's number. The archive is part of the record rather than a
wastebasket, and the move is `git mv`, so history follows the file.

**Consequences**
- **specs/draft-assistant.md Appendix A's "`ls arch/` is the index" is now incomplete.** `ls arch/`
  shows the live set plus an `archive/` directory, and a reader following that instruction will not
  find ADR-26 at all. The index is `ls arch/ arch/archive/`.
- The append-only promise holds where it matters — no entry is destroyed — but Appendix A's "the
  old one is never deleted" does less work than it reads. The entry is not deleted; it is no longer
  where that sentence implies it is.
- The next entry's number is one past the highest across **both** directories. A count taken from
  `arch/` alone reuses a number a retired entry still holds. That hazard is latent today only
  because 0026 sits below the live range; the first supersede of a recent entry makes it live.
- `tests/test_conventions.py`'s grandfathered exemption follows a basename into the archive, so the
  pre-convention entries stay exempt after moving. A path-keyed exemption would have made exactly
  those entries unarchivable, since the pre-convention ones are the likeliest to be superseded.
- Nothing else reads `arch/` programmatically, and prose references to an entry are by number
  rather than by path, so no reference breaks.

**Alternatives rejected**
- **Leave a superseded entry in `arch/`.** This is what happened to ADR-26 for two issues running,
  and it is the reason this entry exists: a retired decision shelved beside live ones is read as
  live. Nothing ever supersedes an already-superseded entry a second time, so an entry missed once
  stays missed.
- **Delete it.** Refused by specs/draft-assistant.md Appendix A outright, and by the reason the log
  exists: a reversal is the most useful thing in the record, and it is legible only beside the
  decision it reversed.
- **Mark it superseded and leave it in place**, expecting readers to filter on the Status line.
  It puts the work on every future reader, and `ls arch/` — the instruction specs/draft-assistant.md
  actually gives — cannot do it.
