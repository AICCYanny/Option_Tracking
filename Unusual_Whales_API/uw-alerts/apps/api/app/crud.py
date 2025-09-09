from __future__ import annotations
import json
from datetime import datetime, timezone
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session
from .db.models import AlertRaw, MetricsGreeks, MetricsPrice, StateKV
from .db.models import MetricsBucket
from .utils.timeparse import to_utc_and_et_date
from sqlalchemy import select, inspect, update

now_utc = lambda: datetime.now(timezone.utc)

def upsert_alert(session: Session, 
                 *, 
                 alert_id: str, 
                 created_at: str, 
                 symbol: str | None,
                 option_symbol: str | None,
                 ask_volume: int | None,
                 bid_volume: int | None,
                 volume: int | None,
                 avg_fill: float | None,
                 close: float | None,
                 diff: float | None,
                 total_premium: float | None,
                 iv_change: float | None,
                 open_interest: int | None,
                 vol_oi_ratio: float | None,
                 multi_leg_vol_ratio: float | None,
                 raw_obj: dict) -> str:
    raw_str = json.dumps(raw_obj, ensure_ascii=False)
    created_utc, biz_date = to_utc_and_et_date(created_at)

    # SQLite UPSERT
    stmt = insert(AlertRaw).values(
        alert_id=alert_id,
        created_at=created_at,
        created_at_utc=created_utc,
        biz_date_et=biz_date,
        symbol=symbol,
        option_symbol=option_symbol,
        ask_volume=ask_volume,
        bid_volume=bid_volume,
        volume=volume,
        avg_fill=avg_fill,
        close=close,
        diff=diff,
        total_premium=total_premium,
        iv_change=iv_change,
        open_interest=open_interest,
        vol_oi_ratio=vol_oi_ratio,
        multi_leg_vol_ratio=multi_leg_vol_ratio,
        raw=raw_str,
    ).on_conflict_do_update(
        index_elements=[
            AlertRaw.biz_date_et,
            AlertRaw.option_symbol,
        ],
        set_={
            'created_at':created_at,
            'created_at_utc': created_utc,
            'symbol': symbol,
            'option_symbol': option_symbol,
            'ask_volume': ask_volume,
            'bid_volume': bid_volume,
            'volume': volume,
            'avg_fill': avg_fill,
            'close': close,
            'diff': diff,
            'total_premium': total_premium,
            'iv_change': iv_change,
            'open_interest': open_interest,
            'vol_oi_ratio': vol_oi_ratio,
            'multi_leg_vol_ratio': multi_leg_vol_ratio,
            'updated_at': now_utc(),
            'raw': raw_str,
        }
    )
    session.execute(stmt)

    row = session.execute(
        select(AlertRaw.alert_id).where(
            AlertRaw.biz_date_et == biz_date,
            AlertRaw.option_symbol == option_symbol,
        )
    ).scalar_one()
    return row

def save_greeks_snapshot(session: Session,
                            *,
                            alert_id: str,
                            option_symbol: str,
                            side: str,
                            dte: int,
                            strike: float,
                            delta: float,
                            gamma: float,
                            theta: float,
                            rho: float,
                            vega: float,
                            vanna: float,
                            charm: float,
                            volatility: float,
                            otm_pct: float,
                            data: dict) -> None:
    raw_str = json.dumps(data, ensure_ascii=False)
    stmt = insert(MetricsGreeks).values(
        alert_id=alert_id,
        snapshot_at=now_utc(),
        option_symbol=option_symbol,
        side=side,
        dte=dte,
        strike=strike,
        delta=delta,
        gamma=gamma,
        theta=theta,
        rho=rho,
        vega=vega,
        vanna=vanna,
        charm=charm,
        volatility=volatility,
        otm_pct=otm_pct,
        data_json=raw_str,
    ).on_conflict_do_update(
        index_elements=[MetricsGreeks.alert_id],
        set_={
            'snapshot_at': now_utc(),
            'option_symbol': option_symbol,
            'side': side,
            'dte': dte,
            'strike': strike,
            'delta': delta,
            'gamma': gamma,
            'theta': theta,
            'rho': rho,
            'vega': vega,
            'vanna': vanna,
            'charm': charm,
            'volatility': volatility,
            'otm_pct': otm_pct,
            'data_json': raw_str,
        }
    )
    session.execute(stmt)

