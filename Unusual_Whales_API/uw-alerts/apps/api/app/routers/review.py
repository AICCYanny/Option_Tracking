from __future__ import annotations 
import json
from datetime import datetime, timezone
from typing import Optional, Literal, List

from fastapi import APIRouter, HTTPException, Body, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update, insert, desc, asc, outerjoin, delete

from apps.api.app.db.engine import SessionLocal
from apps.api.app.db.models import Review, AlertRaw

router = APIRouter(tags=['review'])

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
@router.get("/review/{alert_id}", response_model=ReviewOut)
def get_review(alert_id: str):
    with SessionLocal() as session:
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
    
@router.put("/review/{alert_id}", response_model=ReviewOut)
def upsert_review(alert_id: str, payload: ReviewIn = Body(...)):
    """
    Optimistic lock:
    - alert not found: insert row_version=0
    - alert found: assert row_version + alert == alert in db (bd +1)
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        existing = session.get(Review, alert_id)
        if not existing:

            if payload.row_version not in (None, 0):
                raise HTTPException(status_code=409, detail="row_version mismatch; current=0")
            # Allow create new if row_version doesn't exist
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
        result = session.execute(stmt).first()
        if not result:
            # Mismatch caused by asynchronous update
            session.rollback()
            raise HTTPException(status_code=409, detail="update conflict")
        (new_version, reviewed_at) = result
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
    
@router.get("/reviews", response_model=List[ReviewOut])
def list_reviews(
    biz_date: Optional[str] = Query(None, description="ET trade date YYYY-MM-DD"),
    decision: Optional[Decision] = None,
    limit: int = Query(200, ge=1, le=1000),
    order: Literal['asc', 'desc'] = Query('asc'),
):
    """Cross table filter by trade date, sorted by created_at
    """
    with SessionLocal() as session:
        A, R = AlertRaw, Review
        j = outerjoin(A, R, A.alert_id == R.alert_id)

        stmt = select(
            A.alert_id,
            A.created_at_utc,
            R.decision,
            R.trade_types,
            R.reason_codes,
            R.notes,
            R.row_version,
            R.reviewed_by,
            R.reviewed_at,
        )
        stmt = stmt.select_from(j)

        if biz_date:
            stmt = stmt.where(A.biz_date_et == biz_date)

        if decision is not None:
            stmt = stmt.where(R.decision == decision)

        stmt = stmt.order_by(
            A.created_at_utc.asc() if order == 'asc' else A.created_at_utc.desc()
        ).limit(limit)

        rows = session.execute(stmt).all()

    out: List[ReviewOut] = []
    for a_id, _, d, tt, rc, notes, ver, by, rat in rows:
       if d is None and decision is not None:
            continue
       out.append(ReviewOut(
           alert_id=a_id,
           decision=d,
           trade_types=_load_json(tt),
           reason_codes=_load_json(rc),
           notes=notes,
           row_version=ver or 0,
           reviewed_by=by,
           reviewed_at=rat.isoformat() if rat else None,
       ))
    return rows

@router.delete("/review/{alert_id}", status_code=204)
def delete_review(alert_id: str):
    with SessionLocal() as session:
        res = session.execute(delete(Review).where(Review.alert_id == alert_id))
        session.commit()
    return Response(status_code=204)

@router.delete("/reviews", status_code=204)
def purge_all_reviews(confirm: bool = Query(False, description="Set true to delete ALL reviews")):
    if not confirm:
        raise HTTPException(status_code=400, detail="Set ?confirm=true to purge ALL reviews")
    with SessionLocal() as session:
        session.execute(delete(Review))
        session.commit()
    return Response(status_code=204)