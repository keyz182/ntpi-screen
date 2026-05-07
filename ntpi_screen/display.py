import logging
import queue
import threading
from typing import Dict, Optional

import board
import busio
import displayio
import fourwire
import terminalio
from adafruit_display_text import label
import adafruit_displayio_sh1106
from gpsd2 import GpsResponse

logger = logging.getLogger(__name__)


class Display(threading.Thread):
    WIDTH = 128
    HEIGHT = 64
    FRAME_INTERVAL = 1 / 30  # 30 fps target

    MODE_MAP = [
        "UNKNOWN",
        "No Fix",
        "2D Fix",
        "3D Fix",
    ]

    # Charge gauge geometry
    CHARGE_FILL_W = 8
    CHARGE_FILL_H = 26
    CHARGE_FILL_X = 108
    CHARGE_FILL_Y = 8

    def __init__(self, gps_queue: queue.Queue, nut_queue: queue.Queue) -> None:
        threading.Thread.__init__(self, daemon=True)
        logger.info("Initialising display")
        displayio.release_displays()

        self.gps_queue = gps_queue
        self.nut_queue = nut_queue

        self._stop = threading.Event()

        self.spi = busio.SPI(board.SCLK, board.MOSI, board.MISO)
        self.display_bus = fourwire.FourWire(
            self.spi,
            command=board.D24,
            chip_select=board.D8,
            reset=board.D25,
            baudrate=1000000,
        )

        # auto_refresh=False: we drive refresh manually to avoid races
        # between background refresh thread and scene-graph mutation.
        self.display = adafruit_displayio_sh1106.SH1106(
            self.display_bus,
            width=self.WIDTH,
            height=self.HEIGHT,
            rotation=0,
            auto_refresh=False,
        )

        self.last_gps_reading: Optional[GpsResponse] = None
        # Seed NUT so first frame has shape before NUT thread fetches.
        self.last_nut_reading: Dict = {"battery.charge": 0, "ups.status": "INIT"}
        self._last_fill_height: int = -1

        self._splash_group: Optional[displayio.Group] = None
        self._primary_group: Optional[displayio.Group] = None
        self._gps_mode_label: Optional[label.Label] = None
        self._lat_label: Optional[label.Label] = None
        self._lon_label: Optional[label.Label] = None
        self._sats_label: Optional[label.Label] = None
        self._time_label: Optional[label.Label] = None
        self._status_label: Optional[label.Label] = None
        self._charge_fill_bitmap: Optional[displayio.Bitmap] = None
        # Cache last text per label. Skip setter when unchanged; on change,
        # blank-then-set forces Label to fully rebuild its glyph bitmap
        # (Blinka adafruit_display_text leaves residue on plain reassignment).
        self._label_text_cache: Dict[int, str] = {}

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            self._show_splash()
            self.display.refresh()
            # Interruptible sleep so shutdown is prompt.
            self._stop.wait(1.0)
            if self._stop.is_set():
                return
            self._build_primary_scene()
            self._display_loop()
        finally:
            try:
                displayio.release_displays()
            except Exception:
                logger.exception("Exception releasing display on shutdown")

    def _show_splash(self) -> None:
        self._splash_group = displayio.Group()
        self.display.root_group = self._splash_group

        text_area = label.Label(
            terminalio.FONT,
            text="NTPi.DByZ.uk",
            color=0xFFFFFF,
            x=28,
            y=self.HEIGHT // 2 - 1,
        )
        self._splash_group.append(text_area)

    def _build_primary_scene(self) -> None:
        """Build the per-frame scene graph ONCE. Per-frame code mutates
        label.text and the charge fill bitmap in place — no allocations."""
        primary = displayio.Group()

        # Outer white fill
        bg_bitmap = displayio.Bitmap(self.WIDTH, self.HEIGHT, 1)
        bg_palette = displayio.Palette(1)
        bg_palette[0] = 0xFFFFFF
        primary.append(displayio.TileGrid(bg_bitmap, pixel_shader=bg_palette, x=0, y=0))

        # Inner black rect (preserves original asymmetric bevel intentionally)
        inner_bitmap = displayio.Bitmap(self.WIDTH - 2, self.HEIGHT - 2, 1)
        inner_palette = displayio.Palette(1)
        inner_palette[0] = 0x000000
        primary.append(
            displayio.TileGrid(inner_bitmap, pixel_shader=inner_palette, x=4, y=1)
        )

        # GPS labels. background_color forces opaque black behind glyphs
        # so .text setter rebuild does not leave residue from prior text.
        self._gps_mode_label = label.Label(
            terminalio.FONT,
            text="Mode: ",
            color=0xFFFFFF,
            background_color=0x000000,
            x=7,
            y=9,
        )
        self._lat_label = label.Label(
            terminalio.FONT,
            text="Lat: ",
            color=0xFFFFFF,
            background_color=0x000000,
            x=7,
            y=19,
        )
        self._lon_label = label.Label(
            terminalio.FONT,
            text="Lon: ",
            color=0xFFFFFF,
            background_color=0x000000,
            x=7,
            y=29,
        )
        self._sats_label = label.Label(
            terminalio.FONT,
            text="Sats: ",
            color=0xFFFFFF,
            background_color=0x000000,
            x=7,
            y=39,
        )
        self._time_label = label.Label(
            terminalio.FONT,
            text="Time: ",
            color=0xFFFFFF,
            background_color=0x000000,
            x=7,
            y=49,
        )
        primary.append(self._gps_mode_label)
        primary.append(self._lat_label)
        primary.append(self._lon_label)
        primary.append(self._sats_label)
        primary.append(self._time_label)

        # Charge gauge: outline (white) 12x30 at (106,6)
        outline_bitmap = displayio.Bitmap(12, 30, 1)
        outline_palette = displayio.Palette(1)
        outline_palette[0] = 0xFFFFFF
        primary.append(
            displayio.TileGrid(outline_bitmap, pixel_shader=outline_palette, x=106, y=6)
        )

        # Charge gauge: inner clear (black) 10x28 at (107,7)
        inline_bitmap = displayio.Bitmap(10, 28, 1)
        inline_palette = displayio.Palette(1)
        inline_palette[0] = 0x000000
        primary.append(
            displayio.TileGrid(inline_bitmap, pixel_shader=inline_palette, x=107, y=7)
        )

        # Charge fill: fixed-size 8x26 bitmap. Pixels mutated per-update.
        # Palette: 0=black, 1=white. Initially all-black (0% charge look).
        fill_palette = displayio.Palette(2)
        fill_palette[0] = 0x000000
        fill_palette[1] = 0xFFFFFF
        self._charge_fill_bitmap = displayio.Bitmap(
            self.CHARGE_FILL_W, self.CHARGE_FILL_H, 2
        )
        primary.append(
            displayio.TileGrid(
                self._charge_fill_bitmap,
                pixel_shader=fill_palette,
                x=self.CHARGE_FILL_X,
                y=self.CHARGE_FILL_Y,
            )
        )

        self._status_label = label.Label(
            terminalio.FONT,
            text="",
            color=0xFFFFFF,
            background_color=0x000000,
            x=107,
            y=46,
        )
        primary.append(self._status_label)

        self._primary_group = primary
        self.display.root_group = primary

    def _set_label_text(self, lbl: Optional[label.Label], new_text: str) -> None:
        """Update label text only if changed. Blank-then-set forces Label
        to fully rebuild its glyph bitmap (Blinka displayio leaves residue
        from prior text on plain reassignment, even with background_color).
        """
        if lbl is None:
            return
        key = id(lbl)
        if self._label_text_cache.get(key) == new_text:
            return
        lbl.text = ""
        lbl.text = new_text
        self._label_text_cache[key] = new_text

    def _update_charge_fill(self, fill_height: int) -> None:
        """Redraw charge fill bitmap only when value changes."""
        if fill_height == self._last_fill_height:
            return
        bm = self._charge_fill_bitmap
        if bm is None:
            return
        threshold = self.CHARGE_FILL_H - fill_height
        for y in range(self.CHARGE_FILL_H):
            val = 1 if y >= threshold else 0
            for x in range(self.CHARGE_FILL_W):
                bm[x, y] = val
        self._last_fill_height = fill_height

    def _display_loop(self) -> None:
        while not self._stop.is_set():
            try:
                # Drain GPS queue (latest only)
                if not self.gps_queue.empty():
                    self.last_gps_reading = self.gps_queue.get_nowait()

                if self.last_gps_reading is not None:
                    gps_mode = "Mode: " + self.MODE_MAP[self.last_gps_reading.mode]
                    pos_lat = f"Lat: {self.last_gps_reading.lat:.6f}"
                    pos_lon = f"Lon: {self.last_gps_reading.lon:.6f}"
                    sats = f"Sats: {self.last_gps_reading.sats_valid}/{self.last_gps_reading.sats}"
                    split = self.last_gps_reading.time.split("T")
                    date_part = split[0] if len(split) > 0 else ""
                    time_part = split[1].split(".")[0] if len(split) > 1 else ""
                    cur_time = f"Time: {time_part}"
                    _ = date_part  # currently unused on screen; kept for parity
                else:
                    gps_mode = "Mode: "
                    pos_lat = "Lat: "
                    pos_lon = "Lon: "
                    sats = "Sats: "
                    cur_time = "Time: "

                # Mutate label text in place — no Group/Bitmap allocations.
                self._set_label_text(self._gps_mode_label, gps_mode)
                self._set_label_text(self._lat_label, pos_lat)
                self._set_label_text(self._lon_label, pos_lon)
                self._set_label_text(self._sats_label, sats)
                self._set_label_text(self._time_label, cur_time)

                # Drain NUT queue (latest only)
                if not self.nut_queue.empty():
                    self.last_nut_reading = self.nut_queue.get_nowait()

                charge = int(self.last_nut_reading.get("battery.charge", 0))
                status = str(self.last_nut_reading.get("ups.status", ""))

                fill_height = min(
                    max(1, round(self.CHARGE_FILL_H * (charge / 100))),
                    self.CHARGE_FILL_H,
                )
                self._update_charge_fill(fill_height)

                self._set_label_text(self._status_label, status)

                self.display.refresh()
            except Exception:
                logger.exception("Exception in display loop")

            # Interruptible sleep — wakes on shutdown.
            self._stop.wait(self.FRAME_INTERVAL)
