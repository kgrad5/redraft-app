"""Schema tests: the migration applies and the tables match the models.

The throwaway-database fixtures live in conftest.py, shared with the draft tests.
"""

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from redraft.db.models import Base

# specs/draft-assistant.md §4.4. Named literally rather than derived from Base.metadata, so a table
# dropped from the models fails a test instead of quietly shrinking the expectation.
EXPECTED_TABLES = {
    "snapshots",
    "players",
    "projections",
    "adp",
    "league_config",
    "draft_events",
    "id_exceptions",
}


def test_models_cover_the_spec_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_migration_creates_every_table(migrated, engine):
    assert EXPECTED_TABLES <= set(sa.inspect(engine).get_table_names())


def test_columns_match_the_models(migrated, engine):
    inspector = sa.inspect(engine)
    for name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(name)}
        assert actual == set(table.columns.keys()), name


def test_migration_matches_the_models(migrated, engine):
    """Names alone would let types, nullability and constraints drift apart silently."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff


def test_draft_events_accepts_a_null_player_id(migrated, engine):
    """ADR-28. Without this, tidying the column to NOT NULL breaks nothing here and
    fails a live pick instead, which is the failure specs/draft-assistant.md §8.3 forbids."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO draft_events "
                "(draft_id, pick_no, player_id, team_id, source, event_type) "
                "VALUES ('unresolved', 99, NULL, 'team-1', 'tap', 'pick')"
            )
        )
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM draft_events "
                    "WHERE draft_id = 'unresolved' AND player_id IS NULL"
                )
            ).scalar_one()
            == 1
        )


def test_a_null_player_id_can_be_filled_in_place(migrated, engine):
    """ADR-33 lifted ADR-28's blocking consequence: UPDATE is no longer rejected, so a
    tapped pick whose player could not be resolved is reconcilable where it sits.

    Inserts its own unresolved pick rather than reading the one the test above leaves
    behind. A test that depends on a sibling's row passes in file order and fails under
    -k, -p xdist, or any reordering.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO draft_events "
                "(draft_id, pick_no, player_id, team_id, source, event_type) "
                "VALUES ('reconcile', 12, NULL, 'team-2', 'tap', 'pick')"
            )
        )
        player_id = conn.execute(
            sa.text(
                "INSERT INTO players (full_name, position) VALUES ('Late Resolution', 'WR') "
                "RETURNING player_id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "UPDATE draft_events SET player_id = :player_id "
                "WHERE draft_id = 'reconcile' AND player_id IS NULL"
            ),
            {"player_id": player_id},
        )

    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT player_id FROM draft_events WHERE draft_id = 'reconcile'")
            ).scalar_one()
            == player_id
        )


@pytest.mark.parametrize(
    ("statement", "constraint"),
    [
        (
            "INSERT INTO snapshots (source, fetched_at, raw_payload) VALUES ('espn', now(), '{}')",
            "ck_snapshots_source",
        ),
        (
            (
                "INSERT INTO draft_events (draft_id, pick_no, team_id, source, event_type) "
                "VALUES ('d', 1, 't', 'auto', 'pick')"
            ),
            "ck_draft_events_source",
        ),
        (
            (
                "INSERT INTO draft_events (draft_id, pick_no, team_id, source, event_type) "
                "VALUES ('d', 1, 't', 'manual', 'reset')"
            ),
            "ck_draft_events_event_type",
        ),
    ],
)
def test_closed_set_columns_reject_unknown_values(migrated, engine, statement, constraint):
    """ADR-31, the tap|manual set the issue names, and ADR-32's pick|undo."""
    with pytest.raises(sa.exc.IntegrityError, match=constraint), engine.begin() as conn:
        conn.execute(sa.text(statement))


def test_downgrade_refuses_while_undo_rows_exist(migrated, alembic_config, engine):
    """Dropping event_type would turn every undo row into an ordinary pick, silently
    reseating the pick it reverses, and the restored triggers would then put those rows
    beyond DELETE. The guard runs before any DDL, so a refusal changes nothing.

    Cleans up after itself: leaving the row behind would break test_downgrade_then_upgrade.
    """
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO draft_events "
                "(draft_id, pick_no, player_id, team_id, source, event_type) "
                "VALUES ('rollback-guard', 3, NULL, 'team-1', 'manual', 'undo')"
            )
        )
    try:
        with pytest.raises(RuntimeError, match="undo row"):
            command.downgrade(alembic_config, "-1")
        # The guard fired before any DDL, so the schema is untouched.
        assert "event_type" in {
            column["name"] for column in sa.inspect(engine).get_columns("draft_events")
        }
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM draft_events WHERE draft_id = 'rollback-guard'"))


def test_downgrade_then_upgrade(migrated, alembic_config, engine):
    command.downgrade(alembic_config, "base")
    assert not (EXPECTED_TABLES & set(sa.inspect(engine).get_table_names()))

    command.upgrade(alembic_config, "head")
    assert EXPECTED_TABLES <= set(sa.inspect(engine).get_table_names())
