"""Alembic environment; the database URL comes from the application settings."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _url() -> str:
    """Return the SQLAlchemy URL using the psycopg (v3) driver."""
    dsn = get_settings().db_dsn
    for prefix in ("postgresql://", "postgres://"):
        if dsn.startswith(prefix):
            return "postgresql+psycopg://" + dsn[len(prefix):]
    return dsn


def run_migrations_offline() -> None:
    """Emit SQL without connecting to the database."""
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations against the live database."""
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
