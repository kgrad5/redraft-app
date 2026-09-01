"""Fantasy Football Calculator's PPR ADP: dispersion shape, never the draft location.

The third source of specs/draft-assistant.md §4.1. It is here for `stdev`, `high`,
`low` and `times_drafted` — Yahoo publishes no dispersion at all — and explicitly not
for its `adp`, which is drawn from a pool whose 3-WR starting lineup does not match
this league's 2WR+FLEX default. Free for any use under a grant dated 2018, attribution
requested, updated once daily.

Two things about the request. `year` is honoured — `year=2025` returns that season's
249 rows — so it is pinned rather than left to the endpoint's current-year default,
which would roll over silently. And `teams` is **not** sent: it is display-only per
specs/draft-assistant.md §2.3, and `teams=8/10/12/14` return byte-identical
`adp`/`times_drafted`/`stdev` with only `adp_formatted` changing.

Every row carries every field non-null, including the 33 past ADP 166 that
specs/draft-assistant.md §2.3 warns are thinly sampled — `times_drafted` bottoms out at
5. The thinness is real but it is not missing data, and making that visible rather than
authoritative is issue #21's job.
"""

from typing import Any

import httpx2

ADP_URL = "https://fantasyfootballcalculator.com/api/v1/adp/ppr"

# 51 of the 271 rows are DEF or PK, which `players` never holds
# (specs/draft-assistant.md §2.2).
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")

# What the ingester reads off every row. `adp` is here because the column is NOT NULL,
# and the dispersion four because they are the only reason this source is ingested.
REQUIRED_KEYS = ("name", "position", "adp", "stdev", "high", "low", "times_drafted")


class PayloadShapeError(Exception):
    """The response is not the object-with-a-players-array this source returns."""


def adp_params(season: int) -> dict[str, Any]:
    """The query. `year` is load-bearing; see the module docstring."""
    return {"year": season}


def ffc_client() -> httpx2.Client:
    """A client for the ADP endpoint. The 50KB body is small; the timeout is habit."""
    return httpx2.Client(timeout=httpx2.Timeout(30.0))


def player_records(payload: Any) -> list[dict[str, Any]]:
    """Return the `players` array, refusing anything else.

    Heard at the boundary rather than downstream: a payload missing the key would
    otherwise read as a source with no players in it, which is indistinguishable from a
    day FFC published nothing.

    Every row is checked for the fields the ingester reads, because the alternative is a
    `KeyError` from the middle of a write loop or — for a null `adp`, which the column
    rejects — an `IntegrityError` at the insert. Both land after the snapshot row is
    written, so ADR-38's rollback takes the payload that would explain them.
    """
    if not isinstance(payload, dict):
        raise PayloadShapeError(f"expected an object, got {type(payload).__name__}")
    players = payload.get("players")
    if not isinstance(players, list):
        raise PayloadShapeError(
            f"expected a 'players' array, got {type(players).__name__}; "
            f"top-level keys are {sorted(payload)}"
        )
    for record in players:
        if not isinstance(record, dict):
            raise PayloadShapeError(f"expected player objects, got {type(record).__name__}")
        missing = [key for key in REQUIRED_KEYS if record.get(key) is None]
        if missing:
            raise PayloadShapeError(
                f"player {record.get('name', '?')!r} is missing {missing}; "
                "every row of the live response carries all of them"
            )
    return players
