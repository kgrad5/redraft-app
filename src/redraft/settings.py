"""Application settings, loaded from the environment or a local .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
