from __future__ import annotations
import time
import httpx
from collections import deque
from typing import Any, Dict, Optional
from ..config import settings

class _RateLimiter:
    """Simple minute limiter: ensure <= settings.rpm_max requests per rolling 60s window"""
    def __init__(self, rpm: int):
        self.rpm = rpm
        self.window = deque()

    def acquire(self):
        now = time.monotonic()

        # Clean records from past 60s
        while self.window and now - self.window[0] > 60:
            self.window.popleft()

        if len(self.window) >= self.rpm:
            # Wait for next available window
            sleep_s = 60 - (now - self.window[0])
            if sleep_s > 0:
                time.sleep(sleep_s)
            
            now = time.monotonic()
            while self.window and now - self.window[0] > 60:
                self.window.popleft()
        # Record current time
        self.window.append(now)

class UWClient:
    def __init__(self, timeout: float | None = None):
        self.base = settings.uw_base_url
        self.timeout = timeout or settings.request_timeout_sec
        self.headers = {
            "Accept": "application/json, text/plain",
            "Authorization": f"Bearer {settings.uw_api_token}"
        }

        self._client = httpx.Client(timeout=self.timeout, headers=self.headers)
        self._limiter = _RateLimiter(settings.rpm_max)

    def _url(self, template: str, **kwargs) -> str:
        path = template.format(**kwargs)
        return f"{self.base}{path}"
    
    def _get(self, url: str, *, params: dict | None = None) -> httpx.Response:
        """Generalized GET: rpm limiter + 429 backoff"""
        backoff = settings.retry_backoff_base
        for attempt in range(6): # 5 reties max
            self._limiter.acquire()
            try:
                r = self._client.get(url, params=params)
                r.raise_for_status()
                return r
            except httpx.HTTPStatusError as e:
                if e.response is not None and e.response.status_code == 429:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 30) # exponential backoff, max 30s
                    continue
                raise
            except httpx.RequestError:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)
        raise RuntimeError(f"GET failed after retries: {url}")

    def fetch_alerts(
            self,
            page: int = 0,
            limit: Optional[int] = None,
            intraday_only: Optional[bool] = None,
            config_ids: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        
        params: Dict[str, Any] = {
            'page': page,
            'limit': limit if limit is not None else settings.alerts_limit,
            'intraday_only': intraday_only if intraday_only is not None else settings.alerts_intraday_only,
        }

        if config_ids:
            params['config_ids[]'] = config_ids

        url = self._url(settings.ep_alerts)
        r = self._get(url, params=params)
        return r.json()
    
    def fetch_option_intraday(self, option_symbol: str) -> Dict[str, Any]:
        url = self._url(settings.ep_option_intraday, option_symbol=option_symbol)
        r = self._get(url)
        return r.json()
    
    def fetch_stock_greeks(self, symbol: str, expiry: str) -> Dict[str, Any]:
        url = self._url(settings.ep_stock_greeks, symbol=symbol)
        params = {'expiry': expiry}
        r = self._get(url, params=params)
        return r.json()
    
    def fetch_stock_state(self, symbol: str) -> Dict[str, Any]:
        url = self._url(settings.ep_stock_state, symbol=symbol)
        r = self._get(url)
        return r.json()
    
    def close(self):
        self._client.close()
