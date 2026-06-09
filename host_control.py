#!/usr/bin/env python3
"""Send control commands to an OpenMV recorder over USB serial.

Requires pyserial on the host:
    python3 -m pip install pyserial
"""

import argparse
import datetime as dt
import glob
import sys
import time

try:
    import serial
except ImportError:
    print("Missing pyserial. Install with: python3 -m pip install pyserial", file=sys.stderr)
    raise


def guess_port():
    ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    if not ports:
        raise SystemExit("No /dev/ttyACM* or /dev/ttyUSB* serial ports found.")
    return ports[0]


def send_line(port, line, wait):
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2) as ser:
        ser.write((line + "\n").encode("ascii"))
        ser.flush()
        end = time.time() + wait
        while time.time() < end:
            data = ser.readline()
            if data:
                print(data.decode("utf-8", "replace").rstrip())


def local_time_command():
    now = dt.datetime.now().replace(microsecond=0)
    return "SET_TIME " + now.isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("set-time", "start", "stop", "status", "help", "send"))
    parser.add_argument("line", nargs="?", help="Raw line to send when command is 'send'.")
    parser.add_argument("--port", default=None, help="Serial device, e.g. /dev/ttyACM0.")
    parser.add_argument("--wait", type=float, default=1.0, help="Seconds to print replies after sending.")
    args = parser.parse_args()

    port = args.port or guess_port()

    if args.command == "set-time":
        line = local_time_command()
    elif args.command == "send":
        if not args.line:
            raise SystemExit("'send' requires a raw command line.")
        line = args.line
    else:
        line = args.command.upper().replace("-", "_")

    print("Sending to %s: %s" % (port, line))
    send_line(port, line, args.wait)


if __name__ == "__main__":
    main()
