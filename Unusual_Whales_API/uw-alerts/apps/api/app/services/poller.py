from __future__ import annotations
import threading
from typing import Optional
from scripts.poller_cli import run_loop

class PollerService:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
    
    def start(self):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=run_loop, args=(self._stop,), name="uw-poller", daemon=True
        )
        self._thread.start()

    def stop(self):
        if not self._thread:
            return 
        self._stop.set()
        self._thread.join(timeout=10)
        self._thread = None