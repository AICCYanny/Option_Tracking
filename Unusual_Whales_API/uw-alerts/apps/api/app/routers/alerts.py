from fastapi import APIRouter, Query, HTTPException
from typing import Optional, Literal
from sqlalchemy import select, desc, asc
from apps.api.app.db.engine import SessionLocal
from apps.api.app.db.models import AlertRaw

router = APIRouter(tags=['alerts'])

@router.get("/alerts")
def list_alerts(
    limit: int = Query(100, ge=1, le=500),
    order: Literal["asc", "desc"] = Query("asc"),
    symbol: Optional[str] = None,
    option_symbol: Optional[str] = None,
    biz_date: Optional[str] = Query(None, description="ET Trade Date YYYY-MM-DD"),
):
    """
    Read alerts:
    - default sort by created_at_utc ascending
    - filterable by symbol / option_symbol / biz_date
    """
    with SessionLocal() as session:
        stmt = select(
            AlertRaw.alert_id,
            AlertRaw.created_at,
            AlertRaw.created_at_utc,
            AlertRaw.biz_date_et,
            AlertRaw.symbol,
            AlertRaw.option_symbol,
            AlertRaw.ask_volume,
            AlertRaw.bid_volume,
            AlertRaw.close,
            AlertRaw.diff,
            AlertRaw.iv_change,
            AlertRaw.volume,
            AlertRaw.avg_fill,
            AlertRaw.total_premium,
            AlertRaw.open_interest,
            AlertRaw.vol_oi_ratio,
            AlertRaw.multi_leg_vol_ratio,
        )
        if biz_date:
            stmt = stmt.where(AlertRaw.biz_date_et == biz_date)
        if symbol:
            stmt = stmt.where(AlertRaw.symbol == symbol)
        if option_symbol:
            stmt = stmt.where(AlertRaw.option_symbol == option_symbol)

        stmt = stmt.order_by(
                asc(AlertRaw.created_at_utc) if order == 'asc' else desc(AlertRaw.created_at_utc)
            ).limit(limit)
        
        rows = session.execute(stmt).all()

    return [
        {
            'alert_id': r.alert_id,
            'created_at': r.created_at,
            'created_at_utc': r.created_at_utc,
            'biz_date_et': r.biz_date_et,
            'symbol': r.symbol,
            'option_symbol': r.option_symbol,
            'ask_volume': r.ask_volume,
            'bid_volume': r.bid_volume,
            'close': r.close,
            'diff': r.diff,
            'iv_change': r.iv_change,
            'volume': r.volume,
            'avg_fill': r.avg_fill,
            'total_premium': r.total_premium,
            'open_interest': r.open_interest,
            'vol_oi_ratio': r.vol_oi_ratio,
            'multi_leg_vol_ratio': r.multi_leg_vol_ratio,
        }
        for r in rows
    ]

@router.get("/alerts/{alert_id}")
def get_alert(alert_id: str):
    with SessionLocal() as session:
        row = session.execute(
            select(AlertRaw).where(AlertRaw.alert_id == alert_id)
        ).scalar_one_or_none()

        if not row:
            raise HTTPException(status_code=404, detail='Alert not found')
        return {
            'alert_id': row.alert_id,
            'created_at': row.created_at,
            'created_at_utc': row.created_at_utc,
            'biz_date_et': row.biz_date_et,
            'symbol': row.symbol,
            'option_symbol': row.option_symbol,
            'ask_volume': row.ask_volume,
            'bid_volume': row.bid_volume,
            'volume': row.volume,
            'avg_fill': row.avg_fill,
            'close': row.close,
            'diff': row.diff,
            'total_premium': row.total_premium,
            'iv_change': row.iv_change,
            'open_interest': row.open_interest,
            'vol_oi_ratio': row.vol_oi_ratio,
            'multi_leg_vol_ratio': row.multi_leg_vol_ratio,
            'raw': row.raw,
        }