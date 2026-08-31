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
    """
    if ENVELOPE_KEY not in payload:
        raise EnvelopeError(f"no {ENVELOPE_KEY!r} envelope; top-level keys are {sorted(payload)}")
    return payload[ENVELOPE_KEY]
