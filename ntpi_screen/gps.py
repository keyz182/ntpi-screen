import logging
import queue
import threading

import gpsd2

logger = logging.getLogger(__name__)


class GPS(threading.Thread):
    POLL_INTERVAL = 1.0  # gpsd publishes at receiver cadence (~1Hz)
    RETRY_INTERVAL = 5.0

    def __init__(self, queue: queue.Queue) -> None:
        threading.Thread.__init__(self, daemon=True)
        logger.info("Initialising GPS")
        self.queue = queue
        self._stop = threading.Event()
        self._connected = False

    def cancel(self) -> None:
        self._stop.set()

    def _connect(self) -> bool:
        try:
            gpsd2.connect()
            # Set socket timeout so blocking reads don't hang shutdown.
            sock = getattr(gpsd2, "gpsd_socket", None)
            if sock is not None:
                try:
                    sock.settimeout(5.0)
                except Exception:
                    logger.exception("Could not set gpsd socket timeout")
            self._connected = True
            return True
        except Exception:
            logger.exception("Exception connecting to gpsd")
            self._connected = False
            return False

    def run(self) -> None:
        while not self._stop.is_set():
            if not self._connected:
                if not self._connect():
                    self._stop.wait(self.RETRY_INTERVAL)
                    continue

            if not self.queue.full():
                try:
                    packet = gpsd2.get_current()
                    self.queue.put_nowait(packet)
                except queue.Full:
                    pass
                except Exception:
                    logger.exception("Exception fetching GPS data")
                    self._connected = False  # force reconnect next cycle

            self._stop.wait(self.POLL_INTERVAL)
