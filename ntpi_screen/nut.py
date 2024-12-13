import threading
import time
from nut2 import PyNUTClient
import queue

class NUT(threading.Thread):
    RUN = True
    
    def __init__(self, queue: queue.Queue) -> None:
        threading.Thread.__init__(self)
        self.queue = queue
        self.client = PyNUTClient()
    
    def cancel(self) -> None:
        self.RUN = False
    
    def run(self) -> None:
        while self.RUN:
            if not self.queue.full():
                self.queue.put(self.client.list_vars("desk-ups"))
                time.sleep(0.05)
        