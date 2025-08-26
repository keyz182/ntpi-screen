import logging
import threading
import time
import gpsd2
import queue

logger = logging.getLogger(__name__)


class GPS(threading.Thread):
    RUN = True
    
    def __init__(self, queue: queue.Queue) -> None:
        threading.Thread.__init__(self)
        logger.info("Initialising GPS")
        self.queue = queue
        gpsd2.connect()
    
    def cancel(self) -> None:
        self.RUN = False
    
    def run(self) -> None:
        while self.RUN:
            if not self.queue.full():
                try:
                    packet = gpsd2.get_current()
                    self.queue.put(packet)
                except Exception as e:
                    logger.exception("Exception fetching GPS data")
                time.sleep(0.05)
        