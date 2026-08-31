"""Shared fixtures: a migrated throwaway database per test module, and the fixture player pool.

These run against a throwaway database, never the configured one. They call
`downgrade base`, which would wipe whatever they were pointed at.
"""

import json
import os
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from redraft.settings import settings

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PLAYERS = Path(__file__).resolve().parent / "fixtures" / "players.json"


@pytest.fixture(scope="module")
def database_url(request):
    """Create a throwaway database and point Alembic at it via REDRAFT_DATABASE_URL."""
    # The module name is part of the database name because every module gets its own
    # instance of this fixture. One shared name would let a later module's DROP land
    # on a database an earlier module is still using if the two ever overlap.
    module = request.module.__name__.rsplit(".", 1)[-1]
    name = f"redraft_test_{os.getpid()}_{module}"
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
    # Restore rather than delete: a developer may have this exported at a scratch
    # database, and deleting it would drop later in-process migrations through to
    # the configured one — the database this module must never touch.
    previous = os.environ.get("REDRAFT_DATABASE_URL")
    os.environ["REDRAFT_DATABASE_URL"] = url
    try:
        yield url
    finally:
        if previous is None:
            del os.environ["REDRAFT_DATABASE_URL"]
        else:
            os.environ["REDRAFT_DATABASE_URL"] = previous
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


@pytest.fixture(scope="module")
def player_pool(migrated, engine) -> list[dict]:
    """Load tests/fixtures/players.json and return the rows with their assigned ids.

    ADR-29: the file carries no player_id. Ids are whatever the identity column hands
    out, which is the point — a fixture that pinned them would encode exactly the
    assumption the ADR rejects.
    """
    rows = json.loads(FIXTURE_PLAYERS.read_text())
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO players (full_name, team, position, bye_week) "
                "VALUES (:full_name, :team, :position, :bye_week)"
            ),
            rows,
        )
        assigned = conn.execute(
            sa.text("SELECT player_id, full_name, position FROM players ORDER BY player_id")
        ).all()
    return [
        {"player_id": player_id, "full_name": full_name, "position": position}
        for player_id, full_name, position in assigned
    ]
