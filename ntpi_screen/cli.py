import queue
import typer
from ntpi_screen.display import Display
from ntpi_screen.gps import GPS
from ntpi_screen.nut import NUT

import signal
import sys

app = typer.Typer()

gps_queue = queue.Queue(5)
nut_queue = queue.Queue(5)

@app.command()
def main() -> None:
    display = Display(gps_queue, nut_queue)
    gps = GPS(gps_queue)
    nut = NUT(nut_queue)
    
    def signal_handler(sig, frame):
        display.cancel()
        gps.cancel()
        nut.cancel()
        display.join()
        gps.join()
        nut.join()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGABRT, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)
    
    display.start()
    gps.start()
    nut.start()
    
    signal.pause()

    

if __name__ == "__main__":
    app()