# ADR-46 — ADP rows resolve by source id first, then by exact (full_name, position)

**Status:** Superseded by ADR-49 · 2026-08-31 · issue #7

**Context**
`adp.player_id` is a NOT NULL foreign key to `players` (ADR-29), so every record must resolve to an
internal id before it can be written. ADR-42 settled Sleeper's resolution as the nflverse crosswalk
alone, deferring specs/draft-assistant.md §4.3's second and third tiers to issue #8, and rejected
name matching explicitly "on the measurement" — 37 of 555 unresolved, one inside the top 200. The
measurement for these two sources is not that one. FFC publishes no crosswalk id at all: its rows
carry `name`, `team` and `position` and an FFC-internal `player_id` that matches nothing. And
nflverse's `roster_weekly_2026` carries `yahoo_id` for 549 of 1,004 fantasy-position rows and for
none of the 2026 rookies, so Yahoo's tier one has a hole exactly where the draft is decided.

Measured 2026-08-31, records at QB/RB/WR/TE:

| source | records | id only | id then exact name |
|---|---|---|---|
| Yahoo, numeric ADP | 184 | 143 | 182 |
| FFC | 220 | not available | 213 |

By id alone Yahoo loses Ashton Jeanty (ADP 16.9), Omarion Hampton (18.5) and Jeremiyah Love (30.0).

**Decision**
Both ADP providers resolve through one shared helper in `src/redraft/ingest/adp.py`. A source id is
tried first where the source has one — Yahoo's bare numeric `player_id` against
`players.yahoo_num_id`, never the `470.p.{id}` form (specs/draft-assistant.md §2.1) — then an
**exact** match on `(players.full_name, players.position)`. Exact means exact: no normalization, no
suffix or punctuation handling, no exception file, no unmatched-player report. A record resolving
to neither writes nothing and is counted on `IngestResult.unresolved` (ADR-42). Records whose
position `players` cannot hold are skipped before resolution and not counted.

**Consequences**
- **Nine records still fail — 2 Yahoo, 7 FFC — and all nine are the suffix and punctuation variants
  specs/draft-assistant.md §4.3 names**: Kyle Pitts Sr., James Cook III, Travis Etienne Jr., Michael
  Pittman Jr., Aaron Jones Sr., Kenneth Walker, Tre' Harris, Oronde Gadsden, Tyreek Hill. Issue #8
  closes them and its verification bar — no unmatched player inside the top 200 — is what measures
  it. Without the name tier here that list would instead be 41 Yahoo records deep and start at ADP
  16.9.
- **This is tier two of specs/draft-assistant.md §4.3 in its degenerate form, landing before issue
  #8 owns tier two, which is what ADR-42 rejected for Sleeper.** The difference is the measurement
  ADR-42 itself appeals to: Sleeper had a working tier one and these two do not. This narrows
  ADR-42's reasoning to its evidence rather than reversing its decision, and issue #8 replaces this
  helper rather than extending it.
- **Team is deliberately not in the key.** FFC and nflverse disagree on team for 11 of 220 records —
  FFC writes `LAR` where nflverse writes `LA`, and FFC writes `FA` for players nflverse still
  rosters — which alone drops Puka Nacua at ADP 2.9. Team is the volatile field and including it
  costs more than it protects.
- `(full_name, position)` is not unique by construction. It maps to exactly one player across all
  1,020 rows of today's universe, and two records colliding on one `player_id` would violate `adp`'s
  primary key and raise rather than silently overwrite — the INSERT carries no ON CONFLICT, matching
  `src/redraft/ingest/projections.py`.
- Yahoo's K and DEF records and FFC's PK and DEF records can never resolve, because `players` holds
  QB/RB/WR/TE only (issue #5). Skipping them before resolution rather than counting them keeps
  `unresolved` at 2 and 7 instead of 44 and 58 — the same reasoning ADR-42 used to keep 2,243 ADP
  shells off Sleeper's count. A tripwire that reads 44 on a healthy run is not one.
- Nothing records the *names* of the nine. A count is a tripwire, not the unmatched-player report
  specs/draft-assistant.md §4.3 requires; issue #8 owns that.

**Alternatives rejected**
- **Crosswalk id alone, as ADR-42 chose.** Consistent with the Sleeper ingester and keeps every name
  comparison in issue #8. Rejected on the measurement ADR-42 itself invokes: it leaves 41 of Yahoo's
  184 unplaced including three inside the top 30, and leaves FFC unable to write a single row, which
  fails issue #7's verification outright.
- **Include team in the key**, which is literally tier two as specs/draft-assistant.md §4.3 words it.
  Rejected: 18 of FFC's 220 then fail, and the failures are team-code disagreement rather than
  identity, with Puka Nacua at ADP 2.9 among them.
- **Normalize names here** — strip `Jr.`/`Sr.`/`III`, fold punctuation. It would close all nine
  today. Rejected: specs/draft-assistant.md §4.3 makes normalization and a review step one concern,
  issue #8 owns those files, and a normalizer written here is the one issue #8 deletes.
