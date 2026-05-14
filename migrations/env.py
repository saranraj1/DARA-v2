"""DARA Alembic migration environment — synchronous (Alembic doesn't support async)."""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.settings import get_settings  # noqa: E402

settings = get_settings()
config = context.config

# Use SYNC psycopg2 URL for Alembic (asyncpg doesn't work in sync Alembic context)
config.set_main_option("sqlalchemy.url", settings.sync_postgres_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# target_metadata = None → we use explicit migration files, not autogenerate.
# This avoids importing storage/__init__.py (neo4j/redis/qdrant) which triggers
# MemoryError on Windows when the neo4j driver initialises outside Docker.
target_metadata = None


def run_migrations_offline() -> None:
    """Generate SQL without connecting — used for dry runs / CI."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations with a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
