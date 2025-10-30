from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from apps.api.app.routers.deps import get_session, get_alert_or_404
from apps.api.app.db.models import MetricsGreeks, MetricsPrice, MetricsBucket

router = APIRouter(
    prefix="/alerts/{alert_id}/metrics",
    tags=["metrics"]
)

@router.get("/greeks")
def get_greeks_latest(
    alert_id: str,
    limit: int = Query(1, ge=1, le=50, description="return lates N alerts, default 1"),
    session = Depends(get_session),
    _alert = Depends(get_alert_or_404), 
):
    rows = session.execute(
        select(MetricsGreeks)
        .where(MetricsGreeks.alert_id == alert_id)
        .order_by(desc(MetricsGreeks.snapshot_at))
        .limit(limit)
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail="Greeks not found")

    def to_dict(r: MetricsGreeks):
        return {
            "alert_id": r.alert_id,
            "snapshot_at": r.snapshot_at.isoformat(),
            "option_symbol": r.option_symbol,
            "side": r.side,
            "dte": r.dte,
            "strike": r.strike,
            "volatility": r.volatility,
            "delta": r.delta,
            "gamma": r.gamma,
            "theta": r.theta,
            "rho": r.rho,
            "vega": r.vega,
            "vanna": r.vanna,
            "charm": r.charm,
            "otm_pct": r.otm_pct,
            "expiry": r.expiry,
            "data": r.data_json,
        }

    return to_dict(rows[0]) if limit == 1 else [to_dict(r) for r in rows]


@router.get("/price")
def get_price_latest(
    alert_id: str,
    session = Depends(get_session),
    _alert = Depends(get_alert_or_404),
):
    row = session.execute(
        select(MetricsPrice)
        .where(MetricsPrice.alert_id == alert_id)
        .order_by(desc(MetricsPrice.snapshot_at))
        .limit(1)
    ).scalar_one_or_none()

    if not row:
        raise HTTPException(status_code=404, detail="Price not found")

    return {
        "alert_id": row.alert_id,
        "snapshot_at": row.snapshot_at.isoformat(),
        "market_time": row.market_time,
        "stock_close": row.stock_close,
        "stock_previous_close": row.stock_previous_close,
        "stock_volume": row.stock_volume,
        "stock_total_volume": row.stock_total_volume,
        "data": row.data_json,
    }


@router.get("/buckets")
def list_buckets(
    alert_id: str,
    limit: int = Query(5, ge=1, le=200),
    session = Depends(get_session),
    _alert = Depends(get_alert_or_404),
):
    rows = session.execute(
        select(MetricsBucket)
        .where(MetricsBucket.alert_id == alert_id)
        .order_by(desc(MetricsBucket.id))
        .limit(limit)
    ).scalars().all()

    if not rows:
        raise HTTPException(status_code=404, detail="Bucket not found")

    return [
        {
            "alert_id": r.alert_id,
            "bucket_start": r.bucket_start,
            "bucket_end": r.bucket_end,
            "bucket_minutes": r.bucket_minutes,
            "option_symbol": r.option_symbol,
            "avg_price_ask": r.avg_price_ask,
            "avg_price_bid": r.avg_price_bid,
            "avg_price_mid": r.avg_price_mid,
            "avg_price_no": r.avg_price_no,
            "avg_price": r.avg_price,
            "avg_iv_low": r.avg_iv_low,
            "avg_iv_high": r.avg_iv_high,
            "volume_ask": r.volume_ask,
            "volume_bid": r.volume_bid,
            "volume_mid": r.volume_mid,
            "volume_no": r.volume_no,
            "volume_multi": r.volume_multi,
            "total_volume": r.total_volume,
            "bucket_multi_ratio": r.bucket_multi_ratio,
            "premium_ask": r.premium_ask,
            "premium_bid": r.premium_bid,
            "premium_mid": r.premium_mid,
            "premium_no": r.premium_no,
            "total_premium": r.total_premium,
            "data": r.data_json,
        } for r in rows
    ]