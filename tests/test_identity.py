"""The shared identity resolver (issue #8): four tiers, and a report of what it missed.

The fixture pool is a miniature of the real one, and every row in it is a player the
live measurement of 2026-08-31 actually broke on. Mike Washington is the reason this
issue has a top-200 bar at all: `players` holds him as `Mike Washington Jr.` with a null
`sleeper_id`, Sleeper publishes him as `Mike Washington`, and he is the one record inside
the top 200 that the crosswalk alone leaves unplaced. Kenneth Walker, Oronde Gadsden,
Tre Harris and Kyle Pitts are four of the nine ADR-46 left behind. The two Marvin
Harrisons are not a real pair — they are the shape that makes the exact tier's precedence
observable, and without them a fold-first resolver passes every other test here.

No live network and no ingester: this module tests the resolver against a pool it builds
itself, so a failure names the matching rule rather than a provider.
"""

import ast
import json
import re
from pathlib import Path

import pytest
import sqlalchemy as sa

from redraft.identity.normalize import normalized
from redraft.identity.report import Unmatched, unmatched_report
from redraft.identity.resolve import (
    EXCEPTIONS_PATH,
    SATELLITE,
    DuplicateResolutionError,
    ExceptionFileError,
    Resolver,
    load_exceptions,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PLAYERS = Path(__file__).resolve().parent / "fixtures" / "players.json"

# Every name here is one the live sources spell differently from `players`, except the
# Harrison pair and the Peterson pair, which exist so the two ambiguity rules are
# observable. `sleeper_id` is null exactly where the real crosswalk leaves it null.
POOL = [
    # The top-200 miss of ADR-42. Sleeper says "Mike Washington"; only the fold places him.
    {
        "full_name": "Mike Washington Jr.",
        "team": "LV",
        "position": "RB",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    # Four of ADR-46's nine: the source carries the suffix, or omits one `players` has.
    {
        "full_name": "Kenneth Walker III",
        "team": "KC",
        "position": "RB",
        "sleeper_id": "8135",
        "yahoo_num_id": None,
    },
    {
        "full_name": "Oronde Gadsden II",
        "team": "LAC",
        "position": "TE",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    {
        "full_name": "Tre Harris",
        "team": "LAC",
        "position": "WR",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    {
        "full_name": "Kyle Pitts",
        "team": "ATL",
        "position": "TE",
        "sleeper_id": None,
        "yahoo_num_id": 33418,
    },
    # The punctuation case specs/draft-assistant.md §4.3 names by hand.
    {
        "full_name": "DK Metcalf",
        "team": "PIT",
        "position": "WR",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    # A clean crosswalk hit, so a tier-one regression is not hidden by the name tiers.
    {
        "full_name": "Jahmyr Gibbs",
        "team": "DET",
        "position": "RB",
        "sleeper_id": "8151",
        "yahoo_num_id": 40059,
    },
    # The exact-beats-fold pair. Both fold to "marvin harrison"; only the exact tier
    # can tell them apart, and today's helper in adp.py can.
    {
        "full_name": "Marvin Harrison",
        "team": "ARI",
        "position": "WR",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    {
        "full_name": "Marvin Harrison Jr.",
        "team": "ARI",
        "position": "WR",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    # Two players under one exact name: the ambiguity rule adp.py already has.
    {
        "full_name": "Adrian Peterson",
        "team": "MIN",
        "position": "RB",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
    {
        "full_name": "Adrian Peterson",
        "team": "CHI",
        "position": "RB",
        "sleeper_id": None,
        "yahoo_num_id": None,
    },
]


@pytest.fixture(scope="module")
def players(migrated, engine):
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO players (full_name, team, position, sleeper_id, yahoo_num_id) "
                "VALUES (:full_name, :team, :position, :sleeper_id, :yahoo_num_id)"
            ),
            POOL,
        )


@pytest.fixture
def connection(players, engine):
    """One transaction per test, always rolled back, so every test starts from the pool."""
    with engine.connect() as conn:
        transaction = conn.begin()
        try:
            yield conn
        finally:
            transaction.rollback()


@pytest.fixture
def empty_exceptions(tmp_path):
    """An exception file with no entries, for the tests that are not about the file."""
    path = tmp_path / "id_exceptions.yaml"
    path.write_text("[]\n")
    return path


def resolver(connection, empty_exceptions, source="sleeper", on_duplicate="report"):
    return Resolver(connection, source, exceptions_path=empty_exceptions, on_duplicate=on_duplicate)


def player_id_of(connection, full_name):
    return connection.execute(
        sa.text("SELECT player_id FROM players WHERE full_name = :n"), {"n": full_name}
    ).scalar_one()


# --- normalization -------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_spelling,canonical",
    [
        # The suffix is on the `players` side and the source omits it. This is the
        # top-200 case, and it is the direction a suffix-stripping fold must handle.
        pytest.param("Mike Washington", "Mike Washington Jr.", id="players-carries-the-suffix"),
        pytest.param("Kenneth Walker", "Kenneth Walker III", id="numeral-suffix"),
        pytest.param("Oronde Gadsden", "Oronde Gadsden II", id="numeral-suffix-ii"),
        # The suffix is on the source side and `players` omits it — ADR-46's FFC case.
        pytest.param("Kyle Pitts Sr.", "Kyle Pitts", id="source-carries-the-suffix"),
        # Punctuation only, both directions.
        pytest.param("D.K. Metcalf", "DK Metcalf", id="periods"),
        pytest.param("Tre' Harris", "Tre Harris", id="apostrophe"),
    ],
)
def test_a_punctuation_or_suffix_variant_resolves_to_one_player(
    connection, empty_exceptions, source_spelling, canonical
):
    """The issue's second criterion, one row per variant the live sources actually send."""
    index = resolver(connection, empty_exceptions)
    position = connection.execute(
        sa.text("SELECT position FROM players WHERE full_name = :n"), {"n": canonical}
    ).scalar_one()

    assert index.resolve(None, source_spelling, position) == player_id_of(connection, canonical)
    assert index.unmatched == ()


def test_an_exact_spelling_beats_the_fold(connection, empty_exceptions):
    """Why the exact tier runs before the normalized one.

    Both Marvin Harrisons fold to one key, so a resolver that folds first calls the pair
    ambiguous and loses *both* — where today's exact tier in src/redraft/ingest/adp.py
    places a byte-identical spelling correctly. An exact spelling is a positive
    identification and must not be beaten by a fold the source never asked for.
    """
    index = resolver(connection, empty_exceptions)

    assert index.resolve(None, "Marvin Harrison Jr.", "WR") == player_id_of(
        connection, "Marvin Harrison Jr."
    )
    assert index.resolve(None, "Marvin Harrison", "WR") == player_id_of(
        connection, "Marvin Harrison"
    )
    assert index.unmatched == ()


def test_a_name_two_players_share_resolves_to_neither_and_says_so(connection, empty_exceptions):
    """ADR-46's rule, carried forward: an ambiguous name is reported, never guessed at."""
    index = resolver(connection, empty_exceptions)

    assert index.resolve(None, "Adrian Peterson", "RB") is None
    assert [record.name for record in index.unmatched] == ["Adrian Peterson"]
    # Naming the collision is the point. "unresolved" alone sends whoever reads the
    # report looking for a player who is in fact present twice, which is a different
    # problem with a different fix.
    (record,) = index.unmatched
    assert record.detail == "ambiguous: Adrian Peterson [MIN] and Adrian Peterson [CHI]"


def test_a_fold_that_would_collide_resolves_to_neither(connection, empty_exceptions):
    """A spelling matching neither Harrison exactly folds onto both, so it places nobody
    rather than picking whichever row came back last."""
    index = resolver(connection, empty_exceptions)

    assert index.resolve(None, "Marvin Harrison Sr.", "WR") is None
    (record,) = index.unmatched
    assert record.detail == (
        "ambiguous once folded: Marvin Harrison [ARI] and Marvin Harrison Jr. [ARI]"
    )


def test_digits_survive_normalization(connection):
    """A canary over the shared fixture pool, which names players `Aaron Abbott QB01`.

    Folding digits away collapses 64 of its keys and 160 of its 244 rows, which would
    make every other pool-based test in the repo resolve the wrong player while still
    passing. Measured both ways on 2026-08-31.
    """
    rows = json.loads(FIXTURE_PLAYERS.read_text())
    keys = [(normalized(row["full_name"]), row["position"]) for row in rows]

    assert len(set(keys)) == len(keys), "the fixture pool no longer has unique normalized names"
    assert normalized("Aaron Abbott QB01") == "aaron abbott qb01"


def test_normalization_folds_only_what_it_claims_to():
    """Case, punctuation and one trailing suffix — and nothing else."""
    assert normalized("D.K. Metcalf") == "dk metcalf"
    assert normalized("Amon-Ra St. Brown") == "amonra st brown"
    assert normalized("Travis Etienne Jr.") == "travis etienne"
    # A suffix that is the whole name is not a suffix.
    assert normalized("Jr.") == "jr"
    # Only one trailing suffix comes off, and only from the end.
    assert normalized("Jr. Smith") == "jr smith"


# --- the tiers -----------------------------------------------------------------------


def test_the_crosswalk_id_beats_a_name_belonging_to_someone_else(connection, empty_exceptions):
    """Tier two outranks the name tiers, so a name collision can never override a
    positive identification. Gibbs's id, sent under Adrian Peterson's ambiguous name."""
    index = resolver(connection, empty_exceptions)

    assert index.resolve("8151", "Adrian Peterson", "RB") == player_id_of(
        connection, "Jahmyr Gibbs"
    )


def test_yahoos_numeric_id_resolves_for_yahoo_and_not_for_sleeper(connection, empty_exceptions):
    """Each source reads its own satellite column and no other (ADR-29). Pitts carries a
    `yahoo_num_id` and no `sleeper_id`, so the same key must place him for one and not
    the other."""
    pitts = player_id_of(connection, "Kyle Pitts")

    yahoo = resolver(connection, empty_exceptions, source="yahoo")
    assert yahoo.resolve("33418", "Not His Name", "TE") == pitts

    sleeper = resolver(connection, empty_exceptions, source="sleeper")
    assert sleeper.resolve("33418", "Not His Name", "TE") is None


def test_ffc_has_no_id_tier_at_all(connection, empty_exceptions):
    """FFC publishes an internal id that matches nothing, so it is never consulted."""
    index = resolver(connection, empty_exceptions, source="ffc")

    assert index.resolve("8151", "Adrian Peterson", "RB") is None
    assert index.resolve("8151", "Kyle Pitts", "TE") == player_id_of(connection, "Kyle Pitts")


def identifiers_and_literals(path: Path) -> set[str]:
    """Every name and string this module *uses*, with docstrings and comments excluded.

    Parsed rather than grepped. A grep cannot tell code from prose, so it flags the
    comment in `resolve.py` that says `yahoo_id` is deliberately not consulted — failing
    the build for documenting the rule it is enforcing.
    """
    tree = ast.parse(path.read_text())
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    used: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                used.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.value))
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.Name):
            used.add(node.id)
    return used


def test_yahoo_id_is_never_used_as_a_join_key():
    """The issue's third criterion, pinned against the code rather than left to review.

    specs/draft-assistant.md §2.3: Sleeper's `yahoo_id` is ~24% populated and null for
    essentially every player drafted since 2021. The column this repo joins on is
    `yahoo_num_id`, which nflverse supplies; the two differ by one underscore.

    Scans all of `src/redraft/` with exactly one exemption, named rather than implied:
    `redraft.ingest.players` reads `yahoo_id` from nflverse's roster CSV, which is *where
    `yahoo_num_id` comes from* and the one legitimate use in the repo. Scanning the whole
    tree rather than a hand-picked list matters most for `ingest/adp.py` and
    `providers/yahoo.py` — the two modules a future change is likeliest to reach for a
    Yahoo id from, and the two a list written today would forget to add.
    """
    exempt = {REPO_ROOT / "src" / "redraft" / "ingest" / "players.py"}
    guarded = [
        path for path in sorted((REPO_ROOT / "src" / "redraft").rglob("*.py")) if path not in exempt
    ]
    assert len(guarded) > 10, "the scan found almost nothing; the path is probably wrong"
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in guarded
        if "yahoo_id" in identifiers_and_literals(path)
    ]
    assert not offenders, f"Sleeper's yahoo_id must never be a join key: {offenders}"

    # And the satellite table names no such column, which is the lookup itself.
    assert "yahoo_id" not in SATELLITE.values()
    assert set(SATELLITE.values()) == {"sleeper_id", "yahoo_num_id"}


# --- the exception file --------------------------------------------------------------


def write_exceptions(path, body):
    path.write_text(body)
    return path


def test_an_exception_entry_overrides_an_automatic_match(connection, tmp_path):
    """The issue's fourth criterion, and the reason the file is consulted first.

    Gibbs's Sleeper id would resolve to Gibbs on tier two. The entry sends it to Kyle
    Pitts instead, which a tier running *after* the automatic ones could never do — it
    would only ever see keys the automatic tiers had already failed on.
    """
    path = write_exceptions(
        tmp_path / "e.yaml",
        "- source: sleeper\n"
        '  source_key: "8151"\n'
        '  full_name: "Kyle Pitts"\n'
        "  position: TE\n"
        '  note: "proving the override runs first"\n',
    )
    index = Resolver(connection, "sleeper", exceptions_path=path, on_duplicate="report")

    assert index.resolve("8151", "Jahmyr Gibbs", "RB") == player_id_of(connection, "Kyle Pitts")


def test_an_exception_for_another_source_does_not_apply(connection, tmp_path):
    path = write_exceptions(
        tmp_path / "e.yaml",
        '- source: ffc\n  source_key: "8151"\n  full_name: "Kyle Pitts"\n'
        '  position: TE\n  note: "ffc only"\n',
    )
    index = Resolver(connection, "sleeper", exceptions_path=path, on_duplicate="report")

    assert index.resolve("8151", "Jahmyr Gibbs", "RB") == player_id_of(connection, "Jahmyr Gibbs")


def test_an_exception_naming_no_player_raises_naming_the_entry(connection, tmp_path):
    """A target that matches nothing is a typo or a player who has since moved on, and
    both are silent no-ops if this passes."""
    path = write_exceptions(
        tmp_path / "e.yaml",
        '- source: sleeper\n  source_key: "8151"\n  full_name: "Nobody At All"\n'
        '  position: WR\n  note: "typo"\n',
    )

    with pytest.raises(ExceptionFileError, match="Nobody At All"):
        Resolver(connection, "sleeper", exceptions_path=path, on_duplicate="report")


def test_an_exception_naming_two_players_raises(connection, tmp_path):
    """The one cost of naming a target by name rather than by id (ADR-50)."""
    path = write_exceptions(
        tmp_path / "e.yaml",
        '- source: sleeper\n  source_key: "8151"\n  full_name: "Adrian Peterson"\n'
        '  position: RB\n  note: "ambiguous target"\n',
    )

    with pytest.raises(ExceptionFileError, match="Adrian Peterson"):
        Resolver(connection, "sleeper", exceptions_path=path, on_duplicate="report")


@pytest.mark.parametrize(
    "body,expected",
    [
        pytest.param(
            '- source: nowhere\n  source_key: "1"\n  full_name: "Kyle Pitts"\n'
            '  position: TE\n  note: "n"\n',
            "nowhere",
            id="unknown-source",
        ),
        pytest.param(
            '- source: sleeper\n  source_key: "1"\n  full_name: "Kyle Pitts"\n  position: TE\n',
            "note",
            id="missing-note",
        ),
        pytest.param(
            '- source: sleeper\n  source_key: "1"\n  full_name: "Kyle Pitts"\n'
            '  position: TE\n  note: "a"\n'
            '- source: sleeper\n  source_key: "1"\n  full_name: "DK Metcalf"\n'
            '  position: WR\n  note: "b"\n',
            "twice",
            id="repeated-key",
        ),
    ],
)
def test_a_malformed_exception_file_raises(connection, tmp_path, body, expected):
    path = write_exceptions(tmp_path / "e.yaml", body)

    with pytest.raises(ExceptionFileError, match=expected):
        Resolver(connection, "sleeper", exceptions_path=path, on_duplicate="report")


def test_a_missing_exception_file_raises(connection, tmp_path):
    """Not an empty mapping: the file is checked in, so its absence means a broken
    checkout or a path that stopped resolving, and returning nothing would quietly
    disable the one tier the operator maintains by hand."""
    with pytest.raises(ExceptionFileError, match="not found"):
        Resolver(
            connection, "sleeper", exceptions_path=tmp_path / "gone.yaml", on_duplicate="report"
        )


def test_the_checked_in_exception_file_parses(connection):
    """The shipped artifact, not a synthetic one. It holds no entries today — every
    unmatched record on 2026-08-31 named a player with no `players` row at all, and an
    entry cannot point at a row that does not exist — so this asserts it parses and that
    the path still resolves, which is what would break first."""
    assert EXCEPTIONS_PATH.exists(), EXCEPTIONS_PATH
    assert load_exceptions(EXCEPTIONS_PATH) == ()


# --- duplicates ----------------------------------------------------------------------


def test_two_records_claiming_one_player_raise_where_the_writer_cannot_absorb_it(
    connection, empty_exceptions
):
    """ADR-46's rule for `adp`, whose primary key would catch this as an IntegrityError
    naming neither record, on a transaction whose rollback takes the snapshot with it."""
    index = resolver(connection, empty_exceptions, source="yahoo", on_duplicate="raise")
    index.resolve("33418", "Kyle Pitts", "TE")

    with pytest.raises(DuplicateResolutionError, match="Kyle Pitts"):
        index.resolve(None, "Kyle Pitts Sr.", "TE")


def test_two_records_claiming_one_player_are_reported_where_a_raise_would_abort_the_run(
    connection, empty_exceptions
):
    """ADR-42 rejected run-aborting for Sleeper, whose pool is deliberately wider than a
    roster. `projections`' primary key is (snapshot_id, player_id, stat_key), so two
    records with disjoint stat keys would merge into one player in silence — the second
    is reported instead, which neither aborts nor merges.
    """
    index = resolver(connection, empty_exceptions, source="sleeper", on_duplicate="report")
    first = index.resolve("8135", "Kenneth Walker III", "RB")

    assert index.resolve(None, "Kenneth Walker", "RB") is None
    assert first == player_id_of(connection, "Kenneth Walker III")
    assert [record.name for record in index.unmatched] == ["Kenneth Walker"]
    (record,) = index.unmatched
    assert record.detail == "already placed as 'Kenneth Walker III'"


# --- the report ----------------------------------------------------------------------


def test_the_report_names_every_unmatched_record_worst_first(connection, empty_exceptions):
    index = resolver(connection, empty_exceptions)
    index.resolve(None, "Nobody At All", "WR", rank=12.3)
    index.resolve(None, "Also Nobody", "RB", rank=None)
    index.resolve(None, "Third Nobody", "TE", rank=4.5)

    report = unmatched_report(index.unmatched)

    assert "unmatched players: 3" in report
    for name in ("Nobody At All", "Also Nobody", "Third Nobody"):
        assert name in report
    # Worst first, and a record the source published no ADP for sorts last rather than
    # first, which is where a naive None sort would put it.
    assert (
        report.index("Third Nobody") < report.index("Nobody At All") < report.index("Also Nobody")
    )


def test_an_empty_report_says_so_rather_than_printing_nothing():
    """An empty report that prints nothing is indistinguishable from a job that never
    ran, which is the same reflex as `EmptyProjectionsError` one level down."""
    assert unmatched_report(()) == "unmatched players: none"


def test_the_report_needs_no_database_and_no_ingest_result():
    """It takes the records themselves, so `providers.base` can carry the type without
    importing the renderer back."""
    records = (Unmatched("yahoo", "29399", "Tyreek Hill", "WR", 129.2),)

    assert "Tyreek Hill" in unmatched_report(records)
    assert "129.2" in unmatched_report(records)
