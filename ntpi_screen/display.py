import threading
from typing import Dict
import busio
import board
import displayio
import terminalio
import time
import fourwire
from adafruit_display_text import label
import adafruit_displayio_sh1106
import queue
from gpsd2 import GpsResponse

class Display(threading.Thread):    
    WIDTH = 128
    HEIGHT = 64
    RUN = True
    
    MODE_MAP = [
        "UNKNOWN",
        "No Fix",
        "2D Fix",
        "3D Fix"
    ]
    
    def __init__(self, gps_queue: queue.Queue, nut_queue: queue.Queue) -> None:
        threading.Thread.__init__(self)
        displayio.release_displays()
        
        self.gps_queue = gps_queue
        self.nut_queue = nut_queue

        self.spi = busio.SPI(board.SCLK, board.MOSI, board.MISO)
        self.display_bus = fourwire.FourWire(
            self.spi,
            command=board.D24,
            chip_select=board.D8,
            reset=board.D25,
            baudrate=1000000,
        )

        self.display = adafruit_displayio_sh1106.SH1106(self.display_bus, width=self.WIDTH, height=self.HEIGHT, rotation=0, auto_refresh=True)

    def run(self) -> None:
        self.splash()
        time.sleep(1)
        self.display_loop()
    
    def cancel(self) -> None:
        self.RUN = False
    
    def splash(self) -> None:
        # Make the display context
        self.splash = displayio.Group()
        self.display.root_group = self.splash

        # Draw a label
        text = "NTPi.DByZ.uk"
        text_area = label.Label(
            terminalio.FONT, text=text, color=0xFFFFFF, x=28, y=self.HEIGHT // 2 - 1
        )
        self.splash.append(text_area)
        
        # self.display.refresh()

    def display_loop(self) -> None:
        while self.RUN:
            primary = displayio.Group()
            self.display.root_group = primary
            color_bitmap = displayio.Bitmap(self.WIDTH, self.HEIGHT, 1)
            color_palette = displayio.Palette(1)
            color_palette[0] = 0xFFFFFF  # White

            bg_sprite = displayio.TileGrid(color_bitmap, pixel_shader=color_palette, x=0, y=0)
            primary.append(bg_sprite)

            # Draw a smaller inner rectangle
            inner_bitmap = displayio.Bitmap(self.WIDTH - 2, self.HEIGHT - 2, 1)
            inner_palette = displayio.Palette(1)
            inner_palette[0] = 0x000000  # Black
            inner_sprite = displayio.TileGrid(
                inner_bitmap, pixel_shader=inner_palette, x=4, y=1
            )
            primary.append(inner_sprite)

            
            gps_mode = "Mode: "
            pos_lat = "Lat: "
            pos_lon = "Lon: "
            sats = "Sats: "
            curtime = "Time: "
            date = "Date: "
            if not self.gps_queue.empty():
                self.last_gps_reading: GpsResponse = self.gps_queue.get_nowait()
            
            if self.last_gps_reading:
                gps_mode += self.MODE_MAP[self.last_gps_reading.mode]
                pos_lat += f"{self.last_gps_reading.lat:.6f}"
                pos_lon += f"{self.last_gps_reading.lon:.6f}"
                sats += f"{self.last_gps_reading.sats_valid}/{self.last_gps_reading.sats}"
                split = self.last_gps_reading.time.split("T")
                date += split[0]
                curtime += split[1].split(".")[0]
                
            primary.append(label.Label(
                terminalio.FONT, text=gps_mode, color=0xFFFFFF, x=7, y=9
            ))
            primary.append(label.Label(
                terminalio.FONT, text=pos_lat, color=0xFFFFFF, x=7, y=19
            ))
            primary.append(label.Label(
                terminalio.FONT, text=pos_lon, color=0xFFFFFF, x=7, y=29
            ))
            primary.append(label.Label(
                terminalio.FONT, text=sats, color=0xFFFFFF, x=7, y=39
            ))
            primary.append(label.Label(
                terminalio.FONT, text=curtime, color=0xFFFFFF, x=7, y=49
            ))
                
            charge = 0
            status = ""
            
            if not self.nut_queue.empty():
                self.last_nut_reading: Dict = self.nut_queue.get_nowait()
            
            if self.last_nut_reading:
                charge = int(self.last_nut_reading["battery.charge"])
                status = self.last_nut_reading["ups.status"]
            
            
            # Draw charge outline
            charge_outline_bitmap = displayio.Bitmap(12, 30, 1)
            charge_outline_palette = displayio.Palette(1)
            charge_outline_palette[0] = 0xFFFFFF
            charge_outline_sprite = displayio.TileGrid(
                charge_outline_bitmap, pixel_shader=charge_outline_palette, x=106, y=6
            )
            primary.append(charge_outline_sprite)
            
            # clear inside
            charge_inline_bitmap = displayio.Bitmap(10, 28, 1)
            charge_inline_palette = displayio.Palette(1)
            charge_inline_palette[0] = 0x000000
            charge_inline_sprite = displayio.TileGrid(
                charge_inline_bitmap, pixel_shader=charge_inline_palette, x=107, y=7
            )
            primary.append(charge_inline_sprite)
            
            # fill
            fillHeight = round(26*(charge/100))
            charge_fill_bitmap = displayio.Bitmap(8, fillHeight, 1)
            charge_fill_palette = displayio.Palette(1)
            charge_fill_palette[0] = 0xFFFFFF
            charge_fill_sprite = displayio.TileGrid(
                charge_fill_bitmap, pixel_shader=charge_fill_palette, x=108, y=8 + (26 - fillHeight)
            )
            primary.append(charge_fill_sprite)
            
            primary.append(label.Label(
                terminalio.FONT, text=status, color=0xFFFFFF, x=107, y=46
            ))
            
            # self.display.refresh()
            time.sleep(1/30)