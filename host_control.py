#!/usr/bin/env python3
"""Send control commands to an OpenMV recorder over USB serial.

Requires pyserial on the host:
    python3 -m pip install pyserial
"""

import argparse
import datetime as dt
import struct
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("Missing pyserial. Install with: python3 -m pip install pyserial", file=sys.stderr)
    raise


RECORDER_STATUS_PREFIXES = ("state=idle", "state=recording")
RECORDER_COMMANDS = {"START", "STOP", "STATUS", "SHUTDOWN", "HELP"}

# OpenMV USB debug protocol (same mechanism the OpenMV IDE uses to reset a board).
# The firmware services this in its USB interrupt handler, so it reboots even
# when main.py is hung or has exited to the REPL. The SYS_RESET opcode differs by
# firmware generation, so we try both and keep whichever brings the board back:
# 0x0C = legacy firmware, 0x10 = newer openmv-python protocol.
USBDBG_CMD = 48
USBDBG_SYS_RESET_OPCODES = (0x0C, 0x10)


def recorder_not_running_message() -> str:
    return (
        "Recorder did not reply; the board echoed the command instead. "
        "That usually means main.py is not running or the board is in the MicroPython REPL. "
        "Try 'hard-reset' to reboot it (IDE-style), or reset/power-cycle the board by hand, "
        "then run 'probe' until it reports 'recorder'."
    )


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
        replies = print_replies(ser, wait)
        if replies_indicate_echo(replies, sent_lines=lines):
            print(recorder_not_running_message(), file=sys.stderr)


def send_serial_line(ser, line):
    ser.write((line + "\n").encode("ascii"))
    ser.flush()


def print_replies(ser, seconds):
    lines = read_replies(ser, seconds)
    for line in lines:
        print(line)
    return lines


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


def is_echo_reply(line, sent_lines=None):
    text = line.strip()
    if not text:
        return False
    if sent_lines and text in sent_lines:
        return True
    if text in RECORDER_COMMANDS:
        return True
    if text.startswith("SET_TIME "):
        return True
    return False


def replies_indicate_echo(replies, sent_lines=None):
    return bool(replies) and any(is_echo_reply(line, sent_lines=sent_lines) for line in replies)


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
    echo_replies = [line for line in replies if is_echo_reply(line, sent_lines=["STATUS"])]
    return {
        "port": port,
        "recorder": bool(recorder_replies),
        "reply": recorder_replies[0] if recorder_replies else " | ".join(replies),
        "echoed": bool(echo_replies),
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
        elif result.get("echoed"):
            print("%s | command echo; recorder not running | %s" % (port_text(port), result["reply"]))
        elif result["error"]:
            print("%s | unavailable | %s" % (port_text(port), result["error"]))
        else:
            print("%s | no recorder response" % port_text(port))
    return results


def monitor_status(port, interval):
    print("Monitoring %s every %.1fs. Press Ctrl+C to stop monitoring." % (port, interval))
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2) as ser:
        echo_count = 0
        try:
            while True:
                send_serial_line(ser, "STATUS")
                replies = print_replies(ser, interval)
                if any(is_recorder_reply(line) for line in replies):
                    echo_count = 0
                elif replies_indicate_echo(replies, sent_lines=["STATUS"]):
                    echo_count += 1
                    if echo_count >= 3:
                        print(recorder_not_running_message(), file=sys.stderr)
                        return
        except KeyboardInterrupt:
            print("\nMonitor stopped. Recorder keeps running unless you send STOP.")


def local_time_command():
    now = dt.datetime.now().replace(microsecond=0)
    return "SET_TIME " + now.isoformat()


def usbdbg_reset(device, opcode):
    # Replicate the OpenMV IDE reset: the firmware services this command in its
    # USB interrupt handler, so it reboots the board even when main.py is hung or
    # has exited to the REPL (i.e. when the SHUTDOWN line goes unread). Whether it
    # also works while a script is actively reading USB_VCP is firmware-specific;
    # this path is only reached after the graceful SHUTDOWN attempt fails.
    try:
        with serial.Serial(device, 115200, timeout=0.3, write_timeout=2) as ser:
            ser.write(struct.pack("<BBI", USBDBG_CMD, opcode, 0))
            ser.flush()
        return True
    except Exception as err:
        print("USBDBG reset (opcode 0x%02X) failed: %s" % (opcode, err), file=sys.stderr)
        return False


