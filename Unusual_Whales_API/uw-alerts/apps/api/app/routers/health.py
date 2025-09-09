from fastapi import APIRouter
from datetime import datetime, timezone

router = APIRouter(tags=['health'])

@router.get("/healthz")
def healthz():
    return {"status": "ok", "ts": datetime.now(timezone.utc).isoformat()}

@router.get("/readyz")
def readyz():
    return {"status": "ready"}
