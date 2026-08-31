"""The `service` envelope Yahoo v3 wraps around every response."""

from typing import Any

ENVELOPE_KEY = "service"


class EnvelopeError(Exception):
    """A v3 response not shaped the way specs/draft-assistant.md §2.1 records."""


def unwrap_service(payload: Any) -> Any:
    """Return the body inside the `service` envelope.

    A payload without the key is refused rather than passed through. Yahoo dropping the
    envelope would be a shape change every parser downstream needs to hear about at the
    boundary; passing it through hands them a payload one level off, which reads as
    missing data rather than as a broken assumption.

    The type check comes first because a dropped envelope most likely arrives as a bare
    array of players, and reaching `sorted()` on one raises TypeError from inside this
    function's own error message — an exception the caller cannot catch as EnvelopeError,
    which is the whole point of raising one.
    """
    if not isinstance(payload, dict):
        raise EnvelopeError(f"expected a {ENVELOPE_KEY!r} envelope, got {type(payload).__name__}")
    if ENVELOPE_KEY not in payload:
        raise EnvelopeError(f"no {ENVELOPE_KEY!r} envelope; top-level keys are {sorted(payload)}")
    return payload[ENVELOPE_KEY]
