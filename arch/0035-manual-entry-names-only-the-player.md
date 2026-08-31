# ADR-35 — Manual entry names only the player; the tap names everything

**Status:** Accepted · 2026-08-30 · issue #3

**Context**
specs/draft-assistant.md §7.3 says manual entry is "click the player on the board", and its §8.1
makes it the substrate every other feed accelerates. Both feeds write the same `draft_events`
rows, so the shape of the write is a contract that #19's board UI and #24's tap will both bind to,
and it has to be settled before either of them exists.

**Decision**
`POST /draft/{draft_id}/picks` carries `{"player_id": ...}` and nothing else. The pick number, the
team on the clock and `source='manual'` are derived server-side from the reduced board. Refusals
are HTTP 409 with a closed set of machine-readable `reason` codes: `draft_complete`,
`unknown_player`, `player_already_drafted`, `position_cap_exceeded`, `nothing_to_undo`,
`malformed_event_stream`.

**#24's tap inverts this deliberately.** A tapped frame reports what Yahoo already did, so it
supplies its own `pick_no` and `team_id` rather than deriving them. The two feeds write one table
through two contracts, and the asymmetry is the point: manual entry decides, the tap reports.

**Consequences**
- A manual client cannot disagree with the server about whose turn it is, because it is never
  asked. One source of truth for the clock.
- #19 can render a refusal without parsing prose. A new refusal means a new code, which is a
  visible change rather than a silent one.
- #24 cannot reuse this endpoint as it stands. Deriving the team would overwrite what Yahoo
  actually reported, which is exactly the mismatch the reconciliation in
  specs/draft-assistant.md §4.4 exists to detect.
- Nothing validates that a tap-supplied `pick_no` agrees with the board's own count. That check
  belongs to #24's reconciliation, and this entry is what tells it the check is its job.

**Alternatives rejected**
- **The client supplies `pick_no` and `team_id`.** Uniform across both feeds and one fewer server
  read, but it lets a stale UI seat a player for a team that is not on the clock, and no
  constraint would catch it — ADR-27 leaves `(draft_id, pick_no)` non-unique on purpose.
- **Free-text error messages instead of a code set.** Cheaper to write, and unusable under a
  30-second clock, where the UI has to decide what to show without a human reading the sentence.
