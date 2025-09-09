import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

def _csv(name: str) -> list[str] | None:
    raw = os.getenv(name, '').strip()
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]

@dataclass
class Setting:
    uw_api_token: str = os.getenv('UW_TOKEN', '')
    uw_base_url: str = os.getenv('UW_BASE_URL', '')

    ep_alerts: str = os.getenv('UW_ALERTS_ENDPOINT', '')
    ep_option_intraday: str = os.getenv('UW_OPTION_INTRADAY_ENDPOINT', '')
    ep_stock_greeks: str = os.getenv('UW_STOCK_GREEKS_ENDPOINT', '')
    ep_stock_state: str = os.getenv('UW_STOCK_STATE_ENDPOINT', '')

    poll_interval_sec: float = float(os.getenv('POLL_INTERVAL_SEC', ''))
    request_timeout_sec: float = float(os.getenv('REQUEST_TIMEOUT_SEC', ''))

    alerts_limit: int = int(os.getenv('ALERTS_LIMIT', ''))
    alerts_intraday_only: bool = os.getenv('ALERTS_INTRADAY_ONLY', '').lower() == 'true'

    rpm_max: int = int(os.getenv('RPM_MAX', ''))
    retry_backoff_base: float = float(os.getenv('RETRY_BACKOFF_BASE', ''))
    max_process_per_cycle: int = int(os.getenv('MAX_PROCESS_PER_CYCLE', ''))
    warm_start_mode: str = os.getenv('WARM_START_MODE', '')
    live_alert_grace_sec: int = int(os.getenv('LIVE_ALERT_GRACE_SEC', ''))

    poller_enabled: bool = os.getenv("POLLER_ENABLED", "true").lower() == "true"

settings = Setting()
