"""Populate `players` from the nflverse artifacts (issue #5).

The universe is a union, because neither source table contains the other: weekly
roster rows carry the crosswalk ids (the players dataset stopped carrying them),
and the players dataset carries the free agents no roster row mentions. Positions
are QB/RB/WR/TE only — the league rosters no kicker, and specs/draft-assistant.md
§2.2 records that the projection tiers can ignore the position entirely. Team
defenses do not exist in nflverse at all; they are a later issue's problem.

Everything here fails loudly rather than quietly narrowing the board: a missing
table 404s, a header-only table raises, a roster team without exactly one derivable
bye raises, and a row without a gsis_id raises instead of being dropped — a
silently dropped player is a player missing on draft night
(specs/draft-assistant.md §4.3).

`players` is upserted in place on the caller's transaction, with no snapshot row
and no commit here (ADR-40, and the ADR-38 house pattern).
"""

from collections import defaultdict

import httpx2
from sqlalchemy import Connection, text

from redraft.providers.nflverse import PLAYERS_URL, SCHEDULE_URL, fetch_csv, roster_url

FANTASY_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})
# A roster row with one of these statuses still carries the player's ids, but the
# player is a free agent: the row confers no team, and therefore no bye.
UNROSTERED_STATUSES = frozenset({"CUT", "RET"})

UPSERT = text(
    "INSERT INTO players"
    " (full_name, team, position, bye_week, nflverse_id, sleeper_id, yahoo_num_id)"
    " VALUES (:full_name, :team, :position, :bye_week, :nflverse_id, :sleeper_id, :yahoo_num_id)"
    " ON CONFLICT (nflverse_id) DO UPDATE SET"
    "  full_name = EXCLUDED.full_name,"
    "  team = EXCLUDED.team,"
    "  position = EXCLUDED.position,"
    "  bye_week = EXCLUDED.bye_week,"
    # A crosswalk id, once known, never un-becomes known just because the upstream
    # column went empty. team is different: a cut is a real change and NULL must win.
    "  sleeper_id = COALESCE(EXCLUDED.sleeper_id, players.sleeper_id),"
    "  yahoo_num_id = COALESCE(EXCLUDED.yahoo_num_id, players.yahoo_num_id)"
)


class IngestError(Exception):
    """The upstream data cannot mean what this ingest requires it to mean."""


def bye_weeks(games: list[dict[str, str]], season: int) -> dict[str, int]:
    """Each team's bye: the single regular-season week it does not play.

    The week universe is whatever REG weeks the season's rows contain, so the
    arithmetic holds for the fixture mini-league and the real 18-week season alike.
    A team missing anything but exactly one week has no derivable bye, and that is
    an error rather than a guess — issue #5's verification hangs on it.
    """
    regular = [g for g in games if g["season"] == str(season) and g["game_type"] == "REG"]
    if not regular:
        raise IngestError(f"schedule contains no {season} regular-season games")

    all_weeks: set[int] = set()
    played: dict[str, set[int]] = defaultdict(set)
    for game in regular:
        week = int(game["week"])
        all_weeks.add(week)
        played[game["home_team"]].add(week)
        played[game["away_team"]].add(week)

    byes: dict[str, int] = {}
    offenders: dict[str, list[int]] = {}
    for team, weeks in played.items():
        missing = sorted(all_weeks - weeks)
        if len(missing) == 1:
            byes[team] = missing[0]
        else:
            offenders[team] = missing
    if offenders:
        raise IngestError(f"not exactly one bye week for: {offenders}")
    return byes


def _require_gsis_id(row: dict[str, str], table: str) -> str:
    gsis_id = row["gsis_id"]
    if not gsis_id:
        raise IngestError(f"{table} row without a gsis_id: {row!r}")
    return gsis_id


def _roster_universe(roster: list[dict[str, str]]) -> dict[str, dict]:
    """One candidate row per player: the highest-week row, team-conferring on ties.

    The file holds one row per player per published week — a single week today,
    more as the season starts — so without the max-week rule the first in-season
    run would double the universe.
    """
    best: dict[str, dict] = {}
    for row in roster:
        if row["position"] not in FANTASY_POSITIONS:
            continue
        gsis_id = _require_gsis_id(row, "roster")
        candidate = {
            "full_name": row["full_name"],
            "team": None if row["status"] in UNROSTERED_STATUSES else row["team"],
            "position": row["position"],
            "bye_week": None,
            "nflverse_id": gsis_id,
            "sleeper_id": row["sleeper_id"] or None,
            "yahoo_num_id": int(row["yahoo_id"]) if row["yahoo_id"] else None,
            "week": int(row["week"]),
        }
        held = best.get(gsis_id)
        if (
            held is None
            or candidate["week"] > held["week"]
            or (candidate["week"] == held["week"] and held["team"] is None)
        ):
            best[gsis_id] = candidate
    return best


def ingest_players(connection: Connection, *, client: httpx2.Client, season: int) -> int:
    """Fetch the three artifacts and upsert the player universe. Returns rows upserted.

    The caller owns the transaction and the commit; a failure anywhere rolls the
    whole run back, so a partial universe never lands.
    """
    roster = fetch_csv(client, roster_url(season))
    players = fetch_csv(client, PLAYERS_URL)
    games = fetch_csv(client, SCHEDULE_URL)

    byes = bye_weeks(games, season)
    universe = _roster_universe(roster)
    for entry in universe.values():
        del entry["week"]
        if entry["team"] is not None:
            if entry["team"] not in byes:
                raise IngestError(f"no bye week derivable for team {entry['team']}")
            entry["bye_week"] = byes[entry["team"]]

    # The free-agent tail: current-season players no roster row mentions.
    for row in players:
        if row["position"] not in FANTASY_POSITIONS or row["last_season"] != str(season):
            continue
        gsis_id = _require_gsis_id(row, "players")
        if gsis_id in universe:
            continue
        universe[gsis_id] = {
            "full_name": row["display_name"],
            "team": None,
            "position": row["position"],
            "bye_week": None,
            "nflverse_id": gsis_id,
            "sleeper_id": None,
            "yahoo_num_id": None,
        }

    if not universe:
        raise IngestError("no fantasy-position players in the upstream data")
    connection.execute(UPSERT, list(universe.values()))
    return len(universe)
