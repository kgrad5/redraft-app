"""Drop id_exceptions; the exception file is checked into the repo instead.

Revision ID: c1a7f3e90b52
Revises: bf6aa58833eb
Create Date: 2026-08-31 22:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a7f3e90b52"
down_revision: str | None = "bf6aa58833eb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ADR-50 makes data/id_exceptions.yaml the source of truth, so this table is a second
    # answer to a question that may only have one. Counted before the DROP rather than
    # after: the table is empty on every database today, and if it is not, someone wrote
    # rows a checked-in file cannot know about and dropping them silently is exactly the
    # loss ADR-24 chose Alembic to avoid. Same shape as the ADR-33 downgrade guard.
    rows = op.get_bind().execute(sa.text("SELECT count(*) FROM id_exceptions")).scalar_one()
    if rows:
        raise RuntimeError(
            f"id_exceptions holds {rows} row(s), which nothing in this repo wrote and "
            "nothing reads. Move them into data/id_exceptions.yaml — keyed on "
            "(source, source_key) and naming the target by full_name and position, not "
            "by player_id — then re-run this migration."
        )
    op.drop_table("id_exceptions")


def downgrade() -> None:
    # Recreated exactly as migration 333bdc42eb4b made it, down to the RESTRICT: a
    # partial down path would leave a schema version claiming a table that differs from
    # the one it names. It comes back empty, which is the state it was dropped in.
    op.create_table(
        "id_exceptions",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("player_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["player_id"], ["players.player_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("source", "source_key"),
    )
