"""The shared fetch layer and the three provider seams.

This module carries issue #4's verification check: the `service` envelope is unwrapped,
an HTTP 999 backs off and retries without raising and without its plain-text body ever
reaching a JSON decoder, a 999 on the very first request of a session is survived, and
the raw payload reaches `snapshots` before any parsing runs.

Nothing here touches the network — every client is an `httpx.MockTransport` replaying a
scripted list of statuses. `sleep` is a list's `append`, so a two-minute backoff costs
nothing and the delays it would have taken become assertable.
"""

import json

import httpx
import pytest
import sqlalchemy as sa

from redraft.http import client as http_client
from redraft.http.client import SOURCES, THROTTLE_STATUS, ThrottledError, fetch_json
from redraft.http.envelope import EnvelopeError, unwrap_service

# The shape specs/draft-assistant.md §2.1 records: every v3 response wrapped in `service`.
PAYLOAD = {
    "service": {
        "xml:lang": "en-US",
        "players": [{"name": "Ja'Marr Chase", "average-pick": "2.4"}],
    }
}
# Plain text, not JSON — which is what makes the naive error path crash.
THROTTLE_BODY = "Request denied"
URL = "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v3/players/nfl/12345"

SELECT_SNAPSHOT = sa.text("SELECT source, raw_payload FROM snapshots WHERE snapshot_id = :id")
COUNT_SNAPSHOTS = sa.text("SELECT count(*) FROM snapshots")
INSERT_SOURCE = sa.text(
    "INSERT INTO snapshots (source, fetched_at, raw_payload) "
    "VALUES (:source, clock_timestamp(), '{}'::jsonb)"
)


def make_client(*statuses):
    """A client replaying `statuses` one per request, and the list of what it served.

    `served` is what makes "on the first request of a session" assertable rather than
    asserted-by-docstring: a test can show the 999 was request one with nothing before it.
    """
    remaining = list(statuses)
    served = []

    def handle(request: httpx.Request) -> httpx.Response:
        status = remaining.pop(0)
        served.append(status)
        if status == THROTTLE_STATUS:
            return httpx.Response(status, text=THROTTLE_BODY)
        return httpx.Response(status, json=PAYLOAD)

    return httpx.Client(transport=httpx.MockTransport(handle)), served


@pytest.fixture
def connection(migrated, engine):
    """One transaction per test, always rolled back, so `snapshots` starts empty.

    Tests here assert on row counts, which a committing fixture would couple to
    execution order.
    """
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


def fetch(connection, client, **kwargs):
    return fetch_json(connection, "yahoo", URL, client=client, **kwargs)


def test_service_envelope_is_unwrapped():
    assert unwrap_service(PAYLOAD) == PAYLOAD["service"]


@pytest.mark.parametrize(
    "payload",
    [
        {"players": []},
        # The likeliest shape of a dropped envelope: a bare array of players. It must
        # raise EnvelopeError like any other, not a TypeError the caller cannot catch.
        [{"name": "Ja'Marr Chase"}, {"name": "Bijan Robinson"}],
        "service unavailable",
        None,
    ],
)
def test_unenveloped_payload_is_refused(payload):
    """A v3 response that stopped being enveloped is a shape change, not a pass-through."""
    with pytest.raises(EnvelopeError):
        unwrap_service(payload)


def test_throttle_backs_off_and_retries(connection):
    delays = []
    client, served = make_client(THROTTLE_STATUS, 200)

    snapshot_id, payload = fetch(connection, client, sleep=delays.append)

    assert served == [THROTTLE_STATUS, 200]
    assert delays == [http_client.BACKOFF_SECONDS]
    assert payload == PAYLOAD
    assert snapshot_id is not None


def test_throttle_body_is_never_json_decoded(connection, monkeypatch):
    """The 999 body must not reach a decoder — that is the documented crash.

    The spy replaces the `json` name inside the client module only, so nothing else in
    the process is affected and the count below means exactly what it says.
    """
    decoded = []

    class JsonSpy:
        @staticmethod
        def loads(text, *args, **kwargs):
            decoded.append(text)
            return json.loads(text, *args, **kwargs)

    monkeypatch.setattr(http_client, "json", JsonSpy)
    client, _ = make_client(THROTTLE_STATUS, 200)

    fetch(connection, client, sleep=lambda _: None)

    assert len(decoded) == 1, f"the throttle body reached the JSON decoder: {decoded}"
    assert THROTTLE_BODY not in decoded[0]


