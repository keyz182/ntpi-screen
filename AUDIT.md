# ntpi-screen audit

Codebase: 295 LOC across 5 modules. Threaded poller→queue→display pipeline driving SH1106 OLED via Adafruit Blinka displayio on Pi 5.

## Symptom mapping

User reports: OOM after ~1h + screen glitching → cron-restart workaround.

Both symptoms have a single likely root cause in `display.py:74-176`.

## Critical — memory growth

**`display_loop` allocates the entire scene graph every frame at 30 Hz.**

Per iteration (line 77-171):
- 1 × `displayio.Group`
- 5 × `displayio.Bitmap` (incl. 128×64 framebuffer-sized)
- 5 × `displayio.Palette`
- 5 × `displayio.TileGrid`
- 6 × `adafruit_display_text.label.Label` (each builds its own Group + Bitmap + Palette + TileGrid internally)

≈30 top-level objects/frame × 30 fps = **~900 displayio object allocations/sec**, plus underlying Pillow `Image` buffers (Blinka displayio backs onto PIL on Linux — not MCU CircuitPython where this pattern is idiomatic on a static heap).

Why it leaks in practice on Pi/Blinka:
1. `auto_refresh=True` runs a background refresh thread holding strong refs to `root_group` and descendants. Swapping `root_group` doesn't immediately drop the previous tree — refresh thread may still hold it for one tick.
2. Pillow `Image` objects use C-allocated buffers; rapid alloc/free at this rate fragments glibc heap on aarch64. Process RSS grows even when Python-side GC reclaims promptly.
3. Bitmap buffer rate: 128×64×1bpp ≈ 1KB; 30/frame × 30fps = ~900KB/s of churn. Fragmentation alone explains hourly RSS climb.

**Fix shape (do not implement here, just direction):** build the scene graph once in `splash()` or an `_init_scene()`, retain refs (`self._gps_label`, `self._charge_fill_bitmap`, etc.), then per-frame mutate via `label.text = ...` and bitmap fill ops only. Set `auto_refresh=False` and call `self.display.refresh()` exactly once per frame.

## Critical — screen glitch

**Race between auto-refresh thread and `root_group` reassignment.**

`auto_refresh=True` (line 47) ⇒ Adafruit's refresh thread walks the displayio tree at the display's native refresh rate, pushing frames over SPI at 1 MHz. Meanwhile `display_loop` reassigns `self.display.root_group = primary` (line 78) every 33 ms with a freshly-constructed tree.

When refresh-thread is mid-traversal at the moment main-thread swaps root_group + appends children, partial frame ships → torn rows / "glitch" pixels on SH1106. Same fix as above: static graph + manual `refresh()`.

## High — `splash` method shadowed by attribute

`display.py:60-70`:
```python
def splash(self) -> None:           # method
    self.splash = displayio.Group() # ← now an instance attr, method gone
```
After first call, `self.splash` is a Group. Currently only invoked once from `run()` so it works by luck. Latent footgun — any future re-call (e.g. on reconnect) would `TypeError: 'Group' object is not callable`.

## High — NUT polling rate

`nut.py:30` polls `list_vars("desk-ups")` every 50 ms = **20 Hz over a TCP socket** to upsd. UPS state changes at <1 Hz; appropriate poll is ~1–5 s. Currently:
- saturates upsd connection
- on transient socket error, line 33 catches, but data is still `put` with default `{"battery.charge": 0, "ups.status": "UNAVAIL"}` → display flickers to 0% / UNAVAIL on every transient failure rather than retaining last-known. Likely a contributor to perceived "glitch" if it's also a value-flicker rather than pixel-tear.

## High — GPS polling rate

`gps.py:23` calls `gpsd2.get_current()` at 20 Hz. gpsd publishes at the receiver's NMEA cadence (typically 1 Hz). 19/20 polls return identical data; pure CPU + queue churn.

## Medium — `RUN` as class attribute

All three threads (`Display`, `GPS`, `NUT`) declare `RUN = True` at class scope. `self.RUN = False` in `cancel()` shadows per-instance, works, but is fragile. Should be `self.RUN = True` in `__init__` or use `threading.Event`.

## Medium — `displayio.release_displays()` only on init

`display.py:33` releases at construction. After exception in `display_loop` (line 172 catches all and continues), no path reinitialises the bus. Long-running corruption of SPI state could persist silently.

## Medium — connect failures kill threads at construction

`gps.py:18` calls `gpsd2.connect()` in `__init__`, `nut.py:19` instantiates `PyNUTClient()` in `__init__`. If gpsd or upsd isn't ready at boot (network/USB enumeration race), `cli.py:23` raises and the whole service crash-loops. systemd `Restart=always` masks it but it's noisy. Move connect into `run()` with retry.

## Medium — `signal.SIGABRT` trapped

`cli.py:39` registers SIGABRT as graceful-shutdown. ABRT typically signals abort/crash from C extension — trapping it can hide real faults (e.g. a Blinka SPI panic). Drop SIGABRT, keep SIGINT/SIGTERM.

## Low — service unit

`ntpi-screen.service`:
- `ExecStart=/path/to/venv/bin/ntpi-screen` is a literal placeholder. Presumably edited on-device but should be templated or documented.
- No `RuntimeMaxSec=` — user implements 1h restart externally; systemd can do this natively (`RuntimeMaxSec=3600`).
- No `MemoryHigh=` / `MemoryMax=` cgroup cap — would convert OOM-kill into bounded restart instead of host-wide pressure.
- `RestartSec=1` + `StartLimitIntervalSec=0` = unbounded tight restart loop on persistent failure. Add `StartLimitBurst` + backoff.
- Missing `SIGTERM` in cli signal handler — systemd sends SIGTERM by default for stop, currently not caught → falls through to default handler (terminate, no graceful shutdown).

## Low — queue & put semantics

- `gps.py:21` and `nut.py:34` use `put()` (blocking) after a `not full` check — TOCTOU but harmless given single producer per queue.
- Queue size 5 is fine.

## Low — error handling

`display.py:172` catches `Exception` and continues with no backoff. If exception is persistent (e.g. SPI bus dead), loop spins at 30 Hz logging exceptions — log-rate-limit filter masks volume but CPU is wasted.

## Low — code hygiene

- No tests, no `[tool.ruff]` config, no CI.
- Mutable globals in `cli.py:16-17` (acceptable for this scale).
- `signal_handler` lacks type hints (project elsewhere has them).
- `__init__.py` has 6 lines — unread, may want check.
- `log-rate-limit` 30s window is good; keep.

## Priority order for remediation

1. Static scene graph + `auto_refresh=False` + manual `refresh()` — fixes leak AND tear simultaneously.
2. Drop NUT poll to 1–5 s; drop GPS poll to 1 Hz. Free CPU + reduce upsd load.
3. NUT: keep last-known on transient error rather than overwriting with `UNAVAIL`.
4. Rename `splash` method or attribute — eliminate shadow.
5. Add `RuntimeMaxSec=3600` + `MemoryHigh=` to systemd unit; remove external restart cron.
6. Move `gpsd2.connect()` / `PyNUTClient()` into `run()` with retry.
7. Catch `SIGTERM`, drop `SIGABRT`.
8. Add `StartLimitBurst` backoff to unit.

(1) alone likely eliminates both reported symptoms.
