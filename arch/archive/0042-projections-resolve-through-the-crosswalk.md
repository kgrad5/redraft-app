# ADR-42 — Sleeper projections resolve through the nflverse crosswalk alone

**Status:** Superseded by ADR-49 · 2026-08-31 · issue #6

**Context**
`projections.player_id` is a NOT NULL foreign key to `players` (ADR-29), so every Sleeper
record must resolve to an internal id before it can be written.
specs/draft-assistant.md §4.3 gives three tiers — the nflverse crosswalk, then name +
team + position matching, then a hand-maintained exception file — and assigns tiers two
and three to issue #8, whose files do not exist yet. Issue #5 populated
`players.sleeper_id` from `roster_weekly_2026`, so tier one is available now. Measured
live on 2026-08-31: 837 distinct `sleeper_id` values on QB/RB/WR/TE roster rows resolve
518 of the 555 component-bearing Sleeper records.

**Decision**
The ingester joins on `players.sleeper_id` and nothing else. A record whose Sleeper id is
absent writes no rows, and the count of such records is returned on `IngestResult`, which
gains a third field, `unresolved`. Sleeper's `yahoo_id` is not consulted
(specs/draft-assistant.md §2.3).

**Consequences**
- **37 players with real projections are not written today, and one is inside the top 200
  by ADP** — Mike Washington (RB, LV, Sleeper `adp_ppr` 158.9). Tyreek Hill (227.2) and
  Brandon Aiyuk (251.4) are next. Issue #8's verification sets the bar at no unmatched
  top-200 player, so #8 is what closes this; #6 turns the gap from a silence into a
  number.
- `unresolved` counts only records that carried a real component. Counting every
  unmatched id would put 2,243 ADP shells on the number and drown the signal.
- `IngestResult` gains a field, widening ADR-37's seam for every provider. It is the only
  channel a provider has to report what it declined to write, and issue #9 is the reader;
  a default of 0 keeps it optional for issue #7's two ADP providers.
- Nothing records the *names* of the unresolved. A count is a tripwire, not the
  unmatched-player report specs/draft-assistant.md §4.3 requires — issue #8 owns that.
- `players.sleeper_id` is UNIQUE, so the crosswalk is injective and two Sleeper records
  can never collide on one `player_id`.

**Alternatives rejected**
- **Raise on any unresolved id**, the loud-failure pattern `src/redraft/ingest/players.py`
  uses throughout. Rejected on the measurement: 37 of 555 do not resolve today and
  Sleeper's pool is deliberately wider than a roster, so the run would never complete and
  the ingester would be unusable until issue #8 lands.
- **Do tier-two name matching here.** It would resolve most of the 37 now, but
  specs/draft-assistant.md §4.3 makes normalization and a review step one concern, issue
  #8 owns the files, and a matcher written here is the one #8 would delete.
- **Count every unmatched id, with no component test.** Simpler, but the number is then
  dominated by players Sleeper projects nothing for, and a tripwire nobody can act on is
  not one.
