from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

UTC = ZoneInfo('UTC')
NY = ZoneInfo('America/New_York')

def to_utc_and_et_date(iso_ts: str) -> tuple[str, str]:
    s = iso_ts
    if s.endswith("Z"):
        s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    utc_iso = dt.astimezone(UTC).isoformat()
    et_date = dt.astimezone(NY).date().isoformat()
    return utc_iso, et_date