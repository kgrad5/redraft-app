# ADR-24 — Alembic for schema migrations, on psycopg v3

**Status:** Accepted · 2026-08-30 · issue #2

**Context**
§4.4 sketches seven tables but names no mechanism for creating them. The schema has to be
reproducible from an empty database if the local Postgres is ever rebuilt, and rollable back, so
that a bad migration is not a manual repair under a draft clock.

**Decision**
Alembic, with the SQLAlchemy models as the autogenerate source. The URL names psycopg v3
explicitly (`postgresql+psycopg://`), because SQLAlchemy 2.x resolves a bare `postgresql://` to
psycopg2.

**Consequences**
- The schema is reproducible and the rollback path is testable — `downgrade base` then `upgrade
  head` is part of issue #2's own verification, not a claim.
- Three more dependencies and a `migrations/` tree to maintain. `settings.database_url` and
  `settings.sqlalchemy_database_url` now differ by a scheme prefix, which is a trap for anyone
  composing a URL by hand.

**Alternatives rejected**
- Raw `.sql` files applied by hand — no down path, no version table, no way to prove an empty
  database reaches the same state.
- `Base.metadata.create_all()` — no migration history at all; every later schema change becomes
  a manual drop and rebuild.
