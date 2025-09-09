from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Text, JSON, Integer, DateTime, UniqueConstraint, Numeric, Float

now_utc = lambda: datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class AlertRaw(Base):
    """
    Original alert rows
    """
    __tablename__ = "alerts_raw"

    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))
    created_at_utc : Mapped[str | None] = mapped_column(String(40), nullable=True)
    biz_date_et: Mapped[str | None] = mapped_column(String(10), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    option_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ask_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bid_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_fill: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    close: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    diff: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_premium: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    iv_change: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    open_interest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vol_oi_ratio: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    multi_leg_vol_ratio: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)

    raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    inserted_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc(), onupdate=now_utc())

    __table_args__ = (
        UniqueConstraint('biz_date_et', 'option_symbol', name='uq_alert_day_opt'),
    )

class MetricsGreeks(Base):
    """
    Immediate greeks snapshot (untracable)
    """
    __tablename__ = "metrics_greeks"
    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc())
    option_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    side: Mapped[str | None] = mapped_column(String(1), nullable=True)
    dte: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strike: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    gamma: Mapped[float | None] = mapped_column(Float, nullable=True)
    theta: Mapped[float | None] = mapped_column(Float, nullable=True)
    rho: Mapped[float | None] = mapped_column(Float, nullable=True)
    vega: Mapped[float | None] = mapped_column(Float, nullable=True)
    vanna: Mapped[float | None] = mapped_column(Float, nullable=True)
    charm: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    otm_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    data_json: Mapped[str] = mapped_column(Text)

class MetricsPrice(Base):
    """
    Immediate price snapshot (untracable)
    """
    __tablename__ = "metrics_price"
    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc())
    market_time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stock_close: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    stock_previous_close: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    stock_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stock_total_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)

    data_json: Mapped[str] = mapped_column(Text)

class MetricsBucket(Base):
    """
    Bucket cummulation (volume/premium)
    """
    __tablename__ = "metrics_bucket"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[str] = mapped_column(String(64))
    bucket_start: Mapped[str] = mapped_column(String(40))
    bucket_end: Mapped[str] = mapped_column(String(40))
    bucket_minutes: Mapped[int] = mapped_column(Integer)
    option_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    avg_price_ask: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    avg_price_bid: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    avg_price_mid: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    avg_price_no: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    avg_price: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    avg_iv_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_iv_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_ask: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_bid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_mid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume_multi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bucket_multi_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_ask: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    premium_bid: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    premium_mid: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    premium_no: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    total_premium: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)

    data_json: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("alert_id", "bucket_end", name="uq_alert_bucket"),)

class Review(Base):
    """
    Manual label
    """
    __tablename__ = "review"
    alert_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trade_types: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_codes: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class StateKV(Base):
    """
    Simple states
    """
    __tablename__ = "state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)