# ADR-41 — Sleeper stat keys are classified, and an unknown key fails the run

**Status:** Accepted · 2026-08-31 · issue #6

**Context**
specs/draft-assistant.md §4.2 forbids ingesting anyone's fantasy-point total, and
specs/draft-assistant.md §6.0 makes that fidelity the project's only claimed edge.
Sleeper returns one `stats` dict per player and nothing in it says which keys are
components. Measured live on 2026-08-31: 71 distinct keys across the 9,418-record
all-position response, 37 of them under the QB/RB/WR/TE filter this ingester uses.
Three are fantasy-point totals; twelve are ADP, which issue #7 owns and two of which
are the 999.0 sentinels specs/draft-assistant.md §2.3 records. The component example
in specs/draft-assistant.md §4.1, `rec_tgt`, is not among them — no Sleeper key
contains `tgt` — so that sentence is wrong.

**Decision**
Every key in `stats` is classified into exactly one of three sets before anything is
written. Fantasy-point totals, named exactly — `pts_std`, `pts_ppr`, `pts_half_ppr` —
are never written. ADP, the `adp_` prefix, is never written. Components — the prefixes
`pass_`, `rush_`, `rec_`, `fum_`, `bonus_`, `cmp_`, `pr_`, `def_`, `idp_`, plus the bare
keys `rec` and `gp` — are written. A key in none of the three raises
`UnknownStatKeyError` and fails the run. A record whose only component is `gp` is an ADP
shell rather than a projection and is skipped entirely.

**Consequences**
- The never-a-point-total rule holds by construction rather than by Sleeper's naming
  discipline. A total under a new name lands in no class and stops the run.
- **A key added upstream fails the daily run until someone classifies it.** That is the
  deliberate cost and it is a one-line edit. specs/draft-assistant.md §10 puts this
  ingester on days 2–4, so the exposure runs to draft night; issue #9's failure policy
  decides whether one dead provider stops the whole job.
- `pts_` cannot serve as a denylist prefix: `pts_allow_0` is a real component, a
  defense's points-allowed bucket. It never arrives under the QB/RB/WR/TE filter, but
  naming the three totals exactly means adding DEF later cannot silently drop it.
- The component example in specs/draft-assistant.md §4.1 is wrong and stays wrong; this
  ingester writes `rush_att`, `rec_yd` and `pass_td`. Nothing reads that list
  programmatically, so the false example costs a reader rather than a run.
- 2,559 of the 3,114 filtered records are ADP shells and write nothing. The 555 that
  carry real components yield roughly 7,400 rows per snapshot.
- Classification is per key, so a player's row set varies by position. Nothing downstream
  may assume a fixed stat vocabulary.

**Alternatives rejected**
- **A denylist** — write everything except `pts_` and `adp_`. Nothing is ever silently
  dropped and new components arrive free. Rejected: it makes the one invariant this issue
  exists to hold depend on Sleeper never renaming a total, and `pts_allow_0` already
  shows the prefix is not clean.
- **A closed allowlist of the 37 observed keys**, skipping the rest silently. It
  guarantees the invariant too, but a new component then vanishes without trace — the
  failure specs/draft-assistant.md §4.3 names for players, which reads no better here.
