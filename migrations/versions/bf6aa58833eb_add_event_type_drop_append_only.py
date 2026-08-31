"""Add draft_events.event_type and drop the append-only triggers.

Revision ID: bf6aa58833eb
Revises: 333bdc42eb4b
Create Date: 2026-08-30 21:14:07.883104

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ADR-33 drops the append-only guarantee so a draft reset can delete its own rows. The
# downgrade recreates the function and both triggers exactly as the initial migration
# made them: a partial down path would leave a permissive table under a schema version
# that claims otherwise. The CREATE carries no OR REPLACE, matching the original.
CREATE_APPEND_ONLY = """
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

DROP_APPEND_ONLY = """
DROP TRIGGER IF EXISTS draft_events_no_truncate ON draft_events;
DROP TRIGGER IF EXISTS draft_events_no_update_delete ON draft_events;
DROP FUNCTION IF EXISTS draft_events_append_only();
"""

revision: str = "bf6aa58833eb"
down_revision: str | None = "333bdc42eb4b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The server default backfills existing rows, then goes away immediately: keeping it
    # would let an insert that forgets event_type record an undo as a pick, and
    # specs/draft-assistant.md §4.4 gives no other way to tell the two apart.
    op.add_column(
        "draft_events",
        sa.Column("event_type", sa.Text(), nullable=False, server_default="pick"),
    )
    op.alter_column("draft_events", "event_type", server_default=None)
    op.create_check_constraint(
        "ck_draft_events_event_type", "draft_events", "event_type IN ('pick', 'undo')"
    )
    op.execute(DROP_APPEND_ONLY)


def downgrade() -> None:
    # Checked before the triggers go back on, and before the column goes away. Dropping
    # event_type turns every undo row into an ordinary pick, silently reseating the picks
    # they reverse — and the restored triggers then put those rows beyond DELETE, so the
    # damage is permanent. ADR-24 chose Alembic so a bad migration is not a manual repair
    # under a draft clock; refusing here is what makes that true in this direction too.
    undos = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM draft_events WHERE event_type = 'undo'"))
        .scalar_one()
    )
    if undos:
        raise RuntimeError(
            f"draft_events holds {undos} undo row(s). Downgrading would drop event_type, "
            "turning each one into a pick and permanently reseating the pick it reverses. "
            "Resolve or delete those rows first."
        )

    op.execute(CREATE_APPEND_ONLY)
    op.drop_constraint("ck_draft_events_event_type", "draft_events", type_="check")
    op.drop_column("draft_events", "event_type")
