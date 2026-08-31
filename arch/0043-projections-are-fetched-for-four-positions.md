# ADR-43 — The projections request is filtered to the four fantasy positions

**Status:** Accepted · 2026-08-31 · issue #6

**Context**
specs/draft-assistant.md §4.1 gives Sleeper's endpoint as `/projections/nfl/2026` and no
parameters. Measured live on 2026-08-31, that path answers HTTP 400: `season_type` is
required. With `season_type=regular` alone the response is 9,418 records across every
position, 8.6MB. The request this ingester actually makes is therefore a decision the
spec does not record, and issue #6's approved plan treated it as a fetch detail rather
than as one.

**Decision**
The request is `season_type=regular` plus `position[]` repeated for QB, RB, WR and TE —
3,114 records, 2.9MB. The bracketed spelling is part of the decision, not a spelling
detail: `position` without brackets answers HTTP 200 with WR alone.

**Consequences**
- **No team defense will ever carry a projection row**, and specs/draft-assistant.md §2.2
  rosters one DEF. nflverse carries no team defenses either, so `players` holds nothing
  for a DEF projection to resolve to and the filter costs nothing today — but whichever
  issue adds team defenses has to widen this request as well, and nothing outside this
  entry says so.
- Kickers are excluded on the same grounds at no cost: specs/draft-assistant.md §2.2
  records that the league drafts none, so a kicker on the board is noise.
- specs/draft-assistant.md §4.1's endpoint cell is incomplete rather than wrong — the
  path is right and the parameters are missing. A reader who follows the spec alone gets
  a 400.
- The unbracketed spelling narrows the board by 56% while still answering 200, so the
  parameter is pinned by a test asserting what goes out on the wire rather than by a
  comment that a later edit can contradict.
- ADR-41's key census — 37 component keys rather than 71 — is a consequence of this
  filter rather than an independent fact. Widening the positions widens the
  classification the same day, and `pts_allow_0`, `int`, `sack` and the kicker families
  all arrive together.

**Alternatives rejected**
- **Fetch every position and filter after the parse.** One request shape survives any
  future roster change, and the snapshot then records the whole board, which is what
  specs/draft-assistant.md §4.2's replay-without-refetch promise is best served by.
  Rejected on cost for no present gain: 8.6MB per daily snapshot against 2.9MB, roughly
  6,300 records that can resolve to no row in `players`, and a classification burden for
  kicker and IDP families that nothing in this league scores.
- **Leave it unrecorded as a fetch detail**, which is what issue #6's plan did. Rejected
  on review: the DEF consequence outlives the parameter, and a request shape that
  answers 200 to two different wrong spellings is not a detail.
