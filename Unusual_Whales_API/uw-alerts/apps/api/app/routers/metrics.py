from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import select, desc
from apps.api.app.db.engine import SessionLocal
from apps.api.app.db.models import MetricsGreeks, MetricsPrice, MetricsBucket

router = APIRouter(tags=['metrics (deprecated)'])

DEPRECATION_MSG = (
    "This endpoint is deprecated. Please use nested routes: "
    "/alerts/{alert_id}/metrics/greeks | /price | /buckets"
)

@router.get("/metrics/greeks/{alert_id}")
def get_greeks(alert_id: str, response: Response):
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "2026-03-01"
    response.headers["Link"] = '</alerts/{alert_id}/metrics/greeks>; rel="successor-version"'
    with SessionLocal() as session:
        row = session.execute(
            select(MetricsGreeks)
            .where(MetricsGreeks.alert_id == alert_id)
            .order_by(desc(MetricsGreeks.snapshot_at))
            .limit(1)
        ).scalar_one_or_none()

        if not row:
            raise HTTPException(status_code=404, detail="Greeks not found")
        
        return {
            'alert_id': row.alert_id,
            'snapshot_at': row.snapshot_at.isoformat(),
            'option_symbol': row.option_symbol,
            'side': row.side,
            'dte': row.dte,
            'strike': row.strike,
            'volatility': row.volatility,
            'delta': row.delta,
            'gamma': row.gamma,
            'theta': row.theta,
            'rho': row.rho,
            'vega': row.vega,
            'vanna': row.vanna,
            'charm': row.charm,
            'data': row.data_json,
            "deprecated_hint": DEPRECATION_MSG,
        }
    
@router.get("/metrics/price/{alert_id}")
def get_price(alert_id: str, response: Response):
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "2026-03-01"
    response.headers["Link"] = '</alerts/{alert_id}/metrics/price>; rel="successor-version"'    
    with SessionLocal() as session:
        row = session.execute(
            select(MetricsPrice)
             .where(MetricsPrice.alert_id == alert_id)
             .order_by(desc(MetricsPrice.snapshot_at))
             .limit(1)
        ).scalar_one_or_none()

        if not row:
            raise HTTPException(status_code=404, detail="Price not found")
        
        return {
            'alert_id': row.alert_id,
            'snapshot_at': row.snapshot_at.isoformat(),
            'market_time': row.market_time,
            'stock_close': row.stock_close,
            'stock_previous_close': row.stock_previous_close,
            'stock_volume': row.stock_volume,
            'stock_total_volume': row.stock_total_volume,
            'data': row.data_json,
            "deprecated_hint": DEPRECATION_MSG,
        }
    
@router.get("/metrics/bucket")
def list_buckets(alert_id: str, limit: int = Query(5, ge=1, le=200), response = Response):
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "2026-03-01"
    response.headers["Link"] = '</alerts/{alert_id}/metrics/buckets>; rel="successor-version"'
    with SessionLocal() as session:
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
                'alert_id': r.alert_id,
                'bucket_start': r.bucket_start,
                'bucket_end': r.bucket_end,
                'bucket_minutes': r.bucket_minutes,
                'option_symbol': r.option_symbol,
                'avg_price_ask': r.avg_price_ask,
                'avg_price_bid': r.avg_price_bid,
                'avg_price_mid': r.avg_price_mid,
                'avg_price_no': r.avg_price_no,
                'avg_price': r.avg_price,
                'avg_iv_low': r.avg_iv_low,
                'avg_iv_high': r.avg_iv_high,
                'volume_ask': r.volume_ask,
                'volume_bid': r.volume_bid,
                'volume_mid': r.volume_mid,
                'volume_no': r.volume_no,
                'volume_multi': r.volume_multi,
                'total_volume': r.total_volume,
                'bucket_multi_ratio': r.bucket_multi_ratio,
                'premium_ask': r.premium_ask,
                'premium_bid': r.premium_bid,
                'premium_mid': r.premium_mid,
                'premium_no': r.premium_no,
                'total_premium': r.total_premium,
                'data': r.data_json,
                "deprecated_hint": DEPRECATION_MSG,
            } for r in rows
        ]