import threading
import time
import gpsd2
import queue

class GPS(threading.Thread):
    RUN = True
    
    def __init__(self, queue: queue.Queue) -> None:
        threading.Thread.__init__(self)
        self.queue = queue
        gpsd2.connect()
    
    def cancel(self) -> None:
        self.RUN = False
    
    def run(self) -> None:
        while self.RUN:
            if not self.queue.full():
                packet = gpsd2.get_current()
                self.queue.put(packet)
                time.sleep(0.05)
        