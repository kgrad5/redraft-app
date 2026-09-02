"""The four tiers, in the order that makes each one mean what it says.

specs/draft-assistant.md §4.3 numbers three tiers: the nflverse crosswalk, name matching,
and a hand-maintained exception file. That numbering describes what the tiers hold, not
the order they run in — the file is consulted **first**, because a tier running after the
automatic ones only ever sees the keys they already failed on, and could therefore never
override a match. Overriding one is exactly what issue #8's verification requires of it.

So the order is: the exception file, the source's own crosswalk column, an exact
`(full_name, position)`, then a normalized one. Measured 2026-08-31 against the live pool
of 1,021 players, the current Sleeper snapshot, and live Yahoo and FFC fetches:

| source  | considered            | crosswalk | + exact | + normalized |
|---------|-----------------------|-----------|---------|--------------|
| Sleeper | 555 component-bearing | 37        | 28      | 27           |
| Yahoo   | 184 with a numeric ADP| 41        | 2       | 1            |
| FFC     | 220 at QB/RB/WR/TE    | n/a       | 7       | 0            |

The exact tier runs before the fold and that ordering is load-bearing: `players` can hold
a suffix pair, and folding first calls both members ambiguous and loses *both*, where the
exact tier places a byte-identical spelling correctly. An exact spelling is a positive
identification and is never beaten by a fold the source did not ask for.

Team is not in any key. FFC writes `LAR` where nflverse writes `LA` and `FA` for players
nflverse still rosters, which alone drops Puka Nacua at ADP 2.9 — ADR-46's measurement,
carried forward, and the reason specs/draft-assistant.md §4.3's "name + team + position"
wording is wrong (ADR-49).
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml
from sqlalchemy import Connection, text

from redraft.http.client import SOURCES, Source
from redraft.identity.normalize import normalized
from redraft.identity.report import Unmatched

# Anchored to the repo root rather than the process CWD, the same reason and the same
# shape as `redraft.settings.ENV_FILE`: this module is three packages deep, so one more
# parent. A wrong CWD must fail rather than silently disable the operator's own tier.
EXCEPTIONS_PATH = Path(__file__).resolve().parents[3] / "data" / "id_exceptions.yaml"

# The satellite column each source joins on (ADR-29). FFC is absent because it publishes
# no crosswalk id at all — its own `player_id` matches nothing outside FFC — and nflverse
# is absent because it *is* the crosswalk: `redraft.ingest.players` writes these columns.
# Sleeper's `yahoo_id` is deliberately not here (specs/draft-assistant.md §2.3): it is
# ~24% populated and null for essentially every player drafted since 2021.
SATELLITE: dict[Source, str] = {"sleeper": "sleeper_id", "yahoo": "yahoo_num_id"}

# Ordered so the report reads the same twice. `_index` records a collision in row
# arrival order, and an unordered SELECT lets Postgres return the same two rows either
# way round — so the ambiguity line an operator reads would change between runs on
# unchanged data, and two runs' reports could not be diffed. Resolution is unaffected;
# only what the text says first.
SELECT_PLAYERS = text(
    "SELECT player_id, full_name, team, position, sleeper_id, yahoo_num_id FROM players "
    "ORDER BY player_id"
)

ENTRY_FIELDS = ("source", "source_key", "full_name", "position", "note")

# The tiers in priority order. A record's index here is the strength of its claim, which
# is what settles two records claiming one player — the alternative is payload order,
# and payload order is not a fact about who the player is (ADR-51).
TIERS = ("exception", "crosswalk", "exact", "normalized")
EXCEPTION, CROSSWALK, EXACT, NORMALIZED = range(len(TIERS))


class ExceptionFileError(Exception):
    """`data/id_exceptions.yaml` is malformed, or an entry names a player nothing matches.

    Loud, and at construction rather than at the first record that needs the entry: this
    is the one tier a human maintains by hand, and a typo in it is otherwise a silent
    no-op that leaves the automatic match it was written to correct in place.
    """


class DuplicateResolutionError(Exception):
    """Two records from one source resolved to the same player.

    Moved here from `redraft.ingest.adp`, where ADR-46 introduced it, because resolution
    moved here. The reasoning is unchanged: `adp`'s primary key would catch this, but as
    an `IntegrityError` naming neither record, on a transaction whose rollback takes the
    snapshot with it (ADR-38), so the payload that would say which two collided is gone
    at exactly the moment it is wanted.

    It is reachable because resolution has four tiers: one record can match on a
    crosswalk id while a second, sharing its name and position, falls through to a name
    tier and lands on the same player.
    """


@dataclass(slots=True)
class _Claim:
    """Every record that reached one player this run, and the best tier any of them did.

    Mutable, and read at the end rather than written as it goes: what to report about a
    losing record depends on who ends up with the player, and that is not known until the
    last record has arrived. Appending eagerly says "already placed as X" about a record
    that a later, better one then takes the player from, and says "withdrawn" about a
    player a later crosswalk id goes on to settle.
    """

    tier: int
    # Records that reached the player on `tier`. Exactly one holds him; more than one is
    # a tie no tier can settle, so nobody holds him (ADR-52).
    holders: list[tuple[int, Unmatched]]
    # Records that lost him, each flagged with whether it was holding him at the time.
    # Being displaced and arriving too late read differently in the report.
    lost: list[tuple[int, Unmatched, bool]]


@dataclass(frozen=True, slots=True)
class ExceptionEntry:
    """One hand-written mapping from a source's key to a player, by name (ADR-50).

    The target is `(full_name, position)` rather than a `player_id` because `player_id`
    is an `Identity()` surrogate reissued on any rebuild, and a git-tracked file naming
    one would point at a different player after a rebuild — silently, which is the exact
    failure specs/draft-assistant.md §4.3 exists to prevent.
    """

    source: Source
    source_key: str
    full_name: str
    position: str
    note: str


def load_exceptions(path: Path) -> tuple[ExceptionEntry, ...]:
    """Parse and validate the file, without touching the database.

    Validation that needs the player pool — that a target names exactly one player — is
    the `Resolver`'s, because only it has the pool.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError as absent:
        # Not an empty mapping. The file is checked in, so its absence means a broken
        # checkout or a path that stopped resolving, and returning nothing would quietly
        # disable the one tier an operator maintains by hand.
        raise ExceptionFileError(f"{path} not found; the exception file is checked in") from absent
    except yaml.YAMLError as malformed:
        raise ExceptionFileError(f"{path} is not valid YAML: {malformed}") from malformed

    # An empty file parses to None, which is a legitimate "no exceptions" and the state
    # the file ships in.
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ExceptionFileError(f"{path} must hold a list of entries, got {type(raw).__name__}")

    entries: list[ExceptionEntry] = []
    seen: set[tuple[str, str]] = set()
    for position_in_file, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ExceptionFileError(f"entry {position_in_file} is not a mapping")
        # `is None` first, because `str(None)` is the truthy string "None" and would sail
        # through the blank check below. A null `source_key` then becomes the key "None",
        # which matches no record from any source, and `_targets` validates only the
        # target — so the entry is inert forever and nothing says so. That silent no-op is
        # the failure this exception exists to prevent, and YAML makes it easy to write:
        # a key left with nothing after the colon is null, not "".
        missing = [
            field
            for field in ENTRY_FIELDS
            if item.get(field) is None or not str(item[field]).strip()
        ]
        if missing:
            raise ExceptionFileError(
                f"entry {position_in_file} is missing {', '.join(missing)}; "
                "every entry needs a note saying why automatic matching gets it wrong"
            )
        source = str(item["source"])
        if source not in SOURCES:
            raise ExceptionFileError(
                f"entry {position_in_file} names source {source!r}, which is not one of "
                f"{', '.join(SOURCES)}"
            )
        key = (source, str(item["source_key"]))
        if key in seen:
            raise ExceptionFileError(
                f"{key[1]!r} appears twice for source {source!r}; one key, one player"
            )
        seen.add(key)
        entries.append(
            ExceptionEntry(
                source=source,  # type: ignore[arg-type]
                source_key=str(item["source_key"]),
                full_name=str(item["full_name"]),
                position=str(item["position"]),
                note=str(item["note"]),
            )
        )
    return tuple(entries)


