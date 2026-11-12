"""Normalize configured database URLs for SQLAlchemy."""


def to_sqlalchemy_url(database_url: str) -> str:
    """Return a SQLAlchemy URL that uses the psycopg driver."""

    if database_url.startswith(("postgresql+psycopg://", "postgresql+psycopg2://")):
        return database_url
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    return database_url
