#!/usr/bin/env python3
r"""Sync main.py to the OpenMV board's USB drive and reboot it.

Compares the local main.py with the copy on the board's mounted mass-storage
drive. If they differ, copies the local file over, resets the board so it boots
the new code, and verifies the bytes landed.

    uv run sync_board.py              # check, and sync if needed
    uv run sync_board.py --check      # report only, never write
    uv run sync_board.py --force      # copy even if already identical
    uv run sync_board.py --mount E:\              # if auto-detect fails
    uv run sync_board.py --no-reset   # copy but leave resetting to you

The board only loads main.py at boot, so a sync always resets the board after
copying. Because the tool resets after every copy, the running code matches the
persisted file once a sync reports success.
"""

import argparse
import hashlib
import os
import sys

import host_control as hc


HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SOURCE = os.path.join(HERE, "main.py")
DEFAULT_MARKER = ".openmv_disk"


def read_bytes(path):
    try:
        with open(path, "rb") as handle:
            return handle.read()
    except FileNotFoundError:
        return None


def describe(data):
    if data is None:
        return "absent"
    return "%s (%d bytes)" % (hashlib.sha256(data).hexdigest()[:8], len(data))


def drive_candidates():
    # Roots that could be a mounted USB drive. On Windows these are drive letters;
    # on Linux they are mount points under the usual removable-media locations.
    if sys.platform.startswith("win"):
        return windows_drive_roots()
    return linux_mount_roots()


def windows_drive_roots():
    import ctypes
    import string

    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    drive_removable, drive_fixed = 2, 3  # skip remote/cdrom to avoid stalls
    roots = []
    for index, letter in enumerate(string.ascii_uppercase):
        if not (bitmask >> index) & 1:
            continue
        root = "%s:\\" % letter
        if kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) in (drive_removable, drive_fixed):
            roots.append(root)
    return roots


def linux_mount_roots():
    roots = []
    user = os.environ.get("USER") or ""
    for base in ("/media/" + user, "/run/media/" + user, "/media", "/mnt"):
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                full = os.path.join(base, name)
                if os.path.isdir(full):
                    roots.append(full)
    return roots


def find_mount(explicit, marker):
    # The OpenMV cam's mass-storage drive carries a marker file (.openmv_disk) at
    # its root -- the same signal the OpenMV IDE uses. The volume label is not
    # reliable (NO NAME / USB DRIVE / OPENMV vary), so we detect by the marker.
    if explicit:
        if not os.path.isdir(explicit):
            raise SystemExit("Mount path is not a directory: %s" % explicit)
        if not os.path.exists(os.path.join(explicit, marker)):
            print(
                "Warning: %s has no %s marker; using it anyway." % (explicit, marker),
                file=sys.stderr,
            )
        return explicit

    candidates = drive_candidates()
    matches = [root for root in candidates if os.path.exists(os.path.join(root, marker))]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(
            "Multiple drives contain %s: %s. Pass --mount." % (marker, ", ".join(matches))
        )

    message = ["Could not find an OpenMV drive (no %s on any mounted drive)." % marker]
    if candidates:
        message.append("Drives checked (pass one with --mount):")
        message.extend("  " + path for path in candidates)
    else:
        message.append("No mounted drives found. Pass --mount.")
    raise SystemExit("\n".join(message))


def find_board(explicit, probe_seconds):
    """Locate the board's serial port quietly.

    Returns (port_device_or_None, is_recording). With --port we probe only that
    device; otherwise we use the sole responding recorder. We do not fall back to
    dumping every serial port -- a missing board just means "reset manually".
    """
    results = hc.probe_recorders(
        probe_seconds, quiet=True, device=explicit, progress=(explicit is None)
    )
    if explicit:
        recording = any("state=recording" in (r.get("reply") or "") for r in results)
        return explicit, recording

    recorders = [r for r in results if r["recorder"]]
    if len(recorders) == 1:
        result = recorders[0]
        recording = "state=recording" in (result.get("reply") or "")
        return result["port"].device, recording
    return None, False


def main():
    parser = argparse.ArgumentParser(description="Sync main.py to the OpenMV board.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Local file to deploy (default: main.py next to this script).")
    parser.add_argument("--mount", default=None, help="OpenMV drive mount point / letter. Auto-detected via the marker file if omitted.")
    parser.add_argument("--marker", default=DEFAULT_MARKER, help="Marker file that identifies the OpenMV drive (default: .openmv_disk).")
    parser.add_argument("--check", action="store_true", help="Only report whether a sync is needed; do not write.")
    parser.add_argument("--force", action="store_true", help="Copy even if the board already matches.")
    parser.add_argument("--no-reset", action="store_true", help="Copy without resetting the board (reset it yourself).")
    parser.add_argument("--port", default=None, help="Serial device for the safety check and reset.")
    parser.add_argument("--probe-seconds", type=float, default=0.7, help="Seconds to wait for STATUS replies.")
    parser.add_argument("--reboot-timeout", type=float, default=8.0, help="Seconds to wait for the board to re-enumerate after reset.")
    args = parser.parse_args()

    local = read_bytes(args.source)
    if local is None:
        raise SystemExit("Local source not found: %s" % args.source)

    mount = find_mount(args.mount, args.marker)
    target = os.path.join(mount, "main.py")
    onboard = read_bytes(target)

    print("local: %s  %s" % (args.source, describe(local)))
    print("board: %s  %s" % (target, describe(onboard)))

    if onboard == local and not args.force:
        print("OK: board main.py is up to date. Nothing to do.")
        return

    if onboard == local:
        print("SYNC: files are identical, but --force was given.")
    elif onboard is None:
        print("SYNC NEEDED: board has no main.py.")
    else:
        print("SYNC NEEDED: board main.py differs from local.")

    if args.check:
        print("(--check) Not writing. Run without --check to sync.")
        sys.exit(1)

    # Refuse to write to the drive while the board is recording to that filesystem.
    port, recording = find_board(args.port, args.probe_seconds)
    if recording:
        raise SystemExit("Board is recording. Send 'stop' before syncing.")

    print("Copying %s -> %s" % (args.source, target))
    with open(target, "wb") as handle:
        handle.write(local)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.sync()
    except Exception:
        pass

    if read_bytes(target) != local:
        raise SystemExit("Verify failed: bytes on the board do not match local after copy.")
    print("OK: copied and verified %d bytes." % len(local))

    if args.no_reset:
        print("Skipping reset (--no-reset). Reset the board to load the new code.")
        return

    if not port:
        print("No serial port found to reset the board. Reset it manually to load the new code.")
        return

    print("Resetting board to load new code...")
    device = hc.reset_board(port, args.probe_seconds, args.reboot_timeout, graceful=True)
    if not device:
        raise SystemExit("Could not confirm the board came back. Check it and run 'host_control.py status'.")

    # Confirm the rebooted board is answering by reading STATUS once.
    try:
        info = hc.find_serial_port(device)
        status = hc.probe_recorder_port(info, args.probe_seconds)
        print("Confirmed: board is up on %s -> %s" % (device, status.get("reply") or "(no STATUS reply)"))
    except SystemExit:
        print("Board came back on %s but STATUS could not be read." % device)


if __name__ == "__main__":
    main()
