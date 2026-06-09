# OpenMV H7 + FLIR Lepton recorder.
#
# Files written per recording session:
#   /YYYYMMDDTHHMMSS/
#     raw_0000.bin
#     preview_0000.mjpeg
#     frames.csv
#
# If the RTC is not already valid, send this line over USB serial:
#   SET_TIME 2026-06-09T12:34:56

import gc
import image
import mjpeg
import os
import pyb
import sensor
import time


# ---------------------------- User settings ----------------------------

MIN_VALID_YEAR = 2024
REQUIRE_VALID_RTC = True
AUTOSTART = False
STORAGE_ROOT = ""

FRAME_SIZE = sensor.QQVGA
PIX_FORMAT = sensor.GRAYSCALE
JPEG_QUALITY = 90
RECORD_FORMAT = "both"  # "both", "imageio", or "mjpeg".

# Closing clips periodically makes long recordings less fragile if power is lost.
# Set to 0 to record one long file.
CLIP_SECONDS = 300

SYNC_EVERY_FRAMES = 30
STATUS_EVERY_FRAMES = 30

ENABLE_RADIOMETRY_IF_AVAILABLE = True
TEMP_MIN_C = 20.0
TEMP_MAX_C = 40.0


# ----------------------------- Utilities -------------------------------

def mkdir(path):
    try:
        os.mkdir(path)
        return True
    except OSError:
        return False


def join_path(parent, child):
    if not parent:
        return child
    if parent.endswith("/"):
        return parent + child
    return parent + "/" + child


