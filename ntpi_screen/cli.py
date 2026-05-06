import logging
import queue
import signal
import sys
from types import FrameType
from typing import Optional

import typer

from ntpi_screen.display import Display
from ntpi_screen.gps import GPS
from ntpi_screen.nut import NUT

logger = logging.getLogger(__name__)

app = typer.Typer()

gps_queue: queue.Queue = queue.Queue(5)
nut_queue: queue.Queue = queue.Queue(5)

JOIN_TIMEOUT = 2.0


@app.command()
def main() -> None:
    logger.info("Starting ntpi-screen")
    display = Display(gps_queue, nut_queue)
    gps = GPS(gps_queue)
    nut = NUT(nut_queue)

    def signal_handler(sig: int, frame: Optional[FrameType]) -> None:
        logger.info("Shutting down ntpi-screen (signal=%s)", sig)
        display.cancel()
        gps.cancel()
        nut.cancel()
        # Bounded joins — threads are daemon=True so process exits even
        # if a blocking syscall stalls beyond the timeout.
        display.join(timeout=JOIN_TIMEOUT)
        gps.join(timeout=JOIN_TIMEOUT)
        nut.join(timeout=JOIN_TIMEOUT)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)
    # SIGABRT intentionally NOT trapped — it usually signals a real
    # C-extension fault that should not be silently swallowed.

    display.start()
    gps.start()
    nut.start()

    signal.pause()


if __name__ == "__main__":
    app()
