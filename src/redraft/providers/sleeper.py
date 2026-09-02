"""The Sleeper projections endpoint: RotoWire component stats for one season.

The first source of specs/draft-assistant.md §4.1, and the one whose payload carries no
`service` envelope — that wrapper is Yahoo v3's alone. What lives here is the source's own
surface: where it is, what it has to be asked for, and which of its stat keys are components.

Two things about the request are load-bearing, both verified live on 2026-08-31.
`season_type` is required: the bare path answers HTTP 400. And the position parameter keeps
its brackets — `position[]` returns the four fantasy positions, while the unbracketed
`position` answers 200 with WR alone. That is a silent 56% narrowing of the board rather
than an error anyone would see, which is the failure class specs/draft-assistant.md §4.2
exists to warn about.
"""

from typing import Any

import httpx2

PROJECTIONS_ROOT = "https://api.sleeper.com/projections/nfl"

# The league rosters no kicker and nflverse carries no team defenses, so neither has
# anything in `players` to join to (specs/draft-assistant.md §2.2).
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE")

# Named exactly rather than excluded by prefix, because `pts_` is not a boundary:
# `pts_allow_0` is a real component — a defense's points-allowed bucket. Naming the
# three means a total under a new name lands in no class and stops the run, which is
# what makes specs/draft-assistant.md §4.2's rule hold by construction.
POINT_TOTALS = frozenset({"pts_std", "pts_ppr", "pts_half_ppr"})
# Draft position, which issue #7 owns. adp_dynasty and adp_rookie are the 999.0
# sentinels specs/draft-assistant.md §2.3 records for 100% of rows.
ADP_PREFIX = "adp_"
COMPONENT_PREFIXES = ("pass_", "rush_", "rec_", "fum_", "bonus_", "cmp_", "pr_", "def_", "idp_")
# Receptions and games played carry no family prefix of their own.
COMPONENT_KEYS = frozenset({"rec", "gp"})
# Games played is a projection of availability, not of production. A record whose only
# component is this one is an ADP shell — Sleeper returns one for every player in its
# pool so the adp_ fields have somewhere to live — and is not a projection.
GAMES_PLAYED = "gp"


class UnknownStatKeyError(Exception):
    """A stat key belonging to none of the three classes. Classify it before ingesting."""


class PayloadShapeError(Exception):
    """The response is not the array of projection records this source returns."""


def projections_url(season: int) -> str:
    return f"{PROJECTIONS_ROOT}/{season}"


def projections_params() -> dict[str, Any]:
    """The query this endpoint requires. Both keys are load-bearing; see the module docstring."""
    return {"season_type": "regular", "position[]": list(FANTASY_POSITIONS)}


def sleeper_client() -> httpx2.Client:
    """A client for the projections endpoint.

    The generous timeout replaces httpx2's 5s default, which is tight for a 2.9MB body:
    this path belongs to the daily job, never to the pick clock of
    specs/draft-assistant.md §2.2.
    """
    return httpx2.Client(timeout=httpx2.Timeout(30.0))


def player_identity(record: dict[str, Any]) -> tuple[str, str]:
    """The `(full_name, position)` the name tiers of specs/draft-assistant.md §4.3 match on.

    Read from the nested `player` object rather than assembled from the request filter.
    Verified against the 2026-08-31 snapshot: all 3,114 records carry `player` with a
    non-null `first_name`, `last_name` and `position`, and the position there is the
    player's own — 70 FB and 1 CB come back despite `position[]` asking for four. Two of
    those FB records carry real components and both resolve on their Sleeper id, so the
    position never reaches a name tier today; one that did would be reported rather than
    matched, which is correct, since `players` holds no fullbacks.

    Absence raises rather than degrading to an unresolvable record. A shape change here
    would otherwise report all 555 records as unmatched — technically true, useless to
    read, and a failure this module's other boundary checks would name properly.
    """
    player = record.get("player")
    if not isinstance(player, dict):
        raise PayloadShapeError(
            f"record {record.get('player_id', '?')!r} carries no `player` object; "
            "the name tiers have nothing to match on"
        )
    missing = [field for field in ("first_name", "last_name", "position") if not player.get(field)]
    if missing:
        raise PayloadShapeError(
            f"record {record.get('player_id', '?')!r} is missing player.{', player.'.join(missing)}"
        )
    return f"{player['first_name']} {player['last_name']}", player["position"]


def component_stats(stats: dict[str, float]) -> dict[str, float]:
    """Return the component subset of one player's stats.

    Every key is classified into exactly one of three sets — fantasy-point total, ADP,
    or component — and a key in none of them raises. Skipping the unrecognised instead
    would drop a new component silently, which is the failure that
    specs/draft-assistant.md §4.3 names for players and which reads no better for a
    stat; writing it instead would let a renamed point total through, which is the one
    thing this ingester exists to prevent. Failing gives up neither.
    """
    components = {}
    for key, value in stats.items():
        if key in POINT_TOTALS or key.startswith(ADP_PREFIX):
            continue
        if key in COMPONENT_KEYS or key.startswith(COMPONENT_PREFIXES):
            components[key] = value
        else:
            raise UnknownStatKeyError(
                f"{key!r} is neither a component, an ADP key, nor a known point total; "
                "add it to one of the three classes in this module before ingesting"
            )
    return components
