"""Alembic environment configuration."""

import os
from logging.config import fileConfig

from alembic import context

from fiskal.db import models  # noqa: F401
from fiskal.db.base import Base
from fiskal.db.engine import make_engine

# This is the Alembic Config object, which provides access to the values within
# the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata for 'autogenerate' support.
target_metadata = Base.metadata


def get_database_url() -> str:
    """Return a SQLite URL for running offline migrations.

    The ``FISKAL_MIGRATION_URL`` environment variable overrides the default.
    Defaults to a temporary database so ``alembic upgrade head`` can run in CI
    without configuration.
    """
    return os.environ.get(
        "FISKAL_MIGRATION_URL", "sqlite:///./fiskal-migration-check.db"
    )


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url") or get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    url = config.get_main_option("sqlalchemy.url") or get_database_url()
    connectable = make_engine(url)

    with connectable.connect() as connection:
        # Batch operations rebuild tables (copy, drop, rename). With foreign
        # keys enforced, dropping a table that other rows reference fails, so
        # enforcement is off during migrations and integrity is checked before
        # committing. The pragma is a no-op inside a transaction, so it is set
        # and committed first.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if violations:
                raise RuntimeError(
                    f"Migration left foreign-key violations: {violations}"
                )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
