#!/usr/bin/env python3
"""Sync main.py to the OpenMV board's USB drive and reboot it.

Compares the local main.py with the copy on the board's mounted mass-storage
drive. If they differ, copies the local file over, resets the board so it boots
the new code, and verifies the bytes landed.

    uv run sync_board.py              # check, and sync if needed
    uv run sync_board.py --check      # report only, never write
    uv run sync_board.py --force      # copy even if already identical
    uv run sync_board.py --mount /media/$USER/OPENMV
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
DEFAULT_LABEL = "OPENMV"


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


def unescape_mount(path):
    # /proc/mounts octal-escapes spaces (\040) and similar characters.
    return path.encode().decode("unicode_escape")


def mountpoint_for_device(device):
    try:
        with open("/proc/mounts", "r") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == device:
                    return unescape_mount(parts[1])
    except OSError:
        pass
    return None


def removable_mount_candidates():
    candidates = []
    user = os.environ.get("USER") or ""
    for base in ("/media/" + user, "/run/media/" + user, "/media", "/mnt"):
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                full = os.path.join(base, name)
                if os.path.isdir(full):
                    candidates.append(full)
    return candidates


def find_mount(explicit, label):
    if explicit:
        if not os.path.isdir(explicit):
            raise SystemExit("Mount path is not a directory: %s" % explicit)
        return explicit

    # Resolve by filesystem label: /dev/disk/by-label/OPENMV -> /dev/sdb1, then
    # look that device up in /proc/mounts to get its mount point.
    link = "/dev/disk/by-label/%s" % label
    if os.path.exists(link):
        device = os.path.realpath(link)
        mount = mountpoint_for_device(device)
        if mount:
            return mount
        raise SystemExit(
            "Drive labeled %r (%s) is not mounted. Mount it or pass --mount."
            % (label, device)
        )

    message = ["Could not find a drive labeled %r." % label]
    candidates = removable_mount_candidates()
    if candidates:
        message.append("Candidate mounts (pass one with --mount):")
        message.extend("  " + path for path in candidates)
    else:
        message.append("No removable drives under /media or /run/media. Pass --mount.")
    raise SystemExit("\n".join(message))


def find_board(explicit, probe_seconds):
    """Locate the board's serial port quietly.

    Returns (port_device_or_None, is_recording). With --port we probe only that
    device; otherwise we use the sole responding recorder. We do not fall back to
    dumping every serial port -- a missing board just means "reset manually".
    """
    results = hc.probe_recorders(probe_seconds, quiet=True, device=explicit)
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
    parser.add_argument("--mount", default=None, help="OpenMV drive mount point. Auto-detected by label if omitted.")
    parser.add_argument("--label", default=DEFAULT_LABEL, help="Filesystem label of the OpenMV drive (default: OPENMV).")
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

    mount = find_mount(args.mount, args.label)
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
    hc.reset_board(port, args.probe_seconds, args.reboot_timeout, graceful=True)


if __name__ == "__main__":
    main()
