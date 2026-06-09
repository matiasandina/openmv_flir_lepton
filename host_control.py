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


RECORDER_STATUS_PREFIXES = ("state=idle", "state=recording")


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
    if port.hwid:
        parts.append("hwid=" + port.hwid)
    return " | ".join(parts)


def resolve_port(explicit_port, probe_seconds):
    if explicit_port:
        return explicit_port

    results = probe_recorders(probe_seconds, quiet=True)
    recorders = [result for result in results if result["recorder"]]
    if len(recorders) == 1:
        return recorders[0]["port"].device

    if len(recorders) > 1:
        print("Multiple recorder ports responded:", file=sys.stderr)
        for result in recorders:
            print("  " + result["port"].device + " | " + result["reply"], file=sys.stderr)
        raise SystemExit("Use --port COMx to select one.")

    ports = serial_ports()
    if not ports:
        raise SystemExit("No serial ports found.")

    print("No recorder ports responded to STATUS.", file=sys.stderr)
    print("Available serial ports:", file=sys.stderr)
    for port in ports:
        print("  " + port_text(port), file=sys.stderr)
    raise SystemExit("Use 'probe' to inspect ports, or pass --port COMx explicitly.")


def list_serial_ports():
    ports = serial_ports()
    if not ports:
        print("No serial ports found.")
        return
    for port in ports:
        print(port_text(port))


def find_serial_port(device):
    for port in serial_ports():
        if port.device.lower() == device.lower():
            return port
    raise SystemExit("Serial port not found: %s" % device)


def send_lines(port, lines, wait):
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2) as ser:
        for line in lines:
            send_serial_line(ser, line)
            time.sleep(0.05)
        print_replies(ser, wait)


def send_serial_line(ser, line):
    ser.write((line + "\n").encode("ascii"))
    ser.flush()


def print_replies(ser, seconds):
    for line in read_replies(ser, seconds):
        print(line)


def read_replies(ser, seconds):
    lines = []
    end = time.time() + seconds
    while time.time() < end:
        data = ser.readline()
        if data:
            lines.append(data.decode("utf-8", "replace").rstrip())
    return lines


def is_recorder_reply(line):
    return any(line.startswith(prefix) for prefix in RECORDER_STATUS_PREFIXES)


def probe_recorder_port(port, seconds):
    try:
        with serial.Serial(port.device, 115200, timeout=0.2, write_timeout=2) as ser:
            send_serial_line(ser, "STATUS")
            replies = read_replies(ser, seconds)
    except Exception as err:
        return {
            "port": port,
            "recorder": False,
            "reply": "",
            "error": str(err),
        }

    recorder_replies = [line for line in replies if is_recorder_reply(line)]
    return {
        "port": port,
        "recorder": bool(recorder_replies),
        "reply": recorder_replies[0] if recorder_replies else " | ".join(replies),
        "error": "",
    }


def probe_recorders(seconds, quiet=False, device=None):
    ports = [find_serial_port(device)] if device else serial_ports()
    results = [probe_recorder_port(port, seconds) for port in ports]
    if quiet:
        return results

    if not results:
        print("No serial ports found.")
        return results

    for result in results:
        port = result["port"]
        if result["recorder"]:
            print("%s | recorder | %s" % (port_text(port), result["reply"]))
        elif result["error"]:
            print("%s | unavailable | %s" % (port_text(port), result["error"]))
        else:
            print("%s | no recorder response" % port_text(port))
    return results


def monitor_status(port, interval):
    print("Monitoring %s every %.1fs. Press Ctrl+C to stop monitoring." % (port, interval))
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2) as ser:
        try:
            while True:
                send_serial_line(ser, "STATUS")
                print_replies(ser, interval)
        except KeyboardInterrupt:
            print("\nMonitor stopped. Recorder keeps running unless you send STOP.")


def local_time_command():
    now = dt.datetime.now().replace(microsecond=0)
    return "SET_TIME " + now.isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=("list-ports", "probe", "monitor", "set-time", "start", "stop", "status", "help", "send"),
    )
    parser.add_argument("line", nargs="?", help="Raw line to send when command is 'send'.")
    parser.add_argument("--port", default=None, help="Serial device, e.g. /dev/ttyACM0.")
    parser.add_argument("--wait", type=float, default=1.0, help="Seconds to print replies after sending.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between STATUS polls.")
    parser.add_argument("--probe-seconds", type=float, default=0.7, help="Seconds to wait for STATUS replies in probe.")
    parser.add_argument("--monitor", action="store_true", help="After 'start', print STATUS every interval.")
    parser.add_argument(
        "--no-set-time",
        action="store_true",
        help="For 'start', send START without first setting RTC from host local time.",
    )
    args = parser.parse_args()

    if args.command == "list-ports":
        list_serial_ports()
        return

    if args.command == "probe":
        probe_recorders(args.probe_seconds, device=args.port)
        return

    port = resolve_port(args.port, args.probe_seconds)

    if args.command == "monitor":
        monitor_status(port, args.interval)
        return

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

    if args.command == "start" and args.monitor:
        monitor_status(port, args.interval)


if __name__ == "__main__":
    main()
