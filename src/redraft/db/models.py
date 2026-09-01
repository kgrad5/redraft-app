"""SQLAlchemy models for the tables sketched in specs/draft-assistant.md §4.4.

Timestamps are TIMESTAMPTZ throughout and surrogate keys are BIGINT identities.
Snapshot foreign keys cascade — deleting a snapshot discards its rows — while
player foreign keys restrict, so no player disappears out from under a pick.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _snapshot_fk() -> ForeignKey:
    return ForeignKey("snapshots.snapshot_id", ondelete="CASCADE")


def _player_fk() -> ForeignKey:
    return ForeignKey("players.player_id", ondelete="RESTRICT")


class Snapshot(Base):
    """One raw fetch, stored whole so a parser change can be replayed
    (specs/draft-assistant.md §4.2)."""

    __tablename__ = "snapshots"
    __table_args__ = (
        CheckConstraint(
            "source IN ('sleeper', 'yahoo', 'ffc', 'nflverse')", name="ck_snapshots_source"
        ),
    )

    snapshot_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_payload: Mapped[dict] = mapped_column(JSONB)


class Player(Base):
    __tablename__ = "players"

    player_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    full_name: Mapped[str] = mapped_column(Text)
    team: Mapped[str | None] = mapped_column(Text)  # null while a free agent
    position: Mapped[str] = mapped_column(Text)
    bye_week: Mapped[int | None] = mapped_column(SmallInteger)
    nflverse_id: Mapped[str | None] = mapped_column(Text, unique=True)
    sleeper_id: Mapped[str | None] = mapped_column(Text, unique=True)
    # The bare numeric id (specs/draft-assistant.md §2.1), never the 470.p.{id} form.
    yahoo_num_id: Mapped[int | None] = mapped_column(Integer, unique=True)


class Projection(Base):
    """Component stats only — never a fantasy-point total (specs/draft-assistant.md §4.2)."""

    __tablename__ = "projections"

    snapshot_id: Mapped[int] = mapped_column(BigInteger, _snapshot_fk(), primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, _player_fk(), primary_key=True)
    stat_key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[float] = mapped_column(Double)


class Adp(Base):
    __tablename__ = "adp"

    snapshot_id: Mapped[int] = mapped_column(BigInteger, _snapshot_fk(), primary_key=True)
    player_id: Mapped[int] = mapped_column(BigInteger, _player_fk(), primary_key=True)
    source: Mapped[str] = mapped_column(Text)
    adp: Mapped[float] = mapped_column(Double)
    # Dispersion is nullable: FFC fits no data past ADP 166 (specs/draft-assistant.md §2.3).
    stdev: Mapped[float | None] = mapped_column(Double)
    high: Mapped[float | None] = mapped_column(Double)
    low: Mapped[float | None] = mapped_column(Double)
    times_drafted: Mapped[int | None] = mapped_column(Integer)


class LeagueConfig(Base):
    __tablename__ = "league_config"

    # autoincrement=False: season is a natural key (2026). SQLAlchemy makes a lone
    # integer primary key SERIAL, which would let an insert that omits the season
    # silently store 1 — and specs/draft-assistant.md §5 is explicit that a wrong
    # value here has no visible symptom.
    season: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    scoring_json: Mapped[dict] = mapped_column(JSONB)
    roster_slots_json: Mapped[dict] = mapped_column(JSONB)
    position_caps_json: Mapped[dict] = mapped_column(JSONB)
    pick_duration: Mapped[int] = mapped_column(Integer)


class DraftEvent(Base):
    """One pick or one undo. Read by folding the stream in event_id order, never by select.

    The key is surrogate because an undo (specs/draft-assistant.md §8.2 opcode `u`) is
    recorded as another row, so (draft_id, pick_no) is not unique. draft_id and team_id
    are opaque external identifiers, kept as text so no format assumption can lose
    information.

    Not append-only: ADR-33 dropped the triggers that once rejected UPDATE, DELETE and
    TRUNCATE, so a draft reset can delete its own rows. Nothing in the database now
    prevents a wider delete than intended.
    """

    __tablename__ = "draft_events"
    __table_args__ = (
        CheckConstraint("source IN ('tap', 'manual')", name="ck_draft_events_source"),
        CheckConstraint("event_type IN ('pick', 'undo')", name="ck_draft_events_event_type"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    draft_id: Mapped[str] = mapped_column(Text)
    pick_no: Mapped[int] = mapped_column(Integer)
    # Nullable on purpose: specs/draft-assistant.md §8.3 requires recovery never to
    # fail hard, and a NOT NULL foreign key would reject a live pick whose player
    # could not be resolved.
    player_id: Mapped[int | None] = mapped_column(BigInteger, _player_fk())
    team_id: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    # An undo is a further row naming the pick it reverses (ADR-32), so the board
    # cannot be read without distinguishing the two.
    event_type: Mapped[str] = mapped_column(Text)
    # clock_timestamp(), not now(): now() is transaction start, so a handler that
    # resolves a player before inserting — or batches a poll's picks — would stamp
    # every pick with the same earlier instant, defeating the latency analysis in
    # specs/draft-assistant.md §4.4.
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp()
    )
