import logging
import threading
import time
from nut2 import PyNUTClient
import queue

logger = logging.getLogger(__name__)


class NUT(threading.Thread):
    RUN = True
    
    def __init__(self, queue: queue.Queue) -> None:
        threading.Thread.__init__(self)
        logger.info("Initialising NUT")
        self.queue = queue
        self.client = PyNUTClient()
    
    def cancel(self) -> None:
        self.RUN = False
    
    def run(self) -> None:
        while self.RUN:
            if not self.queue.full():
                data = {
                    "battery.charge": 0,
                    "ups.status": "UNAVAIL"
                }
                try:
                    data = self.client.list_vars("desk-ups")
                except Exception as e:
                    logger.exception("Exception fetching NUT data")
                    
                self.queue.put(data)
                time.sleep(0.05)
        