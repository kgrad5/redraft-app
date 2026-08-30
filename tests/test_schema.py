"""Schema tests: the migration applies, the tables match the models, draft_events is append-only.

These run against a throwaway database, never the configured one. They call
`downgrade base`, which would wipe whatever they were pointed at.
"""

import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from redraft.db.models import Base
from redraft.settings import settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# SPEC §4.4. Named literally rather than derived from Base.metadata, so a table
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


@pytest.fixture(scope="module")
def database_url():
    """Create a throwaway database and point Alembic at it via REDRAFT_DATABASE_URL."""
    name = f"redraft_test_{os.getpid()}"
    admin_url = sa.make_url(settings.sqlalchemy_database_url).set(database="postgres")
    admin = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(sa.text(f'CREATE DATABASE "{name}"'))

    # render_as_string, not str(): str() masks the password as ***.
    url = (
        sa.make_url(settings.sqlalchemy_database_url)
        .set(database=name)
        .render_as_string(hide_password=False)
    )
    os.environ["REDRAFT_DATABASE_URL"] = url
    try:
        yield url
    finally:
        del os.environ["REDRAFT_DATABASE_URL"]
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture(scope="module")
def engine(database_url):
    eng = sa.create_engine(database_url)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def alembic_config(database_url) -> Config:
    return Config(str(REPO_ROOT / "alembic.ini"))


@pytest.fixture(scope="module")
def migrated(alembic_config):
    command.upgrade(alembic_config, "head")
    return alembic_config


def test_models_cover_the_spec_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_migration_creates_every_table(migrated, engine):
    assert EXPECTED_TABLES <= set(sa.inspect(engine).get_table_names())


def test_columns_match_the_models(migrated, engine):
    inspector = sa.inspect(engine)
    for name, table in Base.metadata.tables.items():
        actual = {column["name"] for column in inspector.get_columns(name)}
        assert actual == set(table.columns.keys()), name


def test_draft_events_rejects_update_delete_and_truncate(migrated, engine):
    with engine.begin() as conn:
        player_id = conn.execute(
            sa.text(
                "INSERT INTO players (full_name, position) VALUES ('Test Player', 'RB') "
                "RETURNING player_id"
            )
        ).scalar_one()
        conn.execute(
            sa.text(
                "INSERT INTO draft_events (draft_id, pick_no, player_id, team_id, source) "
                "VALUES ('draft-1', 1, :player_id, 'team-1', 'tap')"
            ),
            {"player_id": player_id},
        )

    for statement in (
        "UPDATE draft_events SET pick_no = 99",
        "DELETE FROM draft_events",
        "TRUNCATE draft_events",
    ):
        with pytest.raises(sa.exc.DBAPIError, match="append-only"), engine.begin() as conn:
            conn.execute(sa.text(statement))

    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM draft_events")).scalar_one() == 1


def test_downgrade_then_upgrade(migrated, alembic_config, engine):
    command.downgrade(alembic_config, "base")
    assert not (EXPECTED_TABLES & set(sa.inspect(engine).get_table_names()))

    command.upgrade(alembic_config, "head")
    assert EXPECTED_TABLES <= set(sa.inspect(engine).get_table_names())
