from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Float, Integer, Boolean, DateTime, ForeignKey, JSON
from datetime import datetime, timezone
from typing import Optional


class Base(DeclarativeBase):
    pass


class Market(Base):
    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    condition_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    question_id: Mapped[str] = mapped_column(String, index=True)
    question: Mapped[str] = mapped_column(String)
    yes_token_id: Mapped[str] = mapped_column(String, default="")
    no_token_id: Mapped[str] = mapped_column(String, default="")
    volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity_usd: Mapped[float] = mapped_column(Float, default=0.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    closes_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    price_snapshots: Mapped[list["PriceSnapshot"]] = relationship(back_populates="market", lazy="select")
    orderbook_snapshots: Mapped[list["OrderBookSnapshot"]] = relationship(back_populates="market", lazy="select")
    signals: Mapped[list["Signal"]] = relationship(back_populates="market", lazy="select")
    trades: Mapped[list["Trade"]] = relationship(back_populates="market", lazy="select")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    yes_price: Mapped[float] = mapped_column(Float)
    no_price: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    market: Mapped["Market"] = relationship(back_populates="price_snapshots")


class OrderBookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    token_id: Mapped[str] = mapped_column(String)
    bids: Mapped[list] = mapped_column(JSON, default=list)
    asks: Mapped[list] = mapped_column(JSON, default=list)
    best_bid: Mapped[float] = mapped_column(Float, default=0.0)
    best_ask: Mapped[float] = mapped_column(Float, default=1.0)
    spread: Mapped[float] = mapped_column(Float, default=0.0)
    midpoint: Mapped[float] = mapped_column(Float, default=0.5)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    market: Mapped["Market"] = relationship(back_populates="orderbook_snapshots")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    provider_name: Mapped[str] = mapped_column(String)
    fair_probability: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    market_midpoint: Mapped[float] = mapped_column(Float)
    edge: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)

    market: Mapped["Market"] = relationship(back_populates="signals")
    trades: Mapped[list["Trade"]] = relationship(back_populates="signal")


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_id: Mapped[Optional[int]] = mapped_column(ForeignKey("signals.id"), nullable=True, index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    is_paper: Mapped[bool] = mapped_column(Boolean, default=True)
    side: Mapped[str] = mapped_column(String)
    token_id: Mapped[str] = mapped_column(String)
    size_usd: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="OPEN")
    reasoning_trace: Mapped[dict] = mapped_column(JSON, default=dict)
    clob_order_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    signal: Mapped[Optional["Signal"]] = relationship(back_populates="trades")
    market: Mapped["Market"] = relationship(back_populates="trades")


class LearningEntry(Base):
    """Records every paper trade with outcome data — the bot's long-term memory."""
    __tablename__ = "learning_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id"), index=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"), index=True)
    market_question: Mapped[str] = mapped_column(String)           # full text for fuzzy matching
    market_category: Mapped[str] = mapped_column(String, default="politics")
    signal_sources_used: Mapped[str] = mapped_column(String, default="[]")  # JSON list
    blend_type: Mapped[str] = mapped_column(String, default="unknown")
    edge_at_entry: Mapped[float] = mapped_column(Float)
    confidence_at_entry: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String)
    entry_price: Mapped[float] = mapped_column(Float)
    outcome_label: Mapped[str] = mapped_column(String, default="OPEN", index=True)  # OPEN/WIN/LOSS/FLAT/ARCHIVED
    pnl_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    was_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    lesson_text: Mapped[str] = mapped_column(String, default="")
    last_referenced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    reference_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    session_label: Mapped[str] = mapped_column(String, default="unknown")
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class PerformanceLog(Base):
    __tablename__ = "performance_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    period_start: Mapped[datetime] = mapped_column(DateTime, index=True)
    period_end: Mapped[datetime] = mapped_column(DateTime)
    provider_name: Mapped[str] = mapped_column(String)
    trades_placed: Mapped[int] = mapped_column(Integer, default=0)
    trades_resolved: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    avg_edge_at_entry: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    max_drawdown_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
