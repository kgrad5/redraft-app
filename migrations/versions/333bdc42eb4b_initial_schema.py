"""Initial schema: the seven tables of SPEC §4.4, with draft_events made append-only.

Revision ID: 333bdc42eb4b
Revises:
Create Date: 2026-08-30 18:21:02.412321

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# A trigger travels with the migration and binds every connection. The alternative,
# REVOKE UPDATE/DELETE, would need a second database role this single-user tool
# does not have. DROP TABLE is unaffected, so downgrade still works.
APPEND_ONLY_UP = """
CREATE FUNCTION draft_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'draft_events is append-only: % rejected', TG_OP;
END;
$$;

CREATE TRIGGER draft_events_no_update_delete BEFORE UPDATE OR DELETE ON draft_events
    FOR EACH ROW EXECUTE FUNCTION draft_events_append_only();

CREATE TRIGGER draft_events_no_truncate BEFORE TRUNCATE ON draft_events
    FOR EACH STATEMENT EXECUTE FUNCTION draft_events_append_only();
"""

# The function is not removed with the table that uses it; drop it explicitly.
APPEND_ONLY_DOWN = """
DROP TRIGGER IF EXISTS draft_events_no_truncate ON draft_events;
DROP TRIGGER IF EXISTS draft_events_no_update_delete ON draft_events;
DROP FUNCTION IF EXISTS draft_events_append_only();
"""

revision: str = "333bdc42eb4b"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "league_config",
        sa.Column("season", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("scoring_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("roster_slots_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position_caps_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pick_duration", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("season"),
    )
    op.create_table(
        "players",
        sa.Column("player_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("position", sa.Text(), nullable=False),
        sa.Column("bye_week", sa.SmallInteger(), nullable=True),
        sa.Column("nflverse_id", sa.Text(), nullable=True),
        sa.Column("sleeper_id", sa.Text(), nullable=True),
        sa.Column("yahoo_num_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("player_id"),
        sa.UniqueConstraint("nflverse_id"),
        sa.UniqueConstraint("sleeper_id"),
        sa.UniqueConstraint("yahoo_num_id"),
    )
    op.create_table(
        "snapshots",
        sa.Column("snapshot_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "source IN ('sleeper', 'yahoo', 'ffc', 'nflverse')", name="ck_snapshots_source"
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_table(
        "adp",
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("adp", sa.Double(), nullable=False),
        sa.Column("stdev", sa.Double(), nullable=True),
        sa.Column("high", sa.Double(), nullable=True),
        sa.Column("low", sa.Double(), nullable=True),
        sa.Column("times_drafted", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.snapshot_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("snapshot_id", "player_id"),
    )
    op.create_table(
        "draft_events",
        sa.Column("event_id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("draft_id", sa.Text(), nullable=False),
        sa.Column("pick_no", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=True),
        sa.Column("team_id", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("source IN ('tap', 'manual')", name="ck_draft_events_source"),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "id_exceptions",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("source", "source_key"),
    )
    op.create_table(
        "projections",
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("stat_key", sa.Text(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["snapshots.snapshot_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("snapshot_id", "player_id", "stat_key"),
    )
    op.execute(APPEND_ONLY_UP)


def downgrade() -> None:
    op.execute(APPEND_ONLY_DOWN)
    op.drop_table("projections")
    op.drop_table("id_exceptions")
    op.drop_table("draft_events")
    op.drop_table("adp")
    op.drop_table("snapshots")
    op.drop_table("players")
    op.drop_table("league_config")
