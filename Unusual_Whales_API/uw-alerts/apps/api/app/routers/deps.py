from fastapi import Depends, HTTPException
from sqlalchemy import select
from apps.api.app.db.engine import SessionLocal
from apps.api.app.db.models import AlertRaw

def get_session():
    with SessionLocal() as session:
        yield session

def get_alert_or_404(alert_id: str, session=Depends(get_session)) -> AlertRaw:
    row = session.execute(
        select(AlertRaw)
        .where(AlertRaw.alert_id == alert_id)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    return row