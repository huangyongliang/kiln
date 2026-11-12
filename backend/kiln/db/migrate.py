"""Apply Alembic migrations to the configured PostgreSQL database."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from kiln.db.url import to_sqlalchemy_url

_MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def upgrade_database(database_url: str) -> None:
    """Upgrade the database at `database_url` to the current schema."""

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", to_sqlalchemy_url(database_url))
    command.upgrade(cfg, "head")
