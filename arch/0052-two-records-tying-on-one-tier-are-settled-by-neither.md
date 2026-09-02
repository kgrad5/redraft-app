# ADR-52 — Two records tying on one tier are settled by neither

**Status:** Accepted · 2026-09-01 · issue #8

**Context**
ADR-51 replaced a first-come claim with a tier-aware one, so that a positive identification is
never dropped in favour of whichever record a source happened to list first. It settled the
unequal case and left the equal one as it found it: *"on an equal or worse tier the later record
loses"*. For a worse tier that is right — the incumbent matched on a better tier, which is a fact
about the player. For an equal tier it is the arrival-order rule ADR-51 exists to remove, surviving
in the single case where the tiers genuinely cannot separate the two records.

`_index` already refuses this bet on the pool side: two *players* sharing a key resolve to nobody,
because picking the row that came back last is the silent wrongness specs/draft-assistant.md §4.3
exists to prevent. Two *records* reaching one player on one tier is the same coin toss seen from
the other end, and it was being taken.

**Decision**
Two records that reach one player on the same tier are settled by neither. The player is withdrawn:
both records are reported, and neither resolves. `Resolver.withdrawn` exposes the withdrawn set,
because `resolve` had already returned the player to the first record by the time the second
arrived and a return value cannot be recalled; every ingester in reporting mode drops those players
from its accumulated rows before it writes. A strictly better tier still takes the player and
clears the withdrawal.

Under `on_duplicate="raise"` — both ADP sources — an equal-tier collision remains
`DuplicateResolutionError` and aborts the run, unchanged from ADR-51. Withdrawal is a property of
the reporting mode alone.

**Consequences**
- **ADR-51's "on an equal or worse tier the later record loses" now holds only for the worse
  case.** This narrows one clause; the rest of ADR-51 stands, so it stays in `arch/` rather than
  moving to the archive under ADR-48.
- **A resolved `player_id` can be invalidated by a later `resolve` call, which was not true
  before.** That is the cost of settling this in one pass, and it puts a real obligation on the
  caller: an ingester that does not sweep `withdrawn` writes the coin-toss rows anyway and the
  decision buys nothing. Only `projections` runs in reporting mode today, so only it sweeps — an
  ADP path that ever switches to `report` must add the sweep with it.
- **A contested record's report line is only true once the run has ended**, so the resolver keeps
  contested records inside the claim and builds `unmatched` at read time rather than appending as
  it goes. Appending eagerly cannot work here: a tie's own "withdrawn" lines become false the
  moment a crosswalk id settles the player, and the record that lost gets reported twice.
- The count on the seam still equals the report: both records are reported, so a tie counts 2
  unresolved rather than 1.
- A withdrawal is not permanent. A crosswalk id arriving afterwards is not arrival order, so it
  takes the player and clears the withdrawal; otherwise one ambiguous pair would bar a positively
  identified record from ever being written.
- No row changes today. ADR-51 measured zero contended claims across Sleeper, Yahoo and FFC, and
  this narrows a case that is a subset of those. Like ADR-51 this is a latent defect closed, not a
  wrong board corrected.

**Alternatives rejected**
- **Keep ADR-51's first-come rule for the equal tier.** It passes every test written before this
  entry. Rejected on what it leaves behind: the winner's rows are written on payload order, and the
  winner is exactly as likely to be the wrong player as the record that was reported. Reporting
  only the loser makes the report *look* complete while the wrong stats are on the board.
- **Raise on an equal-tier tie in `projections` too.** Loud, simple, and symmetrical with ADP.
  Rejected: ADR-42 rejected run-aborting on Sleeper and ADR-49 carried that forward, so one
  ambiguous pair would cost an entire projections run.
- **Defer every write to a second pass**, so no player is ever handed out and then withdrawn. It
  removes the invalidation above, which is the only ugly part of this. Rejected on ADR-51's own
  grounds: it changes the resolver's interface and all three ingest loops to buy an outcome
  `withdrawn` already gets.
