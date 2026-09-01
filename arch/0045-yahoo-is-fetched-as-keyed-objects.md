# ADR-45 — The Yahoo request asks for `format=json_f`, so there are no attribute arrays to index

**Status:** Accepted · 2026-08-31 · issue #7

**Context**
specs/draft-assistant.md §4.2 forbids indexing player attribute arrays positionally, recording that
`display_position` sits at `[10]` when an injury-status object is present and `[9]` when it is not,
and that positional indexing "works perfectly in testing and breaks the instant a player is ruled
out — i.e. on draft night, for exactly the players you most need to reason about". Yahoo renders
the same resource two ways and the spec does not say which is in play.

**Decision**
The request pins `format=json_f`, which renders every player as a keyed object. `format=json` — the
array form the hazard lives in — is never requested. Parsing is by key throughout, and the
parameter is pinned by a test asserting the query that goes out on the wire rather than by a
comment a later edit can contradict.

**Consequences**
- The rule holds by construction rather than by discipline: there is no array for a later edit to
  index into. This is the move ADR-41 made for stat keys, applied to shape instead of vocabulary.
- **The hazard is real and worse than recorded.** Measured 2026-08-31 under `format=json`,
  `display_position` sits at `[12]` for a healthy player and `[14]` for one carrying `status` and
  `injury_note`. The exact indices in specs/draft-assistant.md §4.2 are wrong — they depend on which
  `out=` parameters the request asks for — but the failure class is exactly as that sentence
  describes.
- The 1,195 records carry **13 distinct key-sets**. `status`, `status_full`, `injury_note`,
  `player_notes_last_timestamp` and `has_recent_player_notes` each appear on some records only, and
  `has_player_notes` is absent from others. Every one must be treated as optional, which keyed
  access makes free and positional access makes a trap.
- `json_f` is not a documented Yahoo parameter. If it is withdrawn the fetch does not silently
  degrade: `format=json` nests `league` as a list rather than an object, so the parse raises at the
  boundary instead of reading a wrong index. A test feeds it the array form to pin that.

**Alternatives rejected**
- **Request `format=json` and re-key the arrays by name after parsing.** It uses the documented
  parameter, but it rebuilds by hand the shape `json_f` already returns, and every future edit to
  that conversion is another chance to reintroduce the exact bug specs/draft-assistant.md §4.2
  names.
- **Filter positions server-side, as ADR-43 does for Sleeper.** Yahoo's `position` parameter takes
  one value, so four positions means four requests and therefore four snapshots — which ADR-30
  forbids reading as one source's fetch. `position=ALL` in a single request is what keeps a Yahoo
  pull a single snapshot, and the unwanted K and DEF records are dropped in the parse instead.
