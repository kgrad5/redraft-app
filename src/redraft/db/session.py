"""Database engine and session factory."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from redraft.settings import settings

# Lazy: no connection is opened until a session is used.
engine = create_engine(settings.sqlalchemy_database_url)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
