#!/usr/bin/env python3
"""Send control commands to an OpenMV recorder over USB serial.

Requires pyserial on the host:
    python3 -m pip install pyserial
"""

import argparse
import datetime as dt
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Missing pyserial. Install with: python3 -m pip install pyserial", file=sys.stderr)
    raise


OPENMV_HINTS = ("openmv", "pyboard", "stm", "usb serial", "cdc")


def serial_ports():
    return sorted(list_ports.comports(), key=lambda port: port.device)


def port_text(port):
    parts = [port.device]
    if port.description:
        parts.append(port.description)
    if port.manufacturer:
        parts.append("manufacturer=" + port.manufacturer)
    if port.vid is not None and port.pid is not None:
        parts.append("vid:pid=%04x:%04x" % (port.vid, port.pid))
    if port.serial_number:
        parts.append("serial=" + port.serial_number)
    return " | ".join(parts)


def looks_like_openmv(port):
    text = " ".join(
        str(x or "")
        for x in (
            port.device,
            port.description,
            port.manufacturer,
            port.product,
            port.interface,
        )
    ).lower()
    return any(hint in text for hint in OPENMV_HINTS)


def guess_port():
    ports = serial_ports()
    if not ports:
        raise SystemExit("No serial ports found.")

    candidates = [port for port in ports if looks_like_openmv(port)]
    if len(candidates) == 1:
        return candidates[0].device

    if len(candidates) > 1:
        print("Multiple likely OpenMV ports found:", file=sys.stderr)
        for port in candidates:
            print("  " + port_text(port), file=sys.stderr)
        raise SystemExit("Use --port COMx to select one.")

    if len(ports) == 1:
        return ports[0].device

    print("Multiple serial ports found:", file=sys.stderr)
    for port in ports:
        print("  " + port_text(port), file=sys.stderr)
    raise SystemExit("Use --port COMx to select one.")


def list_serial_ports():
    ports = serial_ports()
    if not ports:
        print("No serial ports found.")
        return
    for port in ports:
        marker = " * likely OpenMV" if looks_like_openmv(port) else ""
        print(port_text(port) + marker)


def send_lines(port, lines, wait):
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2) as ser:
        for line in lines:
            ser.write((line + "\n").encode("ascii"))
            ser.flush()
            time.sleep(0.05)
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
    parser.add_argument(
        "command",
        choices=("list-ports", "set-time", "start", "stop", "status", "help", "send"),
    )
    parser.add_argument("line", nargs="?", help="Raw line to send when command is 'send'.")
    parser.add_argument("--port", default=None, help="Serial device, e.g. /dev/ttyACM0.")
    parser.add_argument("--wait", type=float, default=1.0, help="Seconds to print replies after sending.")
    parser.add_argument(
        "--no-set-time",
        action="store_true",
        help="For 'start', send START without first setting RTC from host local time.",
    )
    args = parser.parse_args()

    if args.command == "list-ports":
        list_serial_ports()
        return

    port = args.port or guess_port()

    if args.command == "set-time":
        lines = [local_time_command()]
    elif args.command == "start" and not args.no_set_time:
        lines = [local_time_command(), "START"]
    elif args.command == "send":
        if not args.line:
            raise SystemExit("'send' requires a raw command line.")
        lines = [args.line]
    else:
        lines = [args.command.upper().replace("-", "_")]

    for line in lines:
        print("Sending to %s: %s" % (port, line))
    send_lines(port, lines, args.wait)


if __name__ == "__main__":
    main()
