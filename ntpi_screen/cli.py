import queue
import typer
from ntpi_screen.display import Display
from ntpi_screen.gps import GPS

app = typer.Typer()

gps_queue = queue.Queue(5)

@app.command()
def main() -> None:
    display = Display(gps_queue)
    display.start()
    gps = GPS(gps_queue)
    gps.start()
    display.join()
    gps.join()

    

if __name__ == "__main__":
    app()