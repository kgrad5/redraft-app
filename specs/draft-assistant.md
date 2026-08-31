# redraft-app — Specification

**Yahoo fantasy football draft assistant.** Personal, local-only, single league.

| | |
|---|---|
| Spec date | 2026-08-30 |
| Season opens | 2026-09-09 |
| Draft | 2–3 weeks out |
| Status | Specified, not started |

Derived from a requirements interview plus an adversarially-verified research pass (all external
facts in §2 were verified live on 2026-08-30 and are dated for that reason — re-verify before relying
on them).

---

## 1. Purpose & scope

A draft assistant for one private Yahoo redraft league. It recommends the best available pick in real
time, given the current roster, the players still on the board, and how this specific draft is
unfolding.

**In scope**

- Daily-refreshed projection, ADP, and roster data
- Exact league-scoring fidelity (component stats scored under this league's rules)
- Pre-draft tiered board and target list
- Live draft-night recommendation engine
- Manual pick capture, plus an optional automated pick feed
- Mock-draft rehearsal and availability-model calibration

**Out of scope**

- In-season lineup, waiver, or trade tools
- Auction drafts (different opcodes, different math — see §11)
- Keepers, superflex, traded draft picks (this league has none)
- Multi-user, hosting, distribution
- A bespoke projection model (see §6.0 for why)

### 1.1 Terms-of-service boundary

This is a personal tool, for one league, running only on the owner's machine. That boundary is the
premise the design rests on, and it is not to be crossed without revisiting the data sources:

- **Sleeper** grants free use for *non-commercial* purposes; its ToS (updated 2026-08-27) establishes
  an "Approved Integration Partner" regime for anything beyond that.
- **FantasyPros** (if ever added) is a personal-use license that explicitly bars providing third
  parties access to API materials.
- **The WebSocket tap (§8)** is defensible because it reads frames the owner's own authenticated
  browser has already received, in their own session, for their own league. Operating it as a service
  for other people is a different posture entirely.

Three provider seams — `ProjectionProvider`, `ADPProvider`, `PickFeed` — are in place from day one so
a licensed version later is a swap, not a rewrite.

---

## 2. Verified constraints

These are load-bearing. Each was confirmed on 2026-08-30.

### 2.1 Yahoo access

| Constraint | Consequence for the design |
|---|---|
| **The OAuth Fantasy API is effectively unavailable this season.** Fantasy Sports is no longer a selectable permission at `developer.yahoo.com/apps/create`. The approval queue shows zero reported successes in 5+ weeks (yfpy issue #84, 10 comments, latest 2026-08-29; issue #85 open since 2026-08-07). | No `draftresults` polling. Live picks come from the draft-room WebSocket or from a human. |
| **The Yahoo Fantasy API is read-only and always has been.** `sports.yahoo.com/developer/access`: *"currently provides read access only."* | The app advises. A human clicks Draft in Yahoo's room. There is no autodraft integration to build. |
| **2026 NFL game key is `470`.** League keys `470.l.{id}`, player keys `470.p.{id}`. | WebSocket frames carry the **bare numeric id** (`7200`); the OAuth API uses `470.p.7200`. **Two different identifier spaces — mixing them silently mismatches players.** |
| **`draft_status` has a third value and it is `draft`, not `drafting`.** Verified across ~25 live mock leagues. | Any state machine branching on `drafting` never fires. |
| **`draft_server` / `draft_port` populate only while `draft_status == 'draft'`** — i.e. from roughly 30 minutes before the draft. They are `''` and `0` for all pre-draft and post-draft leagues (verified on 28 lobby leagues). Port is 443 in every observed case. | The realtime path cannot be established or tested ahead of time. FantasyPros independently documents the same ~30-minute window. |
| **There is no unauthenticated REST feed of live picks.** `v3 players/nfl/{leagueId}` returns ADP but its `team_key` is the NFL team (`nfl.t.23`), never a fantasy team, and there is no drafted/owner field. | Picks live on the socket or nowhere. |
| **Unauthenticated v3 works only for public leagues.** Private leagues return HTTP 403, body `{"error": ... "description": "Unable to retrieve cookie."}`. | **This league is private.** League config is hand-written (§5); Yahoo ADP is fetched through a public league id as a carrier (§4). |
| **All v3 responses are wrapped in a `service` envelope**: `{"service": {"xml:lang": "en-US", ...}}`. | Parsers must unwrap `.service`. |
| **HTTP 999 "Request denied" is the throttle signal — not 429 — and the body is plain text, not JSON.** It can fire on the *first* request of a session with zero prior volume, and clears in ~2 minutes. Not User-Agent dependent. | Standard error handling both misses it (checking for 429) and crashes (`JSON.parse` on the body). It is a stochastic WAF event, not a volume ceiling: **the first poll of draft night can fail for no reason.** |
| **Measured v3 latency**: `draftstatus` 0.19–0.23s (10/10 at 1 req/s); full 1,195-player pool is 2,242,065 bytes in ~0.55s. | Comfortable inside any pick clock. |

### 2.2 Draft mechanics

| Constraint | Consequence |
|---|---|
| **The pick clock may be 30 seconds.** A sampled real 12-team Yahoo league has `draft_pick_duration=30` (Yahoo's documented default is 60). | Design the on-clock frame for **30 seconds**. This is the single most important UI constraint. |
| **`position_draft_caps` exists** (e.g. `{"RB": "6"}` in a sampled live league). | Must be encoded, or the optimizer confidently recommends a seventh RB that Yahoo refuses — burning clock while you work out why. |
| **The roster is 15 deep and has no kicker**: 1 QB, 2 RB, 3 WR, 1 TE, 1 W/R/T flex, 1 DEF, 6 bench. | **The draft is 15 rounds, not 16** — 12 teams × 15 = the 180 slots §2.3 measures FFC's marginals against. Kickers are never drafted, so a K on the board is noise; the projection and scoring tiers can ignore the position entirely. The flex slot means WR/RB/TE replacement levels are coupled and cannot be computed independently (§6.2). |
| **Draft order is not published until ~30 minutes before.** `Team.draft_position` is unavailable earlier. | No pick-slot-dependent planning before then. Pre-draft mode must work without knowing the slot, then specialize. |
| **Keeper rules are not exposed by any Yahoo API.** You get `Player.is_keeper` and a `status=K` filter, never keeper count, cost, or deadline. | Not applicable to this league, but noted so a future league doesn't surprise anyone. |

### 2.3 Data availability

| Constraint | Consequence |
|---|---|
| **No 2026 usage data exists until after Week 1.** nflverse has no `play_by_play_2026`, `snap_counts_2026`, or `stats_player_2026`. Only `players`, `roster_weekly_2026`, `depth_charts_2026`, and `schedules` are live. The injury feed is broken (*"Our data source died after the 2024 season… no ETA"*). | Target share, air yards, and red-zone usage are **unavailable before this draft**. A usage-based projection model is not buildable in time. |
| **Projection accuracy ceiling**: within-position R² of 14–26% among draftable players; QB as low as 6–15%. Calibration slopes are all below 1 (QB 0.67, TE 0.72, RB 0.79, WR 0.85); mean bias is +21.6 points (+46.5 for QB over the last 3 seasons). | Raw projections **exaggerate the spread between players**. They must be shrunk toward the positional mean *before* any VOR arithmetic, or every elite player's apparent edge is inflated — worst at QB. |
| **NFL.com fantasy is dead for 2026 and its API silently serves 2025 data.** `api.fantasy.nfl.com` returns HTTP 200 with `systemConfig.currentGameId = "102025"`; the `season=2026` parameter is ignored. | Do not use it. ESPN is now the NFL's official fantasy game. |
| **Sleeper's `yahoo_id` is unusable as a crosswalk** — only ~24% of fantasy-relevant players have one, and it is null for essentially every player drafted since 2021 (Chase, Nacua, Smith-Njigba all null). Better populated: `fantasy_data_id` (99.4%), `sportradar_id` (98.9%), `rotowire_id` (98.0%). | Budget for a name + team + position matching layer (§4.3). |
| **Sleeper's `adp_dynasty` and `adp_rookie` are `999.0` sentinels for 100% of rows.** | Using them numerically poisons any ranking. |
| **FFC marginals are incoherent as a draft model.** Summing `Φ((180 − ADP_i)/sd_i)` over the pool gives **247.5 expected picks against 180 actual slots — a 38% overcount.** | Must be renormalized so expected players gone by pick N equals N. Otherwise the tool systematically understates availability and reaches. |
| **FFC's `teams` parameter is display-only.** `teams=8/10/12/14` return byte-identical `adp`/`times_drafted`/`stdev`; only `adp_formatted` changes. | Do not expect it to resample by league size. |
| **The FFC ADP tail is thin and selection-biased.** `times_drafted` median is 1,353 for ADP 0–24 but only 40 for ADP 150–200, with no fitted dispersion data past ADP 166. | Confidence in late-round survival estimates must visibly degrade rather than be presented with round-2 authority. |
| **FantasyPros API is 1 call/second and 100 calls/day**, $8.99/mo, personal use only, and explicitly not licensed for historical player statistics. | Cannot be an in-draft dependency and cannot be used for backtesting. Currently excluded. |

---

## 3. Architecture

```
  Yahoo draft room tab                     ┌──────────────────────────┐
  (desktop web, authenticated)             │  Tampermonkey userscript │
        │  window.WebSocket frames ───────►│  ~40 lines, frozen day 3 │
        │                                  └────────────┬─────────────┘
        │                                               │ ws://127.0.0.1:8787
        ▼                                               ▼
  ┌───────────────┐                          ┌────────────────────────┐
  │  Sleeper      │──┐                       │                        │
  │  Yahoo v3     │──┼─► daily job ─► Postgres ─►  Python engine      │─► board UI
  │  FFC          │──┤    (dated snapshots)   │   (FastAPI + NumPy)   │   (2nd screen)
  │  nflverse     │──┘                       │                        │
  └───────────────┘                          └───────────▲────────────┘
                                                         │
                                          manual pick entry (click the board)
```

**Stack**

| Layer | Choice | Why |
|---|---|---|
| Engine | Python 3.14, FastAPI, uvicorn, NumPy | Vectorized order-sampling; best fantasy-data ecosystem |
| Storage | Postgres in Docker (`docker compose up` for the DB only) | Dated snapshots; app runs natively in a venv for fast iteration and easy mid-draft restart |
| UI | Browser page served by the engine, second screen | Visual density the draft board needs |
| Pick tap | Tampermonkey userscript | No store review, instant reload — critical on a short runway |

**Offline-first.** The engine reads only local state. Every input is cached to Postgres before draft
day. If Yahoo, the network, or the tap dies, the draft continues on manual entry with **no degradation
in recommendation quality**. The pick feed is a convenience, never a dependency.

---

## 4. Data tier

A daily scheduled job plus an on-demand **"refresh now"** button (used immediately before the draft).
Every pull is written as a **dated snapshot — never an overwrite.** That enables riser/faller
detection, lets you reconstruct what the model saw on any day, and builds the dataset a future
backtest would need.

### 4.1 Sources

| Source | Endpoint | Provides | Notes |
|---|---|---|---|
| Sleeper | `/projections/nfl/2026` | **Component stats** (`rec_tgt`, `rush_att`, `rec_yd`, `pass_td`, …) | RotoWire data. Non-commercial grant. Stay under 1000 calls/min. |
| Yahoo v3 | `players/nfl/{publicLeagueId}` | `preseason-average-pick`, `average-pick`, `average-round`, `percent-drafted`, `auction-value`, `o_rank`, `psr_rank` — 1,195 players | **The correct ADP for a Yahoo room.** Fetched through any *public* league id as a carrier, since this league is private. |
| FFC | `/api/v1/adp/ppr` | `adp`, `stdev`, `high`, `low`, `times_drafted` | Free for any use (grant dated 2018, attribution requested). Once-daily updates. Used for **dispersion shape only.** |
| nflverse | `players`, `roster_weekly_2026`, `depth_charts_2026`, `schedules` | Bye weeks, teams, positions, ID crosswalk | No 2026 usage data exists. |

### 4.2 Ingestion rules

- **Ingest component stats only — never anyone's fantasy-point total.** Points are always computed
  under this league's exact scoring. That fidelity is the genuine edge over every commercial tool,
  which cannot match a private league's custom categories.
- Unwrap the `service` envelope on every v3 response.
- **Never index player attribute arrays positionally.** `display_position` sits at index `[10]` when
  an injury-status object is present and `[9]` when it is not. Positional indexing works perfectly in
  testing and breaks the instant a player is ruled out — i.e. on draft night, for exactly the players
  you most need to reason about. Key by name.
- Treat **HTTP 999** as throttling: check the status code, never `JSON.parse` the body, back off ~2
  minutes, and retry. Assume it can hit the very first request of the night.
- Record `fetched_at` and the raw response alongside the parsed rows, so a parser change can be
  replayed without re-fetching.

### 4.3 Player identity

1. **nflverse crosswalk** as the backbone.
2. **Name + team + position matching** for the remainder, with normalization for suffixes and
   punctuation (`D.K.` / `DK`, `Jr.`, `III`).
3. **A small hand-maintained exception file** for the stragglers, checked into the repo.

Do **not** join on Sleeper's `yahoo_id`. Prefer `fantasy_data_id` / `sportradar_id` where a numeric
join is possible. Every daily run emits an **unmatched-player report**; it must be short and reviewed,
because a silently-dropped player is a player missing from the board.

### 4.4 Schema sketch

```
snapshots        (snapshot_id, source, fetched_at, raw_payload)
players          (player_id, full_name, team, position, bye_week, nflverse_id, sleeper_id, yahoo_num_id)
projections      (snapshot_id, player_id, stat_key, value)          -- components, never points
adp              (snapshot_id, source, player_id, adp, stdev, high, low, times_drafted)
league_config    (season, scoring_json, roster_slots_json, position_caps_json, pick_duration)
draft_events     (draft_id, pick_no, player_id, team_id, source, received_at)
id_exceptions    (source, source_key, player_id, note)
```

`draft_events` carries one row per pick and one per undo, distinguished by `event_type`
(`pick` | `undo`), because the board is read by folding the stream in order rather than by
selecting it. `source` (`tap` | `manual`) is what makes reconciliation and post-draft latency
analysis possible.

The table was originally append-only, enforced by a trigger. It is not any more: a draft **reset**
deletes that draft's rows so the draft can be run again (§7.3, §9), and the triggers were removed
rather than narrowed because this is a single-operator tool. Nothing at the database level now
refuses a wider delete than intended. See ADR-33, which supersedes ADR-26.

---

## 5. League configuration

The league is private, so `v3 settings` returns 403. Scoring modifiers, `roster_positions`,
`position_draft_caps`, and `draft_pick_duration` are **transcribed by hand** from the league settings
page into a checked-in config file.

This is the single most dangerous file in the project. A wrong stat modifier corrupts every
projection, every VOR number, and every recommendation — **with no visible symptom.**

### 5.1 Verification gate (mandatory, blocks everything downstream)

Apply the hand-written config to **last season's actual stat lines** and reconcile the resulting
totals against what Yahoo actually awarded those players in this league. If the config is right, the
numbers match near-exactly.

Keep a handful of known-player totals as **permanent assertions**, so a later edit to the config fails
loudly instead of quietly.

---

## 6. Analysis tier

### 6.0 Where the edge actually comes from

Not from better talent evaluation. There is no 2026 usage data before the draft, 21 of 32 teams
changed offensive coordinators, and in the one clean published head-to-head a 1,933-feature XGBoost
model **lost** to plain ESPN projections at RB (rank-MAE 31 vs 17.4) and WR (37 vs 23).

The edge is **exact scoring fidelity for this league** plus **market discipline** — pricing the
decision correctly rather than ranking players better than the field does.

### 6.1 Objective: urgency (VONA)

```
score(player) = [ value_now(player) − E[ best survivor at my next pick, same role ] ] × roster_need
```

This prices the decision actually being made: *take him now, or wait?* It is fast enough for a
30-second clock and produces recommendations a human will actually follow.

**Championship-equity / full-roster-rollout is implemented behind a flag and switched OFF for this
draft.** The most rigorous published version of that approach tied its simpler predecessor *inside the
error bar* and shipped disabled, because its picks reached for QBs and TEs and drafters wouldn't
follow them. A recommendation you don't believe is worth nothing.

### 6.2 Pipeline, in order

**1 — Score components** under exact league scoring (§5).

**2 — Recalibrate before any VOR arithmetic.** Shrink toward the positional mean using the measured
calibration slopes (QB 0.67, TE 0.72, RB 0.79, WR 0.85) and subtract the optimism bias (+21.6 points
overall; +46.5 for QB). Skipping this inflates every elite player's apparent edge — worst at QB, which
is where reaching hurts most.

**3 — Variance.** Two signals that actually exist for free:
- **Market-vs-model divergence** — how far the projection sits from ADP. The market is a genuine
  second opinion, and it is the only independent one available.
- **Published per-position error curves** — the R² and calibration figures in §2.3.
- Plus an explicit **games-played distribution** per player (positional base rates, adjustable by
  hand for a known risk — a player coming off an ACL, or one already dinged in camp).

> *Design note:* the original intent was to derive variance from disagreement **between projection
> sources**. That is not viable — among free, ToS-clean sources only Sleeper publishes component stat
> lines. Yahoo v3 and FFC give ADP and ranks, not projections. There is effectively nothing to
> disagree with. This is a real reduction in fidelity and is recorded rather than papered over.

**4 — Availability model.** The highest-leverage component in the system.

- **Location** from Yahoo `preseason-average-pick` — you are drafting against eleven people in a
  *Yahoo* room, and FFC's pool implies a 3-WR starting lineup that does not match Yahoo's 2WR+FLEX
  default.
- **Dispersion shape** borrowed from FFC's `stdev`, then **renormalized so expected picks equals
  actual picks** (FFC raw overcounts by 38%).
- **Sample the whole draft order**, not per-player marginals: Plackett–Luce / Gumbel-max — draw
  `s_i = ADP_i + noise`, sort, take the first *k*. Independent per-player normal CDFs cannot enforce
  that exactly one player leaves per pick, and cannot represent runs at all.
- **Common random numbers across candidates**, so "taking the RB instead costs you the TE" is a real
  causal difference rather than noise wearing a name.
- **Adapt live** to how this league deviates from national ADP — home leagues reliably draft
  differently, and this correction is the largest available edge.

Two explicit traps:
- **Do not use `sd = ADP/4`.** Measured on 2026 data it is right only for the top 12 picks and is
  ~2.2× too wide from round 4 onward, manufacturing phantom urgency and making the tool reach.
- Model pick position as **right-skewed** (shifted-lognormal or gamma) for ADP < 72, where the
  observed low tail sits 3.8 sd below ADP versus 2.6 sd above.

**5 — Roster fit.** Bye-week conflicts among projected starters, and same-team backfield
cannibalization. No full correlation matrix, no stacking model. **Enforce `position_draft_caps`.**

**6 — Guardrails, soft only.** When a recommendation violates conventional structure (a kicker in
round 9, no QB by round 13), flag it and say why the model likes it anyway. **Warn, never block** — a
genuinely contrarian-but-correct pick must still surface.

### 6.3 Latency

**Under 2 seconds from pick received to recommendation on screen.** Precompute continuously between
picks and invalidate on each incoming pick; do not recompute from cold. All draft state lives in
memory. Postgres holds the pre-draft snapshot only.

---

## 7. Presentation tier

One local page, second screen. Under a 30-second clock this is **not** a sortable 200-row table.

### 7.1 Layout

- **The verdict** — one recommended player, large, with 2–3 reason chips.
- **3–5 alternates**, each with one reason and a survival percentage.
- **ADP disagreement is a headline feature**, not a detail: *"the field has him at 34, we have him at
  21, because…"* It is simultaneously the stated goal of the project, the mechanism that makes the
  recommendation trustworthy, and the fastest way to spot a miscalibrated model.
- **"Gone by your turn"** list and positional-run detection, as on-screen panels.
- **Freshness stamp**: `synced 0s ago · ADP 14h old`. ADP is up to 24 hours stale on draft night —
  show it rather than implying it's live.
- **No sound, no browser notifications.** The owner watches the board.

### 7.2 Two measured traps this UI must avoid

**Survival probabilities go to 1.0 exactly when you need them.** When you are on the clock, every
player's chance of surviving *to your current pick* is trivially 100% — so naive scarcity warnings go
silent on the precise screen where the decision is made. In testing this fired on **none of 15
on-the-clock vantages.**

> **Fix:** while on the clock, price every survival number to your **pick after next** — because
> passing is the decision being made — and **label the pick each number refers to** ("survives to your
> pick 43"). One number, one meaning.

**Tier alarms that count players give the backwards answer in both cases that matter.** Three players
each at 30% survival hold with probability `1 − 0.7³ = 65.7%` — "three left, relax" while the tier
evaporates. One deep player nobody wants at 95% survival reads "last one!" when waiting is free.

> **Fix:** fire on **P(at least one survives)**, never on headcount.

**Confidence must visibly degrade in late rounds.** The dispersion fit has no data past ADP 166, and
`times_drafted` collapses from a median of 1,353 (ADP 0–24) to 40 (ADP 150–200). Late-round survival
numbers must not be presented with round-2 authority.

**One number, one meaning.** Two panes honestly quoting differently-horizoned odds for the same player
will disagree on screen with no bug present. Once the reader can't tell which number the ordering
used, the board stops being checkable and gets ignored under time pressure.

### 7.3 Manual pick entry

**Click the player on the board.** Supports undo, for a pick the tap captured wrongly. Manual entries
and tapped frames are the same event type to the engine.

**Reset** clears a draft so it can be run again — for rehearsals, for the simulator, and for the
next season's draft. It deletes that draft's `draft_events` rows and nothing else: a reset of one
draft never touches another. Because the rows are genuinely gone, anything worth learning from a
rehearsal must be read off before resetting it.

### 7.4 Pre-draft mode

Tiered board, the biggest disagreements with ADP, and a slot-specific plan. Note that draft order
isn't published until ~30 minutes before, so slot-dependent planning runs then; everything else works
in advance.

---

## 8. Live draft integration

### 8.1 Manual entry is the substrate, not the fallback

The draft state machine is built against manual entry first. Every other feed is then a pure
accelerator that can fail safely. Built the other way round, a tap that breaks at 7:58pm leaves
nothing.

### 8.2 The tap

A **Tampermonkey userscript** in the owner's own draft-room tab wraps `window.WebSocket` and forwards
frames to `ws://127.0.0.1:8787`. It needs no auth token, opens no second connection, requires no
approval, and works on day one. ~40 lines, frozen by day 3; everything volatile lives behind
localhost, so the engine can be restarted mid-draft without touching the draft tab.

Frames:

| Opcode | Meaning | Payload |
|---|---|---|
| `0` | PICK_MADE | `id\|playerId\|teamId\|pos\|cost` |
| `P` | full-board snapshot | use for reconnect **backfill** — never replay |
| `u` | PICK_UNDONE | |
| `D` | CURRENT_PICK_CHANGED | |
| `H` | HELLO | |

`playerId` here is the **bare numeric id**, not `470.p.{id}` (§2.1).

**Rejected: opening a second spectator socket.** The join frame (opcode `9`) carries `managerId`. If
Yahoo treats a connection as a session and newest-wins, a second client could **evict the owner from
their own draft room** and hand them to autopick. Unverified and untestable without risking a real
draft — which is precisely why tapping the existing socket is the safer architecture.

Note that every existing open-source Yahoo extension hooks `fetch`/XHR/React-fiber instead. That is
the wrong channel; the picks do not travel there.

### 8.3 Recovery

**Recovery must never require reloading the draft-room tab** — that can cost the seat and trigger an
autopick. The instinct when a tool stops updating is to refresh; the design must make that
unnecessary. Reconnect backfills from the `P` frame. HTTP 999 backs off rather than crashing.

### 8.4 Yahoo API application

Submit it anyway, today, as a free option for 2027. Do not schedule around it.

---

## 9. Validation

| What | How | Catches |
|---|---|---|
| **Local draft simulator** | ADP-sampling bot opponents driving full drafts | Engine logic, latency, and the live ADP-deviation adjustment — which fixed replays cannot exercise. Doubles as the regression test. |
| **Yahoo mock drafts** | Full rehearsal against real mocks | The only end-to-end test of the tap, reconnect, and UI under a real clock. **Non-negotiable scope.** |
| **Availability calibration** | Every mock yields hundreds of labelled survival predictions | Tune a single scalar on the cumulative hazard so expected disappearances equal actual picks, then check reliability. |

Both rows above run the same draft more than once, so **reset (§7.3) is a prerequisite for
validation**, not a convenience — without it the second rehearsal has nowhere to go.

**Measure during rehearsal:**
1. End-to-end lag from a pick landing in the room to appearing on the board — *nobody has published
   this number.*
2. That reconnect backfills via the `P` frame rather than replaying.
3. That a 999 backs off instead of crashing.
4. That the engine can be restarted mid-mock without touching the draft tab.

**Mock lobby ids churn continuously** — the id range shifted between two fetches minutes apart, and
rooms leave the lobby the moment they start drafting. The harness must **discover** lobby ids, never
hardcode an `mlid`.

**Do not attempt to prove the projections beat ADP.** It is not provable on this runway, and the
attempt will consume it. The availability model is the one component that *can* be honestly validated
before the draft, because every mock generates labelled data for it.

---

## 10. Build order

| Days | Work | Gate |
|---|---|---|
| 1–2 | Manual pick entry + draft state machine | A full draft can be entered by hand |
| 2–4 | Data pull, ID resolution, scoring under exact league rules | **Config reconciliation passes (§5.1)** |
| 4–6 | Availability model, urgency score, board UI | < 2s recommendation in the simulator |
| 6–7 | Tampermonkey WebSocket tap | Frames land as `draft_events` |
| 8–10 | Rehearsal on live Yahoo mocks | Latency measured, calibration tuned, 999 backoff exercised |

**If time runs short, cut automation before intelligence.** Ship days 1–6 and type the picks — which
is what every commercial incumbent falls back to anyway. Protect the analysis tier and the
recommendation UI.

---

## 11. Risk checklist

- [ ] **Drafting on the Yahoo mobile app would invalidate the entire tap architecture** — there is no
      browser tab to inject into. Confirmed desktop web; re-confirm before draft night.
- [ ] **HTTP 999 on a cold client at 7:59pm.** It can strike with zero prior requests. Back off, retry,
      and never let it crash the process.
- [ ] **Attribute-index shift on ruled-out players** — works in testing, breaks for exactly the players
      that matter.
- [ ] **`position_draft_caps` silently invalidating recommendations** (RB capped at 6 in a sampled
      league).
- [ ] **Mock lobby id churn** breaking the rehearsal harness on its second run.
- [ ] **ADP up to 24h stale** on draft night; late-breaking news will be in the room's heads and not in
      the numbers. Show staleness rather than hiding it.
- [ ] **Thin late-round ADP tail** — no dispersion data past ADP 166; degrade confidence visibly.
- [ ] **The model being rejected on sight.** Under a 30-second clock nobody audits reasoning — you
      either trust the board or override it. **Budget time for explanations, not just for the score.**
- [ ] **Auction drafts are a different program** — `is_auction_draft == 1` means different opcodes
      (`b` bid, `n` nomination, `$` balances), the nominated player is excluded from draft results, and
      the VOR-to-dollars math *is* sensitive to the absolute baseline in a way snake ordering is not.
      Out of scope; assert on the flag and refuse rather than misbehave.

---

## Appendix A — Architecture Decision Record

Recorded so a future reader knows what was chosen and why, and which choices reversed.

**Entries from ADR-24 on live in [`arch/`](arch/), one file per decision.** `ls arch/` is the
index; each file carries the context, the consequences and the alternatives that lost.

Entries 1–23 predate that layout and stay as the table below — the rationales are the ones given
at the time, and expanding them after the fact would invent the parts nobody wrote down. A
decision earns an entry when either holds: it makes a sentence in this spec wrong or incomplete,
or it chose between options whose loser would have produced a different schema, file layout,
dependency, or wire format. Entries are append-only — a reversal is a new file that supersedes
the old one, and the old one is never deleted.

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | Deployment | Local-only, single user | Draft night happens on a laptop; no hosting or multi-tenancy needed |
| 2 | Scope | Draft night only (+ pre-draft prep) | 2–3 week runway; every hour goes to the one night that matters |
| 3 | Source of edge | Optimization over consensus | Custom projections are not buildable without 2026 usage data, and lose to plain consensus in published tests |
| 4 | Objective | **Urgency / VONA** *(reversed from championship equity)* | Full rollout tied its simpler predecessor inside the error bar and shipped disabled — drafters wouldn't follow its picks |
| 5 | Championship equity | Implemented, flag off | Keeps the ambition available without betting draft night on it |
| 6 | Variance | **Market divergence + published error curves + games-played** *(reversed from source disagreement)* | Only one free source publishes component stats — nothing to disagree with |
| 7 | Pick capture | **Manual first, userscript tap second** *(reversed from API polling)* | The OAuth API is unavailable this season; picks exist only on the WebSocket |
| 8 | Degradation | Full offline mode | Makes Yahoo a convenience rather than a dependency |
| 9 | ADP | Yahoo location + FFC dispersion, renormalized | Right room and format; FFC raw marginals overcount by 38% |
| 10 | Data sources | Free and ToS-clean only | Sleeper + Yahoo v3 + FFC + nflverse cover it |
| 11 | Projections | Component stats only | Exact league scoring is the real edge; points-only sources can't be converted correctly |
| 12 | Player identity | nflverse crosswalk + name match + exception file | Sleeper's `yahoo_id` is null for every recent draft class |
| 13 | Storage | Postgres in Docker, daily snapshots, app native | Snapshots enable riser/faller and a future backtest; native app keeps iteration fast |
| 14 | League config | Hand-written + **reconciliation gate** | Private league returns 403; a transcription bug is otherwise invisible |
| 15 | Correlation | Bye weeks + same-team only | The two that matter in redraft; a full matrix isn't estimable here |
| 16 | Guardrails | Soft — warn, never block | Preserves contrarian-but-correct picks |
| 17 | UI | Verdict + 3–5 alternates + one-line why | 30-second clock; a 200-row table is unreadable |
| 18 | Disagreement | Headline feature | The stated goal, the trust mechanism, and the debugging tool at once |
| 19 | Alerts | On-screen panels only, no sound | Owner watches the board |
| 20 | Latency | < 2s | Requires background precompute, not recompute-on-demand |
| 21 | Validation | Local simulator + Yahoo mocks | Different failure modes; the availability model is the only honestly-validatable component |
| 22 | Cut order | Automation before intelligence | A great engine with typed picks beats a weak engine with perfect sync |
| 23 | Engine runtime | **Python 3.14** *(reversed from 3.12)* | Nothing in the dependency graph requires 3.12 — all ship `cp314` wheels or are pure-Python. `nfl-data-py`, the one library that could have justified an older interpreter, pins `pandas<2.0`, whose last release tops out at `cp311`, so it never ran on 3.12 either |

## Appendix B — Open items to verify

1. **Yahoo ADP through a public league id** — confirm `v3 players/nfl/{publicLeagueId}` returns
   Yahoo-wide ADP usable for a different (private) league. One curl.
2. **Actual `draft_pick_duration`** for this league — 30 or 60 seconds. Changes the UI budget.
3. **`position_draft_caps`** for this league.
4. **Desktop web confirmed** for draft night (invalidates §8 if not).
5. Whether last season's per-player awarded points are retrievable from the league site for the §5.1
   reconciliation, and in what form.
