from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional, Literal, List

from fastapi import APIRouter, HTTPException, Body, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, insert, update

from apps.api.app.routers.deps import get_session, get_alert_or_404
from apps.api.app.db.models import Review

router = APIRouter(
    prefix="/alerts/{alert_id}/review",
    tags=["review"]
)

# ---------- Pydantic Schemas ----------
Decision = Literal['accept', 'reject', 'watch']

class ReviewIn(BaseModel):
    decision: Optional[Decision] = None
    trade_types: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    row_version: Optional[int] = None
    reviewed_by: Optional[str] = None

class ReviewOut(BaseModel):
    alert_id: str
    decision: Optional[Decision] = None
    trade_types: List[str] = Field(default_factory=list)
    reason_codes: List[str] = Field(default_factory=list)
    notes: Optional[str] = None
    row_version: int
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None

def _dump_json(arr: List[str]) -> str:
    return json.dumps(arr, ensure_ascii=False)

def _load_json(s: Optional[str]) -> List[str]:
    if not s:
        return []
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return [str(x) for x in val]
    except Exception:
        pass
    return []

# ---------- Routes ----------

@router.get("", response_model=ReviewOut)
def get_review(
    alert_id: str,
    session = Depends(get_session),
    _alert = Depends(get_alert_or_404),   # 校验 alert 存在
):
    row = session.get(Review, alert_id)
    if not row:
        # return empty labels
        return ReviewOut(
            alert_id=alert_id,
            decision=None,
            trade_types=[],
            reason_codes=[],
            notes=None,
            row_version=0,
            reviewed_by=None,
            reviewed_at=None,
        )
    return ReviewOut(
        alert_id=row.alert_id,
        decision=row.decision,
        trade_types=_load_json(row.trade_types),
        reason_codes=_load_json(row.reason_codes),
        notes=row.notes,
        row_version=row.row_version,
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at.isoformat() if row.reviewed_at else None,
    )


@router.put("", response_model=ReviewOut)
def upsert_review(
    alert_id: str,
    payload: ReviewIn = Body(...),
    session = Depends(get_session),
    _alert = Depends(get_alert_or_404),   # 校验 alert 存在
):
    """
    Optimistic lock:
    - alert not found: insert row_version=0
    - alert found: assert row_version + alert == alert in db (bd +1)
    """
    now = datetime.now(timezone.utc)

    existing = session.get(Review, alert_id)
    if not existing:

        if payload.row_version not in (None, 0):
            raise HTTPException(status_code=409, detail="row_version mismatch; current=0")

        stmt = insert(Review).values(
            alert_id=alert_id,
            decision=payload.decision,
            trade_types=_dump_json(payload.trade_types),
            reason_codes=_dump_json(payload.reason_codes),
            notes=payload.notes,
            row_version=0,
            reviewed_by=payload.reviewed_by,
            reviewed_at=now,
        )
        session.execute(stmt)
        session.commit()
        return ReviewOut(
            alert_id=alert_id,
            decision=payload.decision,
            trade_types=payload.trade_types,
            reason_codes=payload.reason_codes,
            notes=payload.notes,
            row_version=0,
            reviewed_by=payload.reviewed_by,
            reviewed_at=now.isoformat(),
        )

    # Exists -> assert row_version + match
    if payload.row_version is None or payload.row_version != existing.row_version:
        raise HTTPException(status_code=409, detail=f"row_version mismatch; current={existing.row_version}")

    # Use returning to get new version
    stmt = (
        update(Review)
        .where(Review.alert_id == alert_id, Review.row_version == payload.row_version)
        .values(
            decision=payload.decision,
            trade_types=_dump_json(payload.trade_types),
            reason_codes=_dump_json(payload.reason_codes),
            notes=payload.notes,
            row_version=Review.row_version + 1,
            reviewed_by=payload.reviewed_by,
            reviewed_at=now,
        )
        .returning(Review.row_version, Review.reviewed_at)
    )
    res = session.execute(stmt).first()
    if not res:
        session.rollback()
        raise HTTPException(status_code=409, detail="update conflict")

    (new_version, reviewed_at) = res
    session.commit()

    return ReviewOut(
        alert_id=alert_id,
        decision=payload.decision,
        trade_types=payload.trade_types,
        reason_codes=payload.reason_codes,
        notes=payload.notes,
        row_version=int(new_version),
        reviewed_by=payload.reviewed_by,
        reviewed_at=reviewed_at.isoformat() if reviewed_at else None,
    )