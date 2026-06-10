from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker


Base = declarative_base()


def _ensure_sqlite_directory(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return

    sqlite_path = database_url.replace("sqlite:///", "", 1)
    if sqlite_path == ":memory:":
        return

    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)


def get_engine(database_url: str) -> Engine:
    _ensure_sqlite_directory(database_url)
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def get_session_factory(database_url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(database_url), autoflush=False, autocommit=False, future=True)


def init_db(database_url: str) -> Engine:
    engine = get_engine(database_url)

    import src.models.catalyst  # noqa: F401
    import src.models.coin  # noqa: F401
    import src.models.market_snapshot  # noqa: F401
    import src.models.price_history  # noqa: F401

    Base.metadata.create_all(bind=engine)
    return engine


@contextmanager
def session_scope(database_url: str) -> Iterator[Session]:
    SessionLocal = get_session_factory(database_url)
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
