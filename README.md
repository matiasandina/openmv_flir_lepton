# OpenMV H7 FLIR Lepton Recorder

`main.py` records FLIR Lepton frames and writes one CSV row per frame with the
frame index, clip index, monotonic `ticks_ms`, RTC timestamp, measured FPS, and
clip byte count.

Each run creates a session directory on the OpenMV filesystem:

```text
YYYYMMDDTHHMMSS/
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

## Two scripts

Use separate scripts for separate jobs:

```text
preview.py  OpenMV IDE live preview only; no recording, no commands.
main.py     headless recorder controlled by host_control.py.
```

This avoids mixing IDE preview with command-line control. In practice, the IDE
owns the camera/USB session while previewing, so `host_control.py` should be used
after the IDE is disconnected.

## IDE preview workflow

Use this when you are physically setting up the experiment:

1. Open `preview.py` in OpenMV IDE.
2. Run it.
3. Use the IDE framebuffer preview to aim/focus/place the cameras.
4. Stop the script and disconnect the IDE before using command-line recording.

## Recording workflow

Use this for actual acquisition:

1. Copy `main.py` to the OpenMV board as `/main.py`.
2. Disconnect the OpenMV IDE.
3. From the host computer, use `host_control.py` to set time, start, status, and
   stop recording. After stopping, use `shutdown` to sync storage and reboot the
   board into a fresh idle state.

The saved `preview_0000.mjpeg` file is separate from IDE preview. It is a
watchable movie written during recording for post-run inspection.

Host command examples:

```bash
uv run host_control.py set-time --port /dev/ttyACM0
uv run host_control.py start --port /dev/ttyACM0 --monitor
uv run host_control.py status --port /dev/ttyACM0
uv run host_control.py stop --port /dev/ttyACM0
uv run host_control.py shutdown --port /dev/ttyACM0
uv run host_control.py hard-reset --port /dev/ttyACM0
```

`shutdown` reboots the board: it sends `SHUTDOWN` (the board syncs storage and
calls `machine.reset()`), then waits for the board to re-enumerate and answer
again. The board is matched by its USB serial number, so a changed `COMx` /
`/dev/ttyACM*` name after reset is fine. If the board is wedged and never
acknowledges `SHUTDOWN`, `shutdown` automatically falls back to the OpenMV
IDE-style hard reset. `hard-reset` skips the graceful attempt and forces that
reset directly — use it when the recorder is unresponsive. Tune the wait with
`--reboot-timeout` (default 8s).

`start` sends the host computer's current local time to the OpenMV RTC first,
then sends `START`. Use `--no-set-time` only if you have already set the RTC and
want to preserve it. `--monitor` sends `STATUS` every second until Ctrl+C; this
does not stop the recorder.

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

From a Linux host, use:

```bash
uv run host_control.py set-time --port /dev/ttyACM0
```

On Windows, ports are usually named `COM3`, `COM4`, etc. With many USB devices
plugged in, list them first:

```powershell
uv run host_control.py list-ports
uv run host_control.py probe
uv run host_control.py set-time --port COM3
```

`list-ports` prints raw Windows/pyserial metadata only. `probe` opens each serial
port, sends `STATUS`, and marks a port as `recorder` only if it answers with the
recorder protocol, such as `state=idle` or `state=recording`.

If exactly one recorder answers, `--port` can be omitted for commands like
`start`. If several answer, the script asks you to choose one explicitly.

## Recording control

After boot and RTC setup, the board waits for serial commands:

```text
START
STOP
STATUS
SHUTDOWN
HELP
SET_TIME 2026-06-09T12:34:56
```

`SHUTDOWN` is an idle-only command. Send `STOP` first if recording is active. It
syncs storage and then calls `machine.reset()`, performing a full reboot
equivalent to pressing the RESET button. After reboot the board comes back idle
(AUTOSTART is off), ready for `STATUS` / `START`.

If host commands print back raw text like `STATUS`, `STOP`, or
`SET_TIME ...START`, the board is echoing serial input rather than running the
recorder command loop. Run `hard-reset` to reboot it (IDE-style), or
reset/power-cycle the board by hand, wait for boot, then run:

```powershell
uv run host_control.py probe --port COM3 --probe-seconds 2
```

Do not start acquisition until `probe` reports `recorder`.

Host helper examples:

```bash
uv run host_control.py start --port /dev/ttyACM0 --monitor
uv run host_control.py status --port /dev/ttyACM0
uv run host_control.py stop --port /dev/ttyACM0
uv run host_control.py shutdown --port /dev/ttyACM0
```

Windows examples:

```powershell
uv run host_control.py start --port COM3 --monitor
uv run host_control.py status --port COM3
uv run host_control.py stop --port COM3
uv run host_control.py shutdown --port COM3
```

`host_control.py` is a separate host-computer command sender. Close/disconnect
the OpenMV IDE before using it so it can open the board serial port.

## Convert Preview Movies

OpenMV `preview_0000.mjpeg` files are not reliably playable in ordinary Windows
players. Convert them on the host with ffmpeg:

```powershell
uv run convert_mjpeg.py 20260609T142253 --overwrite
```

This reads `20260609T142253/frames.csv`, estimates each clip's actual FPS from
the recorded `ticks_ms` timestamps, and writes:

```text
20260609T142253/preview_0000_640x480.mp4
```

The default conversion upscales 160x120 thermal frames to 640x480 with
nearest-neighbor scaling for easier visual inspection. Disable scaling with:

```powershell
uv run convert_mjpeg.py 20260609T142253 --scale none --overwrite
```

ffmpeg and ffprobe must be installed and available on `PATH`.

## Recording settings

The main settings are at the top of `main.py`:

```python
FRAME_SIZE = sensor.QQVGA
PIX_FORMAT = sensor.GRAYSCALE
STORAGE_ROOT = ""
RECORD_FORMAT = "both"
JPEG_QUALITY = 90
CLIP_SECONDS = 300
```

`STORAGE_ROOT = ""` writes session folders next to `main.py` on the board's
mounted filesystem. If you later want to force SD-card storage and know the
OpenMV mount path, set this to that path.

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