def find_port_by_identity(serial_number, vid, pid):
    # Device names (COMx, /dev/ttyACM*) are volatile across a reset, so match the
    # board by its stable USB serial number, falling back to vid:pid.
    for port in serial_ports():
        if serial_number:
            if port.serial_number == serial_number:
                return port
        elif vid is not None and pid is not None and port.vid == vid and port.pid == pid:
            return port
    return None


def wait_for_recorder(serial_number, vid, pid, probe_seconds, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = find_port_by_identity(serial_number, vid, pid)
        if port is not None:
            result = probe_recorder_port(port, probe_seconds)
            if result["recorder"]:
                return port.device
        time.sleep(0.5)
    return None


def resolve_reset_port(explicit_port, probe_seconds):
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

    # No recorder answered. The board may be hung, so fall back to the sole serial
    # port when there is no ambiguity; a hard reset still needs a target.
    ports = serial_ports()
    if not ports:
        raise SystemExit("No serial ports found.")
    if len(ports) == 1:
        print(
            "No recorder responded; assuming the only serial port is the board: %s"
            % ports[0].device,
            file=sys.stderr,
        )
        return ports[0].device

    print("No recorder responded and multiple serial ports exist:", file=sys.stderr)
    for port in ports:
        print("  " + port_text(port), file=sys.stderr)
    raise SystemExit("Use --port COMx to select the board explicitly.")


def reset_board(port_device, probe_seconds, reboot_timeout, graceful=True):
    info = find_serial_port(port_device)
    serial_number = info.serial_number
    vid, pid = info.vid, info.pid

    if graceful:
        print("Sending SHUTDOWN to %s (sync + reboot)." % port_device)
        replies = []
        try:
            with serial.Serial(port_device, 115200, timeout=0.2, write_timeout=2) as ser:
                send_serial_line(ser, "SHUTDOWN")
                replies = print_replies(ser, 1.0)
        except Exception as err:
            print("Serial error during SHUTDOWN: %s" % err, file=sys.stderr)

        if replies_indicate_echo(replies, sent_lines=["SHUTDOWN"]):
            print(
                "Recorder was not listening (command echoed); falling back to hard reset.",
                file=sys.stderr,
            )
        else:
            device = wait_for_recorder(serial_number, vid, pid, probe_seconds, reboot_timeout)
            if device:
                print("Board rebooted and is responding on %s." % device)
                return True
            print(
                "Board did not return after SHUTDOWN; falling back to hard reset.",
                file=sys.stderr,
            )

    for opcode in USBDBG_SYS_RESET_OPCODES:
        current = find_port_by_identity(serial_number, vid, pid)
        device = current.device if current else port_device
        print("Hard reset via USBDBG opcode 0x%02X on %s." % (opcode, device))
        usbdbg_reset(device, opcode)
        device = wait_for_recorder(serial_number, vid, pid, probe_seconds, reboot_timeout)
        if device:
            print("Board rebooted (opcode 0x%02X) and is responding on %s." % (opcode, device))
            return True

    print(recorder_not_running_message(), file=sys.stderr)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "list-ports",
            "probe",
            "monitor",
            "set-time",
            "start",
            "stop",
            "status",
            "shutdown",
            "hard-reset",
            "help",
            "send",
        ),
    )
    parser.add_argument("line", nargs="?", help="Raw line to send when command is 'send'.")
    parser.add_argument("--port", default=None, help="Serial device, e.g. /dev/ttyACM0.")
    parser.add_argument("--wait", type=float, default=1.0, help="Seconds to print replies after sending.")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between STATUS polls.")
    parser.add_argument("--probe-seconds", type=float, default=0.7, help="Seconds to wait for STATUS replies in probe.")
    parser.add_argument("--reboot-timeout", type=float, default=8.0, help="Seconds to wait for the board to re-enumerate after a reset.")
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

    if args.command in ("shutdown", "hard-reset"):
        port = resolve_reset_port(args.port, args.probe_seconds)
        reset_board(
            port,
            args.probe_seconds,
            args.reboot_timeout,
            graceful=(args.command == "shutdown"),
        )
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