def weekday_monday1(year, month, day):
    # Sakamoto's algorithm. Convert 0=Sunday to OpenMV STM32 RTC 1=Monday..7=Sunday.
    offsets = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    y = year
    if month < 3:
        y -= 1
    w = (y + y // 4 - y // 100 + y // 400 + offsets[month - 1] + day) % 7
    if w == 0:
        return 7
    return w


def rtc_tuple():
    return pyb.RTC().datetime()


def rtc_is_valid():
    return rtc_tuple()[0] >= MIN_VALID_YEAR


def timestamp_from_tuple(dt):
    return "%04d%02d%02dT%02d%02d%02d" % (
        dt[0],
        dt[1],
        dt[2],
        dt[4],
        dt[5],
        dt[6],
    )


def iso_from_tuple(dt):
    return "%04d-%02d-%02dT%02d:%02d:%02d" % (
        dt[0],
        dt[1],
        dt[2],
        dt[4],
        dt[5],
        dt[6],
    )


def parse_set_time(line):
    # Accept: SET_TIME 2026-06-09T12:34:56
    line = line.strip()
    if not line:
        return None

    parts = line.split()
    if len(parts) != 2 or parts[0] != "SET_TIME":
        return None

    stamp = parts[1].replace("Z", "")
    for ch in ("-", ":", "T"):
        stamp = stamp.replace(ch, " ")
    fields = stamp.split()
    if len(fields) != 6:
        return None

    year = int(fields[0])
    month = int(fields[1])
    day = int(fields[2])
    hour = int(fields[3])
    minute = int(fields[4])
    second = int(fields[5])
    weekday = weekday_monday1(year, month, day)
    return (year, month, day, weekday, hour, minute, second, 0)


def set_rtc_from_usb_line(line):
    try:
        dt = parse_set_time(line)
    except Exception as err:
        print("Bad SET_TIME command:", err)
        return False

    if dt is None:
        print("Expected: SET_TIME 2026-06-09T12:34:56")
        return False

    pyb.RTC().datetime(dt)
    print("RTC set to", iso_from_tuple(rtc_tuple()))
    return True


def read_command(vcp):
    if not vcp.any():
        return None

    line = vcp.readline()
    if not line:
        return None

    try:
        return line.decode().strip()
    except Exception:
        return str(line).strip()


def wait_for_valid_rtc():
    if rtc_is_valid():
        print("RTC is", iso_from_tuple(rtc_tuple()))
        return True

    print("RTC is not valid:", rtc_tuple())
    print("Send over USB serial: SET_TIME 2026-06-09T12:34:56")

    if not REQUIRE_VALID_RTC:
        print("Continuing despite invalid RTC.")
        return False

    vcp = pyb.USB_VCP()
    while not rtc_is_valid():
        line = read_command(vcp)
        if line:
            set_rtc_from_usb_line(line)
        pyb.delay(100)

    return True


def setup_lepton():
    print("Resetting Lepton...")
    sensor.reset()

    print(
        "Lepton Res (%dx%d)"
        % (
            sensor.ioctl(sensor.IOCTL_LEPTON_GET_WIDTH),
            sensor.ioctl(sensor.IOCTL_LEPTON_GET_HEIGHT),
        )
    )
    print(
        "Radiometry Available: "
        + ("Yes" if sensor.ioctl(sensor.IOCTL_LEPTON_GET_RADIOMETRY) else "No")
    )
    if sensor.ioctl(sensor.IOCTL_LEPTON_GET_RADIOMETRY):
        if ENABLE_RADIOMETRY_IF_AVAILABLE:
            try:
                sensor.ioctl(sensor.IOCTL_LEPTON_SET_MODE, True, False)
                sensor.ioctl(sensor.IOCTL_LEPTON_SET_RANGE, TEMP_MIN_C, TEMP_MAX_C)
                print(
                    "Radiometry mapping enabled: %.1fC..%.1fC"
                    % (TEMP_MIN_C, TEMP_MAX_C)
                )
            except Exception as err:
                print("Radiometry setup failed:", err)
    else:
        print("Radiometry not reported by firmware/module; recording relative 8-bit grayscale.")
    try:
        print("Lepton Refresh: %s Hz" % sensor.ioctl(sensor.IOCTL_LEPTON_GET_REFRESH))
    except Exception:
        pass

    sensor.set_pixformat(PIX_FORMAT)
    sensor.set_framesize(FRAME_SIZE)
    sensor.skip_frames(time=5000)


def open_clip(session_dir, clip_index):
    raw = None
    preview = None

    if RECORD_FORMAT == "imageio" or RECORD_FORMAT == "both":
        path = "%s/raw_%04d.bin" % (session_dir, clip_index)
        print("Opening", path)
        try:
            raw = image.ImageIO(path, "w")
        except Exception as err:
            print("ImageIO open failed:", err)
            if RECORD_FORMAT == "imageio":
                raise

    if RECORD_FORMAT == "mjpeg" or RECORD_FORMAT == "both":
        path = "%s/preview_%04d.mjpeg" % (session_dir, clip_index)
        print("Opening", path)
        try:
            preview = mjpeg.Mjpeg(path)
        except Exception as err:
            print("MJPEG open failed:", err)
            if RECORD_FORMAT == "mjpeg":
                raise

    if raw is None and preview is None:
        raise OSError("No recorder could be opened.")

    return raw, preview


def write_frame(recorders, img):
    raw, preview = recorders
    if raw is not None:
        raw.write(img)
    if preview is not None:
        preview.add_frame(img, quality=JPEG_QUALITY)


def recorder_count(recorder):
    try:
        return recorder.count()
    except Exception:
        return 0


def recorder_size(recorder):
    try:
        return recorder.size()
    except Exception:
        return 0


def sync_clip(recorders):
    for recorder in recorders:
        if recorder is not None:
            try:
                recorder.sync()
            except Exception:
                pass


def close_clip(recorders):
    for recorder in recorders:
        if recorder is not None:
            try:
                recorder.close()
            except Exception:
                pass


def raw_count(recorders):
    return recorder_count(recorders[0])


def raw_size(recorders):
    return recorder_size(recorders[0])


def preview_count(recorders):
    return recorder_count(recorders[1])


def preview_size(recorders):
    return recorder_size(recorders[1])


def print_help():
    print("Commands:")
    print("  SET_TIME 2026-06-09T12:34:56")
    print("  START")
    print("  STOP")
    print("  STATUS")
    print("  SHUTDOWN")
    print("  HELP")


def wait_for_start(vcp):
    print("Ready. Send START to record, STATUS for state, HELP for commands.")
    if AUTOSTART:
        print("AUTOSTART enabled.")
        return True

    while True:
        line = read_command(vcp)
        if line == "START":
            if REQUIRE_VALID_RTC and not rtc_is_valid():
                print("RTC is not valid:", rtc_tuple())
                print("Send: SET_TIME 2026-06-09T12:34:56")
                continue
            return True
        if line == "STATUS":
            print("state=idle rtc=%s format=%s" % (iso_from_tuple(rtc_tuple()), RECORD_FORMAT))
        elif line == "HELP":
            print_help()
        elif line and line.startswith("SET_TIME "):
            set_rtc_from_usb_line(line)
        elif line == "STOP":
            print("state=idle; STOP ignored")
        elif line == "SHUTDOWN":
            try:
                os.sync()
            except Exception:
                pass
            print("SHUTDOWN complete. Safe to unplug or reset.")
            return False
        elif line:
            print("Unknown command:", line)
            print_help()
        pyb.delay(100)


# ------------------------------ Main -----------------------------------

def main():
    setup_lepton()
    vcp = pyb.USB_VCP()
    print_help()

    while True:
        if not wait_for_start(vcp):
            return

        session_name = timestamp_from_tuple(rtc_tuple())
        session_dir = join_path(STORAGE_ROOT, session_name)
        if not mkdir(session_dir):
            print("Storage error: could not create session directory", session_dir)
            print("Check that the OpenMV filesystem or SD card is mounted/writable.")
            continue

        log_path = join_path(session_dir, "frames.csv")
        try:
            log = open(log_path, "w")
        except OSError as err:
            print("Storage error: could not open", log_path, err)
            print("Check that the OpenMV filesystem or SD card is mounted/writable.")
            continue
        log.write(
            "frame,clip,raw_frame,preview_frame,ticks_ms,rtc_iso,fps,raw_bytes,preview_bytes,format,radiometry,temp_min_c,temp_max_c\n"
        )
        log.flush()

        clock = time.clock()
        frame_index = 0
        clip_index = 0
        clip_start_ms = time.ticks_ms()
        recorders = open_clip(session_dir, clip_index)
        radiometry = bool(sensor.ioctl(sensor.IOCTL_LEPTON_GET_RADIOMETRY))

        print("Recording started:", session_dir)

        try:
            while True:
                command = read_command(vcp)
                if command == "STOP":
                    print("STOP received.")
                    break
                if command == "STATUS":
                    print(
                        "state=recording frames=%d clip=%d fps=%.2f rtc=%s dir=%s"
                        % (frame_index, clip_index, clock.fps(), iso_from_tuple(rtc_tuple()), session_dir)
                    )
                elif command == "HELP":
                    print_help()
                elif command and command.startswith("SET_TIME "):
                    set_rtc_from_usb_line(command)
                elif command == "SHUTDOWN":
                    print("Stop recording before SHUTDOWN.")
                elif command:
                    print("Unknown command:", command)
                    print_help()

                clock.tick()
                img = sensor.snapshot()
                write_frame(recorders, img)

                now_ticks = time.ticks_ms()
                now_rtc = rtc_tuple()
                fps = clock.fps()

                log.write(
                    "%d,%d,%d,%d,%d,%s,%.2f,%d,%d,%s,%d,%.1f,%.1f\n"
                    % (
                        frame_index,
                        clip_index,
                        raw_count(recorders),
                        preview_count(recorders),
                        now_ticks,
                        iso_from_tuple(now_rtc),
                        fps,
                        raw_size(recorders),
                        preview_size(recorders),
                        RECORD_FORMAT,
                        1 if radiometry else 0,
                        TEMP_MIN_C,
                        TEMP_MAX_C,
                    )
                )

                frame_index += 1

                if frame_index % SYNC_EVERY_FRAMES == 0:
                    log.flush()
                    sync_clip(recorders)
                    try:
                        os.sync()
                    except Exception:
                        pass

                if frame_index % STATUS_EVERY_FRAMES == 0:
                    print(
                        "frames=%d clip=%d fps=%.2f rtc=%s"
                        % (frame_index, clip_index, fps, iso_from_tuple(now_rtc))
                    )

                if CLIP_SECONDS and time.ticks_diff(now_ticks, clip_start_ms) >= CLIP_SECONDS * 1000:
                    close_clip(recorders)
                    log.flush()
                    clip_index += 1
                    clip_start_ms = time.ticks_ms()
                    gc.collect()
                    recorders = open_clip(session_dir, clip_index)

        except KeyboardInterrupt:
            print("Stopping.")
        finally:
            close_clip(recorders)
            log.flush()
            log.close()
            try:
                os.sync()
            except Exception:
                pass
            print("Saved session", session_dir)


main()
