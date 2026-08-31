"""The shared fetch layer every JSON ingester goes through (nflverse is CSV and bypasses it; ADR-40).

Three hazards from specs/draft-assistant.md §2.1 are handled here once rather than in
each of #5, #6 and #7: the throttle signal is HTTP 999 and not 429, its body is plain
text and not JSON, and it can strike the very first request of a session before clearing
in about two minutes. Handled the standard way, the first fact means the throttle is
never recognised and the second means the process crashes when it is.

Every response that will be parsed is persisted first, so a parser change can be replayed
off `snapshots` without re-fetching (specs/draft-assistant.md §4.2). A throttle writes
nothing, because it produces no rows for a snapshot to sit beside (ADR-36).
"""

import json
import time
from collections.abc import Callable
from typing import Any, Literal, get_args

import httpx2
from sqlalchemy import Connection, bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

# Mirrors the CHECK constraint on snapshots.source (ADR-31), which is what actually
# enforces it. This is the name the call sites use and the type a provider declares.
Source = Literal["sleeper", "yahoo", "ffc", "nflverse"]
SOURCES = get_args(Source)

# "Request denied". Not 429, and not User-Agent dependent.
THROTTLE_STATUS = 999
# It clears in about two minutes; it is a stochastic WAF event, not a volume ceiling.
BACKOFF_SECONDS = 120.0
MAX_ATTEMPTS = 4

INSERT_SNAPSHOT = text(
    "INSERT INTO snapshots (source, fetched_at, raw_payload) "
    "VALUES (:source, clock_timestamp(), :raw_payload) "
    "RETURNING snapshot_id"
    # Explicit rather than relying on psycopg's default adapter for a bare dict.
).bindparams(bindparam("raw_payload", type_=JSONB))


class ThrottledError(Exception):
    """Every attempt came back 999. The caller decides whether that is fatal."""


def fetch_json(
    connection: Connection,
    source: Source,
    url: str,
    *,
    client: httpx2.Client,
    params: dict[str, Any] | None = None,
    attempts: int = MAX_ATTEMPTS,
    backoff: float = BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, Any]:
    """GET `url`, snapshot the body, and return `(snapshot_id, payload)`.

    The payload comes back still enveloped. Unwrapping and row extraction are the
    caller's, and they happen after the write, which is what makes the snapshot a record
    of what arrived rather than of what was understood.

    Nothing here tracks how many requests the session has made, which is exactly why a
    999 on the first one needs no special case: the first request of draft night and the
    thousandth take the identical path.

    `sleep` is a parameter so a test can assert the backoff that would have been taken
    without taking it.

    The caller owns the transaction, and two consequences follow that #9 has to answer.
    A parse that raises inside that transaction takes the snapshot with it — which is
    the failure specs/draft-assistant.md §4.2's replay-without-refetch is most wanted
    for — so a caller that wants the payload kept regardless must commit it separately.
    And a sustained throttle blocks this thread in `sleep` for up to
    `(attempts - 1) * backoff`, six minutes on the defaults, with that transaction held
    open the whole time.
    """
    for attempt in range(1, attempts + 1):
        response = client.get(url, params=params)
        # Ahead of raise_for_status(), which would turn a 999 into an exception, and
        # ahead of any access to the body, which is the plain text that crashes a
        # decoder. The status code is the only thing a throttle can be read from.
        if response.status_code == THROTTLE_STATUS:
            if attempt == attempts:
                raise ThrottledError(f"{url} answered {THROTTLE_STATUS} on all {attempts} attempts")
            sleep(backoff)
            continue
        response.raise_for_status()
        break

    payload = json.loads(response.text)
    snapshot_id = connection.execute(
        INSERT_SNAPSHOT, {"source": source, "raw_payload": payload}
    ).scalar_one()
    return snapshot_id, payload
