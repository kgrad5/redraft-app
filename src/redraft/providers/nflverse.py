"""The nflverse release assets: player universe, weekly rosters, schedule.

The fourth source of specs/draft-assistant.md §4.1, and the one that is CSV rather
than JSON. It bypasses `fetch_json` and writes no `snapshots` row (ADR-40): the
payload cannot land in a JSONB column, the artifacts are re-downloadable at stable
URLs, and GitHub never answers 999, so the shared throttle handling has nothing to
do here either.

A table that does not exist upstream is a plain 404 — `raise_for_status` turns it
into the loud failure issue #5 requires. The subtler way a run could go quiet is an
asset that exists but carries only a header, which is what `EmptyTableError` is for.
"""

import csv
import io

import httpx2

RELEASE_ROOT = "https://github.com/nflverse/nflverse-data/releases/download"

PLAYERS_URL = f"{RELEASE_ROOT}/players/players.csv"
SCHEDULE_URL = f"{RELEASE_ROOT}/schedules/games.csv"


def roster_url(season: int) -> str:
    return f"{RELEASE_ROOT}/weekly_rosters/roster_weekly_{season}.csv"


class EmptyTableError(Exception):
    """The asset downloaded fine but carried no data rows."""


def nflverse_client() -> httpx2.Client:
    """A client for the release assets.

    follow_redirects because GitHub 302s every asset to a storage host, and httpx2
    neither follows by default nor lets `raise_for_status` pass a redirect (ADR-39).
    The generous timeout is fine: this path belongs to the daily job, never to the
    pick clock of specs/draft-assistant.md §2.2.
    """
    return httpx2.Client(follow_redirects=True, timeout=httpx2.Timeout(30.0))


def fetch_csv(client: httpx2.Client, url: str) -> list[dict[str, str]]:
    """GET `url` and return its rows keyed by header name.

    Keyed access is deliberate: a column that moves or disappears upstream becomes
    a KeyError at the point of use, not a silently shifted value — the failure mode
    specs/draft-assistant.md §4.2 records for positional indexing.
    """
    response = client.get(url)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows:
        raise EmptyTableError(f"{url} returned no data rows")
    return rows
