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
