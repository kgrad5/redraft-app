# ADR-49 — One tiered resolver places every source record, and the seam carries what it could not

**Status:** Accepted · 2026-08-31 · issue #8

**Context**
Two resolvers existed, both placeholders for this issue. ADR-42 gave Sleeper `players.sleeper_id`
and nothing else; ADR-46 gave Yahoo and FFC a source id then an exact `(full_name, position)` match
and said issue #8 replaces that helper rather than extending it. Both deferred the same two things:
the normalization of specs/draft-assistant.md §4.3 and the unmatched-player report, of which they
recorded only a count.

Measured 2026-08-31 against the live 1,021-player pool, the current Sleeper snapshot and live
fetches of Yahoo and FFC:

| source | considered | crosswalk only | + exact | + normalized |
|---|---|---|---|---|
| Sleeper | 555 | 37 | 28 | 27 |
| Yahoo | 184 | 41 | 2 | 1 |
| FFC | 220 | n/a | 7 | 0 |

**Decision**
Every source resolves through one `Resolver` in `src/redraft/identity/`, in a fixed order: the
exception file, the source's crosswalk satellite, an exact `(full_name, position)`, then a
normalized `(name, position)`. Team is not in the key. A key naming more than one player resolves
to nobody and is reported. `IngestResult.unresolved: int` becomes
`unmatched: tuple[Unmatched, ...]`, with `unresolved` derived as `len(unmatched)`.

**Consequences**
- **specs/draft-assistant.md §4.3's "name + team + position" is wrong.** Team is out, carrying
  ADR-46's measurement forward: FFC writes `LAR` where nflverse writes `LA`, which alone drops Puka
  Nacua at ADP 2.9.
- **The exact tier precedes the fold, and that ordering is load-bearing.** Folding first makes a
  suffix pair in `players` ambiguous and loses both players, where the exact tier resolves a
  byte-identical spelling correctly. An exact spelling is a positive identification.
- Normalization is safe on today's pool: no two of the 1,021 players share a normalized name, in a
  position or across all of them. The ambiguity rule is a guard that fires on nothing today.
- **One player inside the top 200 remains unplaced and always will.** Yahoo drafts Tyreek Hill at
  129.2; nflverse's `roster_weekly_2026` gives him no row, so no tier can place him — resolution
  maps onto existing `players` rows and cannot invent one. Issue #8's verification was narrowed to
  players that have a row, and the coverage gap is a follow-up issue against
  `src/redraft/ingest/players.py`.
- The report is 28 lines across all three sources, which is the reviewable length specs/draft-assistant.md §4.3 asks for.
- A name-tier collision in `projections.py` reports rather than raises. ADR-42 rejected
  run-aborting on Sleeper, and its "two Sleeper records can never collide" property rested on
  `sleeper_id` being UNIQUE, which a name tier ends.
- Nothing in `src/` calls `ingest()`, so #8 emits no report on a schedule; it supplies the renderer
  and the records, and issue #9 is the caller.

**Alternatives rejected**
- **Normalize without an exact tier.** Fewer branches, and it closes the same players on today's
  data. Rejected: it is a strict regression against the helper ADR-46 scheduled for replacement,
  losing both members of a suffix pair the exact tier resolves.
- **Keep team in the key**, as specs/draft-assistant.md §4.3 words it. Rejected on ADR-46's
  measurement, unchanged here.
- **Keep `unresolved` as a count and add the records beside it.** Rejected: two fields saying the
  same thing drift, and the count is the one #9 reads.