def test_throttle_on_first_request_is_survived(connection):
    """A cold client whose very first response is 999.

    specs/draft-assistant.md §2.1 records that this fires with zero prior volume, and
    specs/draft-assistant.md §11 names it as a draft-night risk. Nothing in `fetch_json`
    tracks warm-up, so this is not a special case — which is precisely why it survives.
    """
    delays = []
    client, served = make_client(THROTTLE_STATUS, 200)

    _, payload = fetch(connection, client, sleep=delays.append)

    assert served[0] == THROTTLE_STATUS, "the throttle was not the session's first request"
    assert payload == PAYLOAD
    assert delays == [http_client.BACKOFF_SECONDS]


def test_exhausted_throttle_raises(connection):
    """Retry is not infinite. Backoff happens between attempts, never after the last."""
    delays = []
    client, served = make_client(*[THROTTLE_STATUS] * 3)

    with pytest.raises(ThrottledError):
        fetch(connection, client, attempts=3, sleep=delays.append)

    assert served == [THROTTLE_STATUS] * 3
    assert delays == [http_client.BACKOFF_SECONDS] * 2


def test_raw_payload_is_stored_enveloped(connection):
    """ADR-36: what lands in `raw_payload` is the body as it arrived, envelope included.

    A snapshot taken after unwrapping would hide exactly the bug class an envelope
    change produces.
    """
    client, _ = make_client(200)

    snapshot_id, _ = fetch(connection, client)

    row = connection.execute(SELECT_SNAPSHOT, {"id": snapshot_id}).one()
    assert row.source == "yahoo"
    assert "service" in row.raw_payload, "the envelope was stripped before the write"
    assert row.raw_payload == PAYLOAD


def test_snapshot_is_written_before_parsing(connection):
    """The row is written and readable before anything parses it.

    `fetch_json` does not return until the INSERT has run, so the ordering is
    structural; this asserts the observable consequence of it.

    Written, not durable: the INSERT is on the caller's transaction, so a caller that
    later rolls back discards this row too. `fetch_json`'s docstring says what that
    costs and leaves the choice to #9.
    """
    client, _ = make_client(200)

    snapshot_id, payload = fetch(connection, client)

    stored = connection.execute(SELECT_SNAPSHOT, {"id": snapshot_id}).one()
    assert stored.raw_payload == PAYLOAD

    # Only now does anything parse, and the stored row is unaffected by it.
    assert unwrap_service(payload) == PAYLOAD["service"]


def test_throttled_fetch_writes_no_snapshot(connection):
    """ADR-36: a throttling episode leaves no trace in the database."""
    client, _ = make_client(*[THROTTLE_STATUS] * 2)

    with pytest.raises(ThrottledError):
        fetch(connection, client, attempts=2, sleep=lambda _: None)

    assert connection.execute(COUNT_SNAPSHOTS).scalar_one() == 0


def test_error_status_writes_no_snapshot(connection):
    """A private league answers 403 (specs/draft-assistant.md §2.1). It is not a throttle,
    so it raises immediately rather than backing off — and records nothing."""
    delays = []
    client, served = make_client(403)

    with pytest.raises(httpx.HTTPStatusError):
        fetch(connection, client, sleep=delays.append)

    assert served == [403], "a 403 was retried as though it were a throttle"
    assert delays == []
    assert connection.execute(COUNT_SNAPSHOTS).scalar_one() == 0


def test_source_literals_match_the_check_constraint(connection):
    """ADR-31's closed set is duplicated in Python; this is the drift guard.

    Every member of `SOURCES` must be one the constraint accepts, and nothing outside
    it may be written. The aborted transaction at the end is why this assertion is last.
    """
    for source in SOURCES:
        connection.execute(INSERT_SOURCE, {"source": source})
    assert connection.execute(COUNT_SNAPSHOTS).scalar_one() == len(SOURCES)

    with pytest.raises(sa.exc.IntegrityError):
        connection.execute(INSERT_SOURCE, {"source": "fantasypros"})
