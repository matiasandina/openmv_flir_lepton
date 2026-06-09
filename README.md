# OpenMV H7 FLIR Lepton Recorder

`main.py` records FLIR Lepton frames and writes one CSV row per frame with the
frame index, clip index, monotonic `ticks_ms`, RTC timestamp, measured FPS, and
clip byte count.

Each run creates a session directory on the OpenMV filesystem:

```text
/YYYYMMDDTHHMMSS/
  raw_0000.bin
  preview_0000.mjpeg
  frames.csv
```

By default `RECORD_FORMAT = "both"` writes:

```text
raw_0000.bin        exact OpenMV ImageIO frames for analysis
preview_0000.mjpeg  lossy but easy-to-watch preview movie
```

If FPS or SD-card bandwidth suffers, change `RECORD_FORMAT` in `main.py` to
`"imageio"` for exact values only or `"mjpeg"` for preview video only.

## First test workflow

There are two different kinds of preview/control:

```text
OpenMV IDE preview: live framebuffer view for aiming and setup.
MJPEG preview file: saved movie for watching after the run.
```

For a first bench test, use the OpenMV IDE:

1. Copy `main.py` to the OpenMV board / SD card and run it from the IDE.
2. The script resets the Lepton and then idles.
3. While idle, the IDE preview window should show live frames if
   `ENABLE_IDE_PREVIEW = True`.
4. Use the IDE serial terminal to send:

```text
SET_TIME 2026-06-09T12:34:56
START
STATUS
STOP
```

This mode is for setup and debugging. It is expected to be slower because frames
are also being pushed to the IDE preview.

For production, close/disconnect the IDE and use `host_control.py` from the host
computer. In that mode there is no live IDE preview; the board records files and
you inspect `preview_0000.mjpeg` afterward.

## RTC setup

The OpenMV RTC does not become correct just because the IDE is connected. It must
be set from some external source after power-up unless you have working backup
timekeeping. This script allows idle preview before the RTC is set, but it will
not start recording until the RTC is valid by default.

Open the OpenMV USB serial terminal and send one line like:

```text
SET_TIME 2026-06-09T12:34:56
```

The timestamp should be local wall time unless you decide to standardize on UTC.
If you want the board to start recording even with an invalid RTC, change
`REQUIRE_VALID_RTC = False` in `main.py`.

From a Linux host with `pyserial` installed, you can also use:

```bash
python3 host_control.py set-time --port /dev/ttyACM0
```

On Windows, ports are usually named `COM3`, `COM4`, etc. With many USB devices
plugged in, list them first:

```powershell
python host_control.py list-ports
python host_control.py set-time --port COM3
```

If exactly one likely OpenMV/STM USB serial port is found, `--port` can be
omitted. If several are found, the script asks you to choose one explicitly.

## Recording control

After boot and RTC setup, the board waits for serial commands:

```text
START
STOP
STATUS
HELP
SET_TIME 2026-06-09T12:34:56
```

Host helper examples:

```bash
python3 host_control.py start --port /dev/ttyACM0
python3 host_control.py status --port /dev/ttyACM0
python3 host_control.py stop --port /dev/ttyACM0
```

Windows examples:

```powershell
python host_control.py start --port COM3
python host_control.py status --port COM3
python host_control.py stop --port COM3
```

`host_control.py` is a separate host-computer command sender. When the OpenMV IDE
is connected, it may already own the USB connection, so use one of these modes:

```text
IDE preview/debug mode: type commands in the OpenMV IDE terminal.
Headless mode: close/disconnect the IDE and use host_control.py.
```

When the IDE debug connection is active, `main.py` flushes captured frames to the
IDE preview window while idle and while recording. This can reduce frame rate, so
set `ENABLE_IDE_PREVIEW = False` for maximum throughput.

## Recording settings

The main settings are at the top of `main.py`:

```python
FRAME_SIZE = sensor.QQVGA
PIX_FORMAT = sensor.GRAYSCALE
ENABLE_IDE_PREVIEW = True
RECORD_FORMAT = "both"
JPEG_QUALITY = 90
CLIP_SECONDS = 300
```

`CLIP_SECONDS` closes and starts a new clip every five minutes so a long run is
less likely to lose everything after a power interruption. Set it to `0` for one
long clip file.

## Radiometry and raw values

`Radiometry Available: No` usually means the OpenMV firmware/module combination
does not see a radiometric Lepton core. With the 160x120 resolution you likely
have a Lepton 3.x-class sensor, but calibrated temperature output requires a
radiometric variant such as Lepton 3.5.

When radiometry is unavailable, OpenMV can still record the 8-bit grayscale
thermal image. Treat those pixel values as relative intensity after the Lepton /
driver mapping, not calibrated Celsius. When radiometry is reported as available,
`main.py` tries to enable OpenMV's measurement mapping over `TEMP_MIN_C` to
`TEMP_MAX_C`, and records that metadata in `frames.csv`.
