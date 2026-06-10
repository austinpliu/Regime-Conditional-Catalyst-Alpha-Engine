from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
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


def _migrate_price_history_schema(engine: Engine) -> None:
    """Drop price_history if it has the old column layout so create_all can rebuild it."""
    insp = inspect(engine)
    if "price_history" not in insp.get_table_names():
        return
    cols = {col["name"] for col in insp.get_columns("price_history")}
    needs_rebuild = "close_usd" in cols or "coingecko_id" not in cols
    if needs_rebuild:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE price_history"))


def init_db(database_url: str) -> Engine:
    engine = get_engine(database_url)

    import src.models.catalyst  # noqa: F401
    import src.models.coin  # noqa: F401
    import src.models.market_snapshot  # noqa: F401
    import src.models.price_history  # noqa: F401

    _migrate_price_history_schema(engine)
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
