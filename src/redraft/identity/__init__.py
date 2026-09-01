"""Player identity: one place where a source's record becomes a `players.player_id`.

specs/draft-assistant.md §4.3 gives three tiers — the nflverse crosswalk, name matching
with normalization, and a hand-maintained exception file — and this package is all three,
plus the unmatched-player report that says who none of them placed. It replaces the two
partial resolvers that stood in for it: `players.sleeper_id` alone in
`redraft.ingest.projections` (ADR-42) and an exact `(full_name, position)` match in
`redraft.ingest.adp` (ADR-46), which said in as many words that issue #8 would replace it.
"""
