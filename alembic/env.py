"""Alembic environment: tells Alembic how to connect to the DB and which models exist."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

# Importing the models package makes sure every model is registered on
# Base.metadata. Today it's empty; later we'll import each model in
# app/models/__init__.py so Alembic can see them.
import app.models  # noqa: F401
from alembic import context
from app.core.config import get_settings
from app.models.base import Base

config = context.config

# Use the same DATABASE_URL as the app (from .env) instead of alembic.ini.
# "%" must be doubled because Alembic's config treats it as a special character.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Alembic compares this metadata with the real DB to auto-generate migrations.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without connecting to the DB (rarely used)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the DB and apply migrations (the normal mode)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,  # also detect column type changes
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
