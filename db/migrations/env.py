# Alembic env stub — full async migration support to be wired in a later task
from logging.config import fileConfig
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Import metadata for autogenerate support
from db.models import Base  # noqa: E402
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


def run_migrations_online() -> None:
    # Async engine wiring to be completed when Alembic task is implemented
    pass


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
