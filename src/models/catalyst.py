from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.coin import Coin
from src.storage.db import Base


class EventType(str, Enum):
    exchange_listing = "exchange_listing"
    mainnet_upgrade = "mainnet_upgrade"
    governance_vote = "governance_vote"
    token_unlock = "token_unlock"
    airdrop_snapshot = "airdrop_snapshot"
    conference = "conference"
    roadmap_release = "roadmap_release"
    partnership = "partnership"
    other = "other"


class CatalystCreate(BaseModel):
    coin_symbol: str = Field(min_length=1)
    event_type: EventType
    event_date: date
    description: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    confidence_score: float
    source_credibility: float

    @field_validator("coin_symbol")
    @classmethod
    def uppercase_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("description", "source_url")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be empty.")
        return value

    @field_validator("confidence_score", "source_credibility")
    @classmethod
    def normalize_score(cls, value: float) -> float:
        if 0 <= value <= 1:
            return float(value)
        if 1 < value <= 100:
            return float(value) / 100
        raise ValueError("Score values must be between 0 and 1, or between 1 and 100.")


class Catalyst(Base):
    __tablename__ = "catalysts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    coin_id: Mapped[int] = mapped_column(ForeignKey("coins.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    source_credibility: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    coin = relationship(Coin, backref="catalysts")