def _index(rows: Iterable[Any], key_of) -> tuple[dict, dict]:
    """Build a lookup and, beside it, the keys that name more than one player.

    A key naming two players is removed rather than resolved arbitrarily — "whichever row
    came back last" is the silent wrongness specs/draft-assistant.md §4.3 exists to
    prevent — and kept in the second dict so the report can say what it fell between.
    """
    lookup: dict = {}
    collisions: dict = {}
    for row in rows:
        key = key_of(row)
        label = f"{row.full_name} [{row.team or '-'}]"
        if key in collisions:
            collisions[key].append(label)
            continue
        if key in lookup:
            collisions[key] = [lookup.pop(key)[1], label]
            continue
        lookup[key] = (row.player_id, label)
    return lookup, collisions


class Resolver:
    """Every source's path from a record to a `players.player_id`, for one run.

    Built once per ingest and stateful on purpose: it accumulates what it could not place
    so the ingester can hand it to `IngestResult` without keeping a parallel list, and it
    remembers which players this run has already claimed.

    `on_duplicate` has no default because the right answer differs by source and both
    answers are recorded. `adp` wants `"raise"` (ADR-46): its primary key would catch a
    collision anyway, unhelpfully. `projections` wants `"report"`: ADR-42 rejected
    run-aborting on Sleeper, whose pool is deliberately wider than a roster, and its
    primary key is `(snapshot_id, player_id, stat_key)`, so two records with disjoint stat
    keys would otherwise merge into one player in silence. Reporting the second neither
    aborts the run nor merges anybody.
    """

    def __init__(
        self,
        connection: Connection,
        source: Source,
        *,
        on_duplicate: Literal["raise", "report"],
        exceptions_path: Path = EXCEPTIONS_PATH,
    ) -> None:
        self.source = source
        self.on_duplicate = on_duplicate
        rows = connection.execute(SELECT_PLAYERS).all()

        satellite = SATELLITE.get(source)
        # Coerced to text on both sides. `sleeper_id` is Text and `yahoo_num_id` is
        # Integer, and the source key arrives as text from both payloads, so comparing
        # them raw places every Sleeper record and no Yahoo one — a tier that fails
        # silently and completely.
        self._by_source_id = (
            {
                str(getattr(row, satellite)): row.player_id
                for row in rows
                if getattr(row, satellite) is not None
            }
            if satellite
            else {}
        )
        self._by_exact, self._exact_collisions = _index(
            rows, lambda row: (row.full_name, row.position)
        )
        self._by_normalized, self._normalized_collisions = _index(
            rows, lambda row: (normalized(row.full_name), row.position)
        )
        self._exceptions = self._targets(load_exceptions(exceptions_path))

        self._claimed: dict[int, _Claim] = {}
        # Records that matched no player at all. Final the moment they are made — nothing
        # arriving later changes what matched nothing — so unlike a contested record they
        # live here rather than inside a claim. The int is arrival order, which the report
        # is rebuilt in.
        self._unmatched: list[tuple[int, Unmatched]] = []
        self._seen = 0

    def _targets(self, entries: tuple[ExceptionEntry, ...]) -> dict[str, int]:
        """This source's entries, with each target turned into a `player_id`.

        Resolved here rather than lazily so a bad entry stops the run before the fetch is
        parsed, and against the exact spelling only: an entry is hand-written against a
        row someone is looking at, so requiring it to match that row byte for byte costs
        nothing and keeps the file's meaning independent of how the fold happens to work.
        """
        targets: dict[str, int] = {}
        for entry in entries:
            if entry.source != self.source:
                continue
            key = (entry.full_name, entry.position)
            if key in self._exact_collisions:
                raise ExceptionFileError(
                    f"{entry.full_name!r} ({entry.position}) names more than one player — "
                    f"{' and '.join(self._exact_collisions[key])}"
                )
            if key not in self._by_exact:
                raise ExceptionFileError(
                    f"{entry.full_name!r} ({entry.position}) names no player; "
                    f"the entry for {entry.source_key!r} matches nothing"
                )
            targets[entry.source_key] = self._by_exact[key][0]
        return targets

    def _match(
        self, source_key: str | None, name: str, position: str
    ) -> tuple[int | None, int | None, str | None]:
        """The tiers, in order: the player, the tier that placed them, and why not."""
        if source_key is not None and source_key in self._exceptions:
            return self._exceptions[source_key], EXCEPTION, None
        if source_key is not None and source_key in self._by_source_id:
            return self._by_source_id[source_key], CROSSWALK, None

        exact = (name, position)
        if exact in self._by_exact:
            return self._by_exact[exact][0], EXACT, None
        if exact in self._exact_collisions:
            return None, None, f"ambiguous: {' and '.join(self._exact_collisions[exact])}"

        folded = (normalized(name), position)
        if folded in self._by_normalized:
            return self._by_normalized[folded][0], NORMALIZED, None
        if folded in self._normalized_collisions:
            return (
                None,
                None,
                f"ambiguous once folded: {' and '.join(self._normalized_collisions[folded])}",
            )
        return None, None, None

    def resolve(
        self, source_key: str | None, name: str, position: str, *, rank: float | None = None
    ) -> int | None:
        """This record's internal `player_id`, or None if nothing places it.

        A None return is always accompanied by a record on `unmatched`, so an ingester
        cannot drop a player without the report saying so. The converse does not hold:
        a `player_id` this returns can still be taken back by a later record, so
        resolution is two-phase and a caller must sweep `withdrawn` after the loop and
        before it writes (ADR-52). Nothing in the return type says so, which is the cost
        of settling contention in one pass.

        When two records claim one player, **the tiers settle it, never arrival order**
        (ADR-52). Resolving in tier order within a record is not enough on its own: a flat
        first-come claim hands the player to whichever record the source listed first, so
        an operator's exception entry loses to an automatic match and a crosswalk id loses
        to a name fold. A strictly better tier takes the player and the record it displaces
        is reported. Two records reaching one player on the *same* tier are settled by
        neither — nothing but arrival order separates them, and that is not a fact about
        who the player is — so the player is withdrawn and both records are reported. It is
        the rule `_index` already applies on the pool side, applied to records too.
        """
        player_id, tier, why = self._match(source_key, name, position)
        record = Unmatched(self.source, source_key, name, position, rank, why)
        self._seen += 1
        seq = self._seen
        if player_id is None:
            self._unmatched.append((seq, record))
            return None

        claim = self._claimed.get(player_id)
        if claim is None:
            self._claimed[player_id] = _Claim(tier, [(seq, record)], [])
            return player_id

        if tier < claim.tier:
            # A positive identification outranks whatever came before it, and settles a
            # player an earlier tie had withdrawn.
            claim.lost.extend((held_seq, held, True) for held_seq, held in claim.holders)
            claim.tier = tier
            claim.holders = [(seq, record)]
            return player_id

        if self.on_duplicate == "raise":
            raise DuplicateResolutionError(
                f"{name!r} and {claim.holders[0][1].name!r} both resolved to player_id "
                f"{player_id} on the {TIERS[claim.tier]} tier; one of them is matching "
                "on a name it does not own"
            )

        if tier > claim.tier:
            claim.lost.append((seq, record, False))
            return None

        # Same tier. Reporting only the loser would leave the winner's rows written on a
        # coin toss, and the winner is exactly as likely to be the wrong player.
        claim.holders.append((seq, record))
        return None

    @property
    def withdrawn(self) -> frozenset[int]:
        """Players no record may keep, because more than one reached them on one tier.

        An ingester must drop whatever it wrote for these before it writes: `resolve`
        returned the player to the first of them, which was correct until the second
        arrived, and nothing can be un-returned after the fact.
        """
        return frozenset(
            player_id for player_id, claim in self._claimed.items() if len(claim.holders) > 1
        )

    @property
    def unmatched(self) -> tuple[Unmatched, ...]:
        """What this run could not place, in the order it met them.

        Built here rather than accumulated, because a contested record's detail is only
        true once the contest is over.
        """
        records = list(self._unmatched)
        for claim in self._claimed.values():
            # One holder holds the player. More than one is a tie, and then nobody does,
            # so every record that touched him — holders and losers alike — is reported.
            winner = claim.holders[0][1] if len(claim.holders) == 1 else None
            if winner is None:
                tie = (
                    "withdrawn: more than one record matched this player on the "
                    f"{TIERS[claim.tier]} tier"
                )
                records.extend((seq, replace(held, detail=tie)) for seq, held in claim.holders)
            for seq, record, was_holding in claim.lost:
                if winner is None:
                    detail = (
                        f"withdrawn: outranked on the {TIERS[claim.tier]} tier, where more "
                        "than one record matched"
                    )
                elif was_holding:
                    detail = (
                        f"displaced by {winner.name!r}, which matched on the "
                        f"{TIERS[claim.tier]} tier"
                    )
                else:
                    detail = f"already placed as {winner.name!r}"
                records.append((seq, replace(record, detail=detail)))
        records.sort(key=lambda pair: pair[0])
        return tuple(record for _, record in records)
