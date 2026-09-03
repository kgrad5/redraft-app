# ADR-53 — The daily run is four independent source attempts

**Status:** Accepted · 2026-09-02 · issue #9

**Context**
ADR-37 spent this issue in advance: "#9's daily job only chooses which providers to run and what to
do when one fails", and "#9 shrinks to a loop and a failure policy." Nothing in `src/` has ever
called an ingester, so this is the first caller and the first place four accepted entries meet.
ADR-38 handed the transaction to the caller and named #9 as the caller who must answer for it.
ADR-40 took nflverse off the snapshot path entirely: a function returning a row count, with no
`snapshots` row to report. ADR-41 asked outright "whether one dead provider stops the whole job".
ADR-49 recorded that "issue #9 is the caller" of the unmatched-player report that
specs/draft-assistant.md §4.3 requires on every run.

What decides the shape is not the loop but what the loop is for. specs/draft-assistant.md §3: "If
Yahoo, the network, or the tap dies, the draft continues on manual entry with **no degradation in
recommendation quality**." specs/draft-assistant.md §4 puts the second caller of the same loop — the
"refresh now" button — in the minutes before the draft.

Four facts about the code as it stands constrain the answer.

- The three JSON ingesters build their `Resolver` from `SELECT ... FROM players`, and only
  `ingest_players` writes that table. Against an empty pool every tier misses on every record and
  each ingester raises its `Empty*Error` — after paying for a fetch and a snapshot INSERT that then
  roll back. (That is true today because `data/id_exceptions.yaml` ships with no entries; a single
  entry against an empty pool raises `ExceptionFileError` at the same point instead.)
- A DBAPI-level failure aborts the Postgres transaction; SQLAlchemy deactivates it and every later
  `execute` on that `Connection` raises `PendingRollbackError`. The reachable case is not a duplicate
  `adp` row — both ADP ingesters key their rows by `player_id` and resolve with `on_duplicate="raise"`
  precisely so `DuplicateResolutionError` fires first. It is the `players` UPSERT, whose `ON CONFLICT`
  targets `nflverse_id` alone while `sleeper_id` and `yahoo_num_id` carry UNIQUE constraints of their
  own; and it is any dropped connection.
- `DefaultDialect.do_begin` is `pass`, so `engine.begin()` opens no server-side transaction until the
  first statement, and in every ingester here the fetch precedes it. ADR-38's "holds the caller's
  transaction open ... along with any locks it has taken" therefore bites only when an *earlier*
  source in the same transaction has already written.
- The exception surface is not enumerable. Three unrelated classes are called `PayloadShapeError` —
  one each in `providers.sleeper`, `providers.yahoo` and `providers.ffc`, sharing no base — beside
  `ThrottledError`, `UnknownStatKeyError`, `ExceptionFileError`, `DuplicateResolutionError`,
  `EmptyProjectionsError`, `EmptyAdpError`, `EmptyTableError`, `IngestError`, the `httpx2` and
  `sqlalchemy.exc` families, and bare `KeyError`, `TypeError` and `ValueError` from `fetch_csv`'s
  deliberately keyed access. `EnvelopeError` is not among them: `src/redraft/http/envelope.py` still
  has no caller in `src/`, as ADR-44 recorded.

**Decision**
A run is one pass over four sources in a fixed order — nflverse, then Sleeper, Yahoo and FFC in
specs/draft-assistant.md §4.1's own order — each inside its own `engine.begin()`. A source that
raises is caught at `Exception`, recorded as a failed `SourceRun`, and the run continues; nothing
re-raises, and no source's failure changes what any other source wrote. A run returns one `SourceRun`
per source whatever happened, in the order they ran, and that list is both what
`python -m redraft.jobs.daily` prints and what `POST /refresh` answers with, at 200 either way.
nflverse's entry carries `snapshot_id = null`, because ADR-40 gives it none to carry. The run emits
one `unmatched_report` across every source that returned, on stdout, on every run including a clean
one and including the endpoint's. The process exits 1 if any source failed. Dailiness is a crontab
line running `make snapshot`; nothing in the process schedules anything and no table records that a
run happened. **Nothing commits a snapshot ahead of its parse.**

