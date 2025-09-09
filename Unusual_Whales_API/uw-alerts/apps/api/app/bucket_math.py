from __future__ import annotations
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
UTC = ZoneInfo("UTC")

FIFTEEN_SEC = 15 * 60
STEP_SEC    = 5 * 60 
HALF_SEC    = FIFTEEN_SEC // 2

def _prev_next_bound(dt_et: datetime) -> tuple[datetime, datetime]:
    """
    Given ET datetime, return nearest 15 min bucket.
    """
    assert dt_et.tzinfo is not None
    day0 = dt_et.replace(hour=0, minute=0, second=0, microsecond=0)
    sec = int((dt_et - day0).total_seconds())

    n = round((sec - 150) / STEP_SEC)
    center = day0 + timedelta(seconds=150 + n * STEP_SEC)
    start  = center - timedelta(seconds=HALF_SEC)
    end    = center + timedelta(seconds=HALF_SEC)
    return start, end

def session_bounds_et(day_et: datetime) -> tuple[datetime, datetime]:
    """
    Return target date in [09:30, 16:15] (ET).
    """
    assert day_et.tzinfo is not None
    start = day_et.replace(hour=0, minute=30, second=0, microsecond=0)
    end   = day_et.replace(hour=16, minute=15, second=0, microsecond=0)
    return start, end

def bucket_for_alert_iso(t_alert_iso: str) -> tuple[str, str] | None:
    """
    Input alert time in ISO, return cut bucket [start_utc_iso, end_utc_iso]
    if end <= start, return None.
    """
    # Interpret ISO
    ts = t_alert_iso
    if ts.endswith("Z"):
        ts = ts.replace("Z", "+00:00")
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
        
    # Convert to ET and apply cut
    dt_et = dt.astimezone(NY)
    prev_b, next_b = _prev_next_bound(dt_et)

    # Cut to current event
    sess_start, sess_end = session_bounds_et(dt_et)
    start_clip = max(prev_b, sess_start)
    end_clip = min(next_b, sess_end)

    if end_clip <= start_clip:
        return None
    
    # Output in UTC ISO
    start_utc = start_clip.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc   = end_clip.astimezone(ZoneInfo("UTC")).isoformat()
    return start_utc, end_utc