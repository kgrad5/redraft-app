"""Yahoo's public draft-analysis pool: the ADP location a Yahoo room actually drafts to.

The second source of specs/draft-assistant.md §4.1, and the one the spec describes
wrongly. There is no `v3 players/nfl/{publicLeagueId}` — every path under that
description answers 404, and the URL issue #4 left in the test suite was a
MockTransport placeholder rather than a measured endpoint. What works is the **v2**
read-only host the logged-out Draft Analysis page calls client-side, addressed through
`{game_key}.l.public`: a pseudo-league Yahoo publishes per game key. No carrier league
id is needed and none is configured (ADR-44).

Three things about the request are load-bearing, all verified live on 2026-08-31.

`format=json_f` renders each player as a keyed object. The documented `format=json`
renders it as a positional array, which is where the hazard of
specs/draft-assistant.md §4.2 lives: `display_position` sits at index `[12]` for a
healthy player and `[14]` for one carrying `status` and `injury_note`. The 1,195
records span 13 distinct key-sets, so that shift is not an edge case — it is most of
the board. Asking for keyed objects means there is no array for a later edit to index
into (ADR-45).

`position=ALL` keeps the pull to one request and therefore one snapshot, which is what
ADR-30 requires of a source's fetch. Yahoo's `position` parameter takes a single value,
so filtering server-side the way ADR-43 does for Sleeper would mean four snapshots.

And the ADP itself is a **string**: 969 of the 1,195 records carry the literal `"-"`
where a number belongs, so `float()` on it raises for 81% of the pool (ADR-47).
"""

from typing import Any

import httpx2

PLAYERS_ROOT = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2"

# The league rosters no kicker and nflverse carries no team defenses, so Yahoo's K and
# DEF records have nothing in `players` to resolve to (specs/draft-assistant.md §2.2).
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")

ENVELOPE_KEY = "fantasy_content"
# What the spec calls the ADP: the preseason figure rather than the live one. Both are
# present on every record and they differ by at most 1.5 picks today; the unstored one
# stays reachable in snapshots.raw_payload (ADR-47).
ADP_KEY = "preseason_average_pick"
# Not a number, not null, and on most of the pool.
NO_ADP = "-"


class PayloadShapeError(Exception):
    """The response is not the keyed-object pool this source returns under json_f."""


def players_url(game_key: int) -> str:
    """The pool for one game key. 470 is 2026 (specs/draft-assistant.md §2.1).

    The matrix parameters live in the path rather than the query string because that is
    where Yahoo puts them; httpx2 passes the semicolons through unaltered.
    """
    return (
        f"{PLAYERS_ROOT}/league/{game_key}.l.public"
        "/players;position=ALL;start=0;count=2000;sort=average_pick"
        "/draft_analysis"
    )


def players_params() -> dict[str, Any]:
    """Keyed objects, not positional arrays. See the module docstring."""
    return {"format": "json_f"}


def yahoo_client() -> httpx2.Client:
    """A client for the draft-analysis pool.

    The generous timeout replaces httpx2's 5s default, which is tight for a 4.2MB body:
    this path belongs to the daily job, never to the pick clock of
    specs/draft-assistant.md §2.2. No User-Agent is set — httpx2's own answers 200 with
    a byte-identical body to a browser's, so ADR-39's flagged fingerprint change does
    not fire here.
    """
    return httpx2.Client(timeout=httpx2.Timeout(30.0))


def player_records(payload: Any) -> list[dict[str, Any]]:
    """Unwrap the pool, refusing any shape but the one json_f returns.

    The envelope is `fantasy_content`, not the `service` wrapper
    specs/draft-assistant.md §2.1 describes — that shape belongs to some other endpoint
    and never arrives here (ADR-44).

    Every step is checked rather than assumed, because the failure this guards against
    is `format=json`, whose `league` is a list of sections rather than an object. Left
    unchecked, that payload does not raise here; it raises somewhere downstream reading
    a position off the wrong offset, which is the bug specs/draft-assistant.md §4.2
    exists to prevent.
    """
    if not isinstance(payload, dict) or ENVELOPE_KEY not in payload:
        raise PayloadShapeError(
            f"no {ENVELOPE_KEY!r} envelope; got {type(payload).__name__}"
            + (f" with keys {sorted(payload)}" if isinstance(payload, dict) else "")
        )
    league = payload[ENVELOPE_KEY].get("league")
    if not isinstance(league, dict):
        raise PayloadShapeError(
            f"expected a keyed 'league' object, got {type(league).__name__} — "
            "this is what format=json returns, and nothing here parses it"
        )
    players = league.get("players")
    if not isinstance(players, list):
        raise PayloadShapeError(f"expected a 'players' array, got {type(players).__name__}")
    return [entry["player"] for entry in players]


def adp_value(record: dict[str, Any]) -> float | None:
    """This record's ADP, or None when Yahoo publishes none for it.

    `"-"` is the common case rather than the exceptional one, so it is returned as
    "no ADP" instead of raising. A record without one is not a player the crosswalk
    failed to place, and ADR-47 keeps it off the `unresolved` count for that reason.
    """
    raw = record["draft_analysis"].get(ADP_KEY)
    if raw is None or raw == NO_ADP:
        return None
    try:
        return float(raw)
    except TypeError, ValueError:
        return None
