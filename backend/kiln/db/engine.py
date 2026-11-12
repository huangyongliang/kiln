"""SQLAlchemy engine factory."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from kiln.db.url import to_sqlalchemy_url


def create_db_engine(database_url: str) -> Engine:
    return create_engine(
        to_sqlalchemy_url(database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5},
    )
