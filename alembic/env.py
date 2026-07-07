from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

# Import your Base and config
from app.core.database import Base
from app.config import settings
import app.models

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Build the correct database URL for psycopg2 (synchronous)
# Convert from asyncpg format to psycopg2 format
database_url = settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql+psycopg2").replace("ssl=require", "sslmode=require")

# Override sqlalchemy.url in alembic.ini
config.set_main_option("sqlalchemy.url", database_url)

# Set target metadata for 'autogenerate' support
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()