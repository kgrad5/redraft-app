# ADR-47 — Yahoo's ADP is `preseason_average_pick`, and `"-"` is not an ADP

**Status:** Accepted · 2026-08-31 · issue #7

**Context**
specs/draft-assistant.md §4.1 lists seven Yahoo fields — `preseason-average-pick`, `average-pick`,
`average-round`, `percent-drafted`, `auction-value`, `o_rank`, `psr_rank` — and the `adp` table
(specs/draft-assistant.md §4.4, ADR-30) has exactly one column for a location. Which field fills it
is unrecorded. Measured 2026-08-31: all 1,195 records carry a `draft_analysis` object holding both
`average_pick` and `preseason_average_pick`, but only 226 hold a number. The other 969 hold the
literal string `"-"`.

**Decision**
`adp.adp` is `preseason_average_pick`, the field issue #7's verification names. A record carrying
the `"-"` sentinel writes no row and is **not** counted as unresolved. Any *other* unparseable
value stops the run: `"-"` is the only non-numeric value the live pool holds, so a second one is a
shape change rather than a missing ADP, and swallowing it would leave the board quietly short while
`unresolved` — the tripwire built for exactly that — never moved. `adp.stdev`, `high`, `low` and
`times_drafted` stay NULL on Yahoo rows — Yahoo publishes no dispersion, which is the entire reason
FFC is ingested.

**Consequences**
- **`"-"` is a string where a number belongs, and it is on 81% of the pool.** A parser that assumes
  a number crashes on 969 of 1,195 records. This is the same class as the plain-text HTTP 999 body
  of specs/draft-assistant.md §2.1: the standard reflex walks straight into it, and the fixture
  carries several so a regression shows up as a raise rather than as a quiet shortfall.
- **The usable Yahoo pool is 226 players, not the 1,195 specs/draft-assistant.md §4.1 advertises,
  and the deepest numeric ADP is 143.5** — short of the 180 slots specs/draft-assistant.md §2.3
  measures FFC's marginals against. Yahoo location data runs out before the last two rounds, and
  issue #15's availability model inherits that gap rather than being handed it whole.
- `average_pick` is not stored. It diverges from the preseason figure by a mean of 0.30 picks and at
  most 1.5 today, but it is the live-market number and issue #16 is the likely reader. It is
  recoverable without a re-fetch: `snapshots.raw_payload` holds the whole response as JSONB
  (ADR-25), which is what that column exists for.
- The other five fields specs/draft-assistant.md §4.1 names have no column in `adp`. `average-round`
  and `percent-drafted` reach `raw_payload` and stop there. **`auction-value`, `o_rank` and
  `psr_rank` are not even requested** — the minimal request omits `out=auction_values,ranks` and
  `out=expert_ranks`, which is 0.57MB per snapshot — so an issue that wants them must widen the
  request, not just the parse. Nothing outside this entry says so.
- Yahoo rows and FFC rows sit in the same table with different columns populated. A reader joining
  them must know that `stdev IS NULL` means "Yahoo row", not "no dispersion measured". ADR-30
  already establishes `snapshots.source` as the field to trust for that.

**Alternatives rejected**
- **`average_pick` as the location.** It is the live market and arguably the better number for a
  draft happening now. Rejected: issue #7's verification names `preseason-average-pick`, the two
  differ by at most 1.5 picks today, and `raw_payload` keeps the alternative reachable by query if
  issue #16 wants it.
- **Write a row with a NULL `adp` for the 969 `"-"` records**, so the table records the whole pool.
  Rejected: `adp.adp` is NOT NULL in the model, and a row asserting a player was in the pool with no
  draft position tells a reader nothing an absent row does not.
- **Count the `"-"` records as unresolved.** Rejected: they resolve perfectly well, they simply have
  no ADP. Putting 969 of them on a tripwire meant to show which players the board is missing is
  ADR-42's ADP-shell failure exactly.
- **Skip every unparseable value rather than only the sentinel**, which is what issue #7's approved
  plan specified and what this entry said when it was approved. Rejected on review, before merge: it
  makes a renamed sentinel — or a thousands separator, or a nested object — indistinguishable from a
  player Yahoo publishes no ADP for, and `unresolved` cannot catch the difference because such a
  record resolves perfectly well. The board would simply be short. ADR-41 took the same line for
  Sleeper's stat keys, and for the same reason: the invariant should hold by construction rather
  than by the source's naming discipline.