**Consequences**
- **specs/draft-assistant.md §4's "A daily scheduled job plus an on-demand 'refresh now' button" is
  now incomplete.** The repo holds the job and the button and no schedule at all: recurrence is a
  crontab line the operator installs by hand, documented in the Makefile beside the target. A fresh
  clone that is never `crontab -e`'d produces snapshots only when someone presses refresh, and
  specs/draft-assistant.md §3's diagram draws `daily job` as a box inside the system when it is now
  outside it.
- **A run is no longer atomic, and that is the decision rather than a side effect.** Three sources
  landing and one failing is the outcome specs/draft-assistant.md §3 asks for. The price is that
  "what the model saw on day D" can be three sources deep one day and four the next, and nothing in
  the database says which. specs/draft-assistant.md §7.1's freshness stamp shows how old the ADP is,
  so a stale source surfaces there as an older number — but it does not say *which* source is old.
  Any reader (#19) must select per source and must not assume the three arrive together.
- **"Record and continue" is only implementable per transaction.** Under one run-wide transaction a
  DBAPI-level failure in nflverse — the first source — leaves every later `execute` raising
  `PendingRollbackError`, so the policy would work for a `ValueError` and fail silently for the whole
  DBAPI half of the surface. `begin_nested()` savepoints would rescue it and are machinery nothing
  else here needs.
- **A throttle now holds no locks.** Each source's first statement follows its own fetch, so a
  six-minute backoff holds a checked-out pool connection and nothing else. Under one run-wide
  transaction nflverse's `players` row locks would already be taken when Yahoo's backoff began.
- **The catch is `Exception`, deliberately, and `BaseException` deliberately is not caught.** An
  honest tuple over the surface in Context goes stale the moment an upstream shape change adds a
  class, and it goes stale by aborting the run — the mode ADR-41 warned this issue about. It will
  also swallow a bug in the job itself, so each failure prints its traceback to stderr beside the
  one-line summary. An interrupt still stops the process rather than being recorded as four failures.
- **ADR-41's "A key added upstream fails the daily run until someone classifies it" is now narrower.**
  An `UnknownStatKeyError` fails Sleeper's leg only: the run prints three `ok` lines and one `FAILED`,
  exits 1, and on the refresh path answers 200 with one failed entry of four. The loudness ADR-41
  bought is reduced to one source, which is the question it deferred here and this is the answer.
- **nflverse runs first and its failure is not fatal.** On a virgin database that is transitively
  fatal anyway — an empty `players` resolves nobody, so all three JSON sources raise and the run
  costs three fetches and three rolled-back snapshots for nothing. Accepted, because it happens once.
  On a populated database ADR-40's in-place upsert leaves yesterday's pool standing and the JSON
  sources resolve against it for everyone it already holds, missing only players added upstream since
  — the same gap ADR-49 records for the one top-200 player no tier can place. Aborting instead would
  throw away three good pulls over a player dimension stale by a day.
- **The refresh button writes the player dimension, in place, minutes before the draft.** ADR-40 makes
  `players` the one non-snapshotted, non-reconstructible write in the system, and a `CUT` or `RET`
  status blanks `team` and therefore `bye_week` — the correlation input of specs/draft-assistant.md
  Appendix A entry 15. Accepted rather than overlooked: the issue asks for a refresh that runs every
  ingester, a roster move on draft morning is exactly what the button exists to pick up, and a
  threadpool handler is not cancelled by a client disconnect, so the write completes whether or not
  the operator is still watching.
- **The three JSON sources run in specs/draft-assistant.md §4.1's order and not a "draft-night" one.**
  Nothing measured distinguishes them: Yahoo's 4.2MB body is the largest and has never been observed
  to throttle (ADR-44), Sleeper's 2.9MB is the one with a published ceiling, and FFC's 50KB publishes
  once daily. Any priority ordering would be a claim the record cannot support.
- **A failed source contributes nothing to the unmatched-player report and `unresolved: 0` to the
  wire.** Its `Resolver` is a local inside `ingest()` and dies with the exception, so the run where
  FFC placed nobody and raised `EmptyAdpError` prints `unmatched players: none` — the string
  `identity/report.py` chose so a clean run would not look like a job that never ran now also looks
  like a total failure. Unfixable from here under ADR-37's seam. Mitigated only by printing each
  source's outcome line above the report, so the report is never the run's whole verdict, and pinned
  by a test.
- **ADR-38's replay debt is re-deferred, not paid, and not by claiming the option is unavailable.**
  ADR-38's "#9 can still choose this per provider — the seam does not forbid it" stands; what #9
  declines is taking it. ADR-37 puts the fetch, the snapshot INSERT and the parse inside one
  `ingest()` call, so pre-committing from here means an event listener on `INSERT_SNAPSHOT` or a
  second connection threaded through — both edits to `projections.py` and `adp.py`, where each
  ingester already recorded its own reasoned no-pre-commit choice, and both outside this issue.
  specs/draft-assistant.md §4.2's "so a parser change can be replayed without re-fetching" therefore
  still does not hold for a run that fails in the parse, and after this issue nobody owns it. The
  trigger named on the seam has not fired: `YahooADP.ingest` says "Issue #9 owns revisiting that if
  999 ever fires here", and ADR-44 records that this host has never been observed to throttle.
  `FfcADP.ingest` is the strongest future candidate — the one source that publishes once daily, so a
  re-fetch cannot recover a different payload, and the only one of the three whose docstring never
  argued the point.
- **The wire format is six fields per entry, four entries, always.** `source`, `snapshot_id`,
  `rows_written`, `unresolved`, `failed`, `error`. `rows_written` carries three units in one field:
  `projections` stat rows for Sleeper, `adp` player rows for Yahoo and FFC, `players` rows upserted
  for nflverse. The unmatched *records* do not cross — ADR-49 derived `unresolved` from them so a
  count and a report could not disagree, and carrying both again one layer up would reinstate exactly
  that drift, in a per-source structure that cannot hold a document global to the run. A
  `ThrottledError` and an `EmptyAdpError` both present as `snapshot_id: null, rows_written: 0`, and
  only the class name inside `error` separates them; ADR-36 already noted a throttle leaves no
  database trace, and this endpoint is the first surface that could have shown one and does not.
- **`IngestResult` is untouched**, and so is `fetch_json`. Widening `snapshot_id` to `int | None` so
  one uniform loop could cover all four sources would put ADR-40's exception on a seam ADR-37 fixed
  for three sources that always have one, and on every future consumer of it.
- **The handler is a plain `def`.** ADR-38 names what the same blocking call from an `async def`
  would do to the event loop, and stalling it would take `/health` and every manual pick down — the
  failure specs/draft-assistant.md §3 forbids. The cost is one threadpool worker held for the whole
  run: three sources' throttle backoff plus nflverse's requests under a *per-phase* 30-second timeout
  (ADR-39), which is well over twenty minutes in the worst case and is not shortened here, because
  `fetch_json`'s `attempts`/`backoff` are not threaded through ADR-37's seam. ADR-39's remaining
  timeout obligation on #9 is discharged rather than re-opened: all four client factories already set
  `httpx2.Timeout(30.0)` and each records that its path belongs to the daily job and never to the
  pick clock of specs/draft-assistant.md §2.2.
- **A second database-dependency shape enters the API layer.** `api/picks.py` yields a request-scoped
  `Connection`; `api/refresh.py` yields an `Engine` and opens four transactions of its own. Every
  future router now has two precedents, and a test must override the exact function object its router
  uses — overriding `picks.get_connection` from a refresh test does nothing at all.
- **Nothing detects a run that never happened, and a failed run can leave no trace whatsoever.**
  `max(fetched_at)` per source answers "did the job fire" for the three JSON sources only; ADR-40
  gives nflverse no `fetched_at` anywhere, so a run in which only nflverse succeeded is invisible to
  every query. cron mails a job's *output*, not its exit status, and only where an MTA is configured —
  which is not the default on this machine — so the documented crontab line appends to a log file and
  that file, not mail, is the review surface specs/draft-assistant.md §4.3 asks for. `make` also
  reports a failed recipe as exit 2, not the job's 1.
- **Nothing serialises two runs.** Two refresh presses, or a press during the cron window, produce two
  snapshots per source; `adp`'s primary key is `(snapshot_id, player_id)` (ADR-30) so they cannot
  collide, and `players` is an idempotent upsert. An advisory lock was considered and dropped: one
  operator, one machine. A future riser/faller query assuming one snapshot per source per day is
  where this will surface.
- **`season` and `yahoo_game_key` enter `Settings` with defaults rather than as required fields**,
  because `Settings()` is constructed at import time by `db/session.py`, `migrations/env.py` and
  `tests/conftest.py`, and the Makefile's `.env` rule deliberately never re-copies `.env.example`.
  Requiring them would break `make test`, `make dev` and every Alembic command for anyone whose
  `.env` predates this change. The consequence is that an existing `.env` gains nothing and the
  defaults are what run — right for 2026 and 470, and two edits rather than one when the season rolls.
  `league_config.season` remains #10's, and this field is the *ingest* season, not the league's
  configured one; when #10 lands, one of the two has to win.
- **This entry carries the run's shape, its wire format and its schedule together**, where the house
  writes one file per decision, because they are three answers to one question — what a run is and
  when it happens — and each is load-bearing for the others: the failure policy forces the transaction
  boundary, and the transaction boundary is what makes a per-source result list meaningful.

**Alternatives rejected**
- **One transaction for the whole run.** It is the shape `api/picks.py` already uses and keeps a day's
  four writes atomic, which is the property ADR-38 values for one ingest. Rejected: at four sources
  the property inverts — one `EmptyAdpError` discards three healthy sources' work — and a DBAPI-level
  failure makes the failure policy this entry exists to record unimplementable without savepoints.
- **Abort the run on the first failure**, letting the exception leave the loop and the process die
  with a traceback. Loud, and half the code. Rejected on specs/draft-assistant.md §3: it makes every
  source a dependency of every other, and turns a dead Sleeper into no Yahoo ADP at the moment the
  draft starts.
- **Commit each snapshot on a second connection before parsing** — ADR-38's own other arm. It is what
  specs/draft-assistant.md §4.2 wants most. Rejected on cost rather than possibility: from here it is
  an event listener on `INSERT_SNAPSHOT` or a connection threaded through `ingest()`, so the honest
  form of the change is an edit to the three ingesters, in an issue whose file list does not include
  them, to buy a replay path nothing yet reads.
- **`connection.begin_nested()` savepoints inside one shared transaction.** Rescues the shared shape
  and keeps a single commit. Rejected as machinery for a once-a-day single-operator job, and it leaves
  the lock hold in place.
- **An adapter class giving `ingest_players` an `ingest()` method**, so all four sources go through one
  uniform loop. Rejected twice over: it has no snapshot id to return, so it must invent one or widen
  the seam ADR-37 fixed, and it is a class wrapping a single call with a single caller.
- **Run the three JSON sources first, or nflverse last.** It would spare three wasted fetches on a
  virgin database. Rejected: it spares them exactly once and pays for it every day after, because the
  pool the three resolve against would then always be a day older than the one just fetched.
- **An in-process scheduler** — APScheduler, or a FastAPI lifespan task. It makes "the app is running"
  mean "snapshots are being taken". Rejected: it fires only while uvicorn is up, needs
  process-singleton handling under `--reload`'s two processes, puts a background thread inside the
  process that serves the draft, and adds a dependency to do what six words of crontab already do.
  `make snapshot` is both the scheduled path and the manual one, so the two cannot diverge.
- **A `runs` table** recording each pass with one snapshot id per source hanging off it. It would make
  a partially-failed day queryable, answer "did the job fire" including for nflverse, and give the
  endpoint a single id to return. Rejected by the owner: it is a schema change, a migration and a read
  path in service of a report nothing consumes yet, and the per-source list already answers the
  question actually asked — did each source land, and what is its new snapshot.
- **A `202 Accepted` refresh returning a handle to a background run.** The textbook answer to a request
  that can block for minutes. Rejected: the endpoint is asked to return the snapshot ids a query finds
  a moment later, so the answer has to be the finished run — and a handle needs somewhere to keep run
  state, which is the `runs` table above.
- **Excluding nflverse from the refresh button**, so the pre-draft press touches no in-place write.
  It removes the one destructive write from the riskiest moment. Rejected: a roster move on draft
  morning is exactly what that press is for, and a button whose four-entry answer silently became
  three would be a second wire format for the same route.
- **A pre-loop `load_exceptions` guard**, so a typo in `data/id_exceptions.yaml` costs zero fetches
  rather than three. One line, no abstraction. Rejected: single operator, drop the guards. The file
  ships with zero entries so the guard fires on nothing today, and a broken one already shows up as
  three identical `ExceptionFileError` lines naming the bad entry.