def save_price_snapshot(session: Session,
                        *,
                        alert_id: str,
                        market_time: str | None,
                        stock_close: float,
                        stock_previous_close: float | None,
                        stock_volume: int | None,
                        stock_total_volume: int | None,
                        data: dict) -> None:
    raw_str = json.dumps(data, ensure_ascii=False)
    stmt = insert(MetricsPrice).values(
        alert_id=alert_id,
        snapshot_at=now_utc(),
        market_time=market_time,
        stock_close=stock_close,
        stock_previous_close=stock_previous_close,
        stock_volume=stock_volume,
        stock_total_volume=stock_total_volume,
        data_json=raw_str,
    ).on_conflict_do_update(
        index_elements=[MetricsPrice.alert_id],
        set_={
            'snapshot_at': now_utc(),
            'market_time': market_time,
            'stock_close': stock_close,
            'stock_previous_close': stock_previous_close,
            'stock_volume': stock_volume,
            'stock_total_volume': stock_total_volume,
            'data_json': raw_str,
        }
    )
    session.execute(stmt)

def get_state(session: Session, key: str) -> str | None:
    obj = session.get(StateKV, key)
    return obj.value if obj else None

def set_state(session: Session, key: str, value: str) -> None:
    stmt = insert(StateKV).values(
        key=key,
        value=value,
    ).on_conflict_do_update(
        index_elements=[StateKV.key],
        set_={
            'value': value,
        }
    )
    session.execute(stmt)

def save_bucket_metrics(session: Session, 
                        *, 
                        alert_id: str,
                        bucket_start_iso_utc: str,
                        bucket_end_iso_utc: str,
                        bucket_minutes: int | None,
                        option_symbol: str | None,
                        avg_price_ask: float | None,
                        avg_price_bid: float | None,
                        avg_price_mid: float | None,
                        avg_price_no: float | None,
                        avg_price: float | None,
                        avg_iv_low: float | None,
                        avg_iv_high: float | None,
                        volume_ask: int | None,
                        volume_bid: int | None,
                        volume_mid: int | None,
                        volume_no: int | None,
                        volume_multi: int | None,
                        total_volume: int | None,
                        bucket_multi_ratio: float | None,
                        premium_ask: float | None,
                        premium_bid: float | None,
                        premium_mid: float | None,
                        premium_no: float | None,
                        total_premium: float | None,
                        data: dict) -> None:
    raw_str = json.dumps(data, ensure_ascii=False)
    stmt = insert(MetricsBucket).values(
        alert_id=alert_id,
        bucket_start=bucket_start_iso_utc,
        bucket_end=bucket_end_iso_utc,
        bucket_minutes=bucket_minutes,
        option_symbol=option_symbol,
        avg_price_ask=avg_price_ask,
        avg_price_bid=avg_price_bid,
        avg_price_mid=avg_price_mid,
        avg_price_no=avg_price_no,
        avg_price=avg_price,
        avg_iv_low=avg_iv_low,
        avg_iv_high=avg_iv_high,
        volume_ask=volume_ask,
        volume_bid=volume_bid,
        volume_mid=volume_mid,
        volume_no=volume_no,
        volume_multi=volume_multi,
        total_volume=total_volume,
        bucket_multi_ratio=bucket_multi_ratio,
        premium_ask=premium_ask,
        premium_bid=premium_bid,
        premium_mid=premium_mid,
        premium_no=premium_no,
        total_premium=total_premium,
        data_json=raw_str,
    ).on_conflict_do_update(
        index_elements=[MetricsBucket.alert_id, MetricsBucket.bucket_end],
        set_={
            'bucket_minutes': bucket_minutes,
            'option_symbol': option_symbol,
            'avg_price_ask': avg_price_ask,
            'avg_price_bid': avg_price_bid,
            'avg_price_mid': avg_price_mid,
            'avg_price_no': avg_price_no,
            'avg_price': avg_price,
            'avg_iv_low': avg_iv_low,
            'avg_iv_high': avg_iv_high,
            'volume_ask': volume_ask,
            'volume_bid': volume_bid,
            'volume_mid': volume_mid,
            'volume_no': volume_no,
            'volume_multi': volume_multi,
            'total_volume': total_volume,
            'bucket_multi_ratio': bucket_multi_ratio,
            'premium_ask': premium_ask,
            'premium_bid': premium_bid,
            'premium_mid': premium_mid,
            'premium_no': premium_no,
            'total_premium': total_premium,
            'data_json': raw_str,
        }
    )
    session.execute(stmt)