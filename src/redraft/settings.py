"""Application settings, loaded from the environment or a local .env file."""

from pathlib import Path
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to the repo root, not the process CWD: every field has a default, so
# a wrong CWD would otherwise yield silently-wrong config instead of an error.
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    postgres_user: str = "redraft"
    postgres_password: str = "redraft"
    postgres_db: str = "redraft"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432

    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # Issue #9 is the first caller that needs either: the daily job fetches one season,
    # and Yahoo keys its pool by game key — 470 is 2026 (specs/draft-assistant.md §2.1).
    # Defaulted rather than required because `Settings()` is constructed at import time
    # by db/session.py, migrations/env.py and tests/conftest.py, and the Makefile's .env
    # rule deliberately never re-copies .env.example over an existing file. A required
    # field would therefore break `make test`, `make dev` and every Alembic command for
    # anyone whose .env predates this change, to catch two values that are correct today.
    # `league_config.season` stays issue #10's; this is the ingest season, not the
    # league's configured one, and when #10 lands one of the two has to win.
    season: int = 2026
    yahoo_game_key: int = 470

    @property
    def database_url(self) -> str:
        """Composed from the discrete parts so .env carries no second copy to drift."""
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def sqlalchemy_database_url(self) -> str:
        """SQLAlchemy resolves a bare postgresql:// to psycopg2; name the v3 driver explicitly."""
        return self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)


settings = Settings()
