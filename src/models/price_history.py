from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.storage.db import Base


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    close_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap_usd: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_price_history_symbol_date"),)
