import logging
import queue
import threading
from typing import Optional

from nut2 import PyNUTClient

logger = logging.getLogger(__name__)


class NUT(threading.Thread):
    POLL_INTERVAL = 2.0  # UPS state changes at <1Hz
    RETRY_INTERVAL = 5.0
    UPS_NAME = "desk-ups"

    def __init__(self, queue: queue.Queue) -> None:
        threading.Thread.__init__(self, daemon=True)
        logger.info("Initialising NUT")
        self.queue = queue
        self._stop = threading.Event()
        self.client: Optional[PyNUTClient] = None

    def cancel(self) -> None:
        self._stop.set()

    def _connect(self) -> bool:
        try:
            # 5s socket timeout so blocking reads don't hang shutdown.
            self.client = PyNUTClient(timeout=5)
            return True
        except Exception:
            logger.exception("Exception connecting to NUT")
            self.client = None
            return False

    def run(self) -> None:
        while not self._stop.is_set():
            if self.client is None:
                if not self._connect():
                    self._stop.wait(self.RETRY_INTERVAL)
                    continue

            try:
                data = self.client.list_vars(self.UPS_NAME)
                if not self.queue.full():
                    try:
                        self.queue.put_nowait(data)
                    except queue.Full:
                        pass
            except Exception:
                # Keep last-known on error — do NOT push UNAVAIL/zero.
                logger.exception("Exception fetching NUT data")
                self.client = None  # force reconnect next cycle

            self._stop.wait(self.POLL_INTERVAL)
