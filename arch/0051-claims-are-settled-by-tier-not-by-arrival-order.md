# ADR-51 — Two records claiming one player are settled by tier, not by arrival order

**Status:** Accepted · 2026-09-01 · issue #8

**Context**
ADR-49 gave resolution four tiers in priority order but said only what happens within a
single record. It left open what happens when two records from one source resolve to the
same player, and the implementation answered it by accident: a flat first-come claim, so
whichever record the source listed first kept the player.

That discards the precedence the tiers exist to express. Reproduced against the live
schema on 2026-09-01, both directions:

- A record matching on the crosswalk id — a positive identification — is dropped in
  favour of an earlier record that matched only on the normalized name. Reversing the
  two records reverses which player's projections are written, so payload order decides
  identity.
- An exception-file entry is dropped in favour of an earlier automatic match, which is
  what ADR-50 says the file cannot lose to. Consulting the file first is not sufficient:
  the override simply loses further down the same function.

Neither fires on today's data — measured across all three sources, zero contended
claims — so this is a latent defect rather than a wrong board.

**Decision**
A claim records the tier that made it. A record matching on a strictly better tier takes
the player, and the record it displaces is reported as unmatched with the tier that
displaced it. On an equal or worse tier the later record loses, which is
`DuplicateResolutionError` for `adp` and a reported record for `projections` — the two
behaviours ADR-49 already assigns. ADR-52 narrows the equal case: two records tying on one
tier are settled by neither, so `projections` reports both and withdraws the player, while
`adp` still raises. Both ingesters key their accumulated rows by `player_id`, so writing a
displacing record discards the rows the displaced one left.

**Consequences**
- ADR-50's "the file is consulted before the automatic tiers" now holds where it is
  actually tested — against a competing record, rather than only against an empty field.
- **This narrows ADR-46's reflex, which `adp` inherited: it raised on any two records
  resolving to one player, and now stays silent when the arriving record outranks the
  incumbent.** That case is no longer an error, because the tiers say which record is
  right — without it an exception entry would abort a Yahoo run rather than override. The
  narrowing is one-directional, and this bullet first claimed more than the Decision above
  grants: a collision the arriving record does *not* win, differing tiers included, is
  still `DuplicateResolutionError` for `adp`.
- The written rows no longer depend on the order a source lists its records in. No row
  changes today: zero contended claims across Sleeper, Yahoo and FFC.
- The ingesters accumulate into a dict keyed by `player_id` rather than a list. For
  `projections` this also removes a silent merge — its primary key is
  (snapshot_id, player_id, stat_key), so two records with disjoint stat keys would
  otherwise have been written as one player rather than colliding. ADR-52 puts a second
  mechanism on top of that key: `projections` also drops the players it withdrew before
  it writes.
- A displaced record is reported, so the count on the seam still equals the report.

**Alternatives rejected**
- **Leave the first-come claim.** It is what the branch shipped and it passes every test
  written before this entry. Rejected on the reproduction: it drops the one record that
  is certain and makes output depend on payload order.
- **Two-pass resolution** — match every record, then award claims in tier order. Reaches
  the same answer and is arguably clearer, but it changes the resolver's interface and
  all three ingest loops to buy an outcome the tier-aware claim already gets.
- **Raise on any contention in both ingesters.** Simple and loud. Rejected: it makes a
  hand-written exception entry a run-killer on Yahoo, which is the opposite of what
  specs/draft-assistant.md §4.3 asks the file to do.
