#!/usr/bin/env python3
"""Free a TCP port by stopping whatever is listening on it.

"How do I restart the server?" usually means "port 7100 is still in use".
This asks the OS who owns the port, sends SIGTERM, waits for a clean exit,
and escalates to SIGKILL only if the process refuses to die.

    python killport.py              # free port 7100
    python killport.py 8000         # any port
    python killport.py 7100 -n      # dry run: show the owner, change nothing
    python killport.py 7100 -k      # skip the graceful phase (SIGKILL now)
    python killport.py 7100 -t 15   # wait up to 15s for a graceful exit

No third-party dependencies. macOS/Linux use lsof (ss as a fallback when
lsof is missing); Windows uses netstat + taskkill.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
import time

DEFAULT_PORT = 7100
IS_WINDOWS = sys.platform.startswith("win")


# ---------------------------------------------------------------- discovery

def _shell(cmd: list[str]) -> str:
    """Run a command, return stdout, never raise."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _command_line(pid: int) -> str:
    """Best-effort argv for a pid. Empty string when it cannot be read."""
    if IS_WINDOWS:
        out = _shell([
            "powershell", "-NoProfile", "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
        ])
        return out.strip()
    if os.path.exists(f"/proc/{pid}/cmdline"):  # Linux
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read().replace(b"\x00", b" ").strip()
            return raw.decode("utf-8", "replace")
        except OSError:
            pass
    out = _shell(["ps", "-o", "command=", "-p", str(pid)])
    return out.strip()


def listeners(port: int) -> dict[int, str]:
    """Map pid -> process name for every socket listening on ``port``."""
    found: dict[int, str] = {}

    if IS_WINDOWS:
        for line in _shell(["netstat", "-ano"]).splitlines():
            parts = line.split()
            # Proto  Local Address  Foreign Address  State  PID
            if len(parts) < 5 or parts[3].upper() != "LISTENING":
                continue
            if not parts[1].endswith(f":{port}"):
                continue
            if not parts[4].isdigit():
                continue
            found[int(parts[4])] = "listener"
        return found

    out = _shell(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"])
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            found[int(parts[1])] = parts[0]
        except ValueError:
            continue

    if not found:  # lsof absent (minimal containers) — try ss
        out = _shell(["ss", "-ltnpH", f"sport = :{port}"])
        for pid in re.findall(r"pid=(\d+)", out):
            found[int(pid)] = "listener"

    return found


# ------------------------------------------------------------------- killing

def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    return True


def stop(pid: int, timeout: float, force: bool) -> str:
    """Stop one pid. Returns 'stopped', 'gone', 'denied' or 'forced'."""
    if not _alive(pid):
        return "gone"

    if IS_WINDOWS:
        args = ["taskkill", "/PID", str(pid)]
        args.append("/F" if force else "/T")
        _shell(args)
        for _ in range(int(max(timeout, 0) / 0.2)):
            if not _alive(pid):
                return "stopped"
            time.sleep(0.2)
        _shell(["taskkill", "/PID", str(pid), "/F"])
        return "forced" if not _alive(pid) else "denied"

    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return "gone"
    except PermissionError:
        return "denied"

    if force:
        time.sleep(0.3)
        return "forced" if not _alive(pid) else "denied"

    deadline = time.time() + max(timeout, 0)
    while time.time() < deadline:
        if not _alive(pid):
            return "stopped"
        time.sleep(0.1)

    print(f"  pid {pid} ignored SIGTERM for {timeout:g}s — sending SIGKILL")
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return "stopped"
    except PermissionError:
        return "denied"
    time.sleep(0.3)
    return "forced" if not _alive(pid) else "denied"


# ---------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="killport.py",
        description="Stop the process listening on a TCP port.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n"
               "  python killport.py            # free 7100\n"
               "  python killport.py 8000 -n    # just show who owns 8000\n",
    )
    ap.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT,
                    help=f"TCP port to free (default: {DEFAULT_PORT})")
    ap.add_argument("-n", "--dry-run", action="store_true",
                    help="list the listener(s) without stopping anything")
    ap.add_argument("-k", "--force", action="store_true",
                    help="skip the graceful wait, send SIGKILL straight away")
    ap.add_argument("-t", "--timeout", type=float, default=5.0,
                    help="seconds to wait for a clean exit before SIGKILL (default: 5)")
    args = ap.parse_args(argv)

    port = args.port
    if not 1 <= port <= 65535:
        ap.error(f"port must be 1-65535, got {port}")

    owners = listeners(port)
    if not owners:
        print(f"Port {port} is free — nothing to stop.")
        return 0

    for pid, name in owners.items():
        cmd = _command_line(pid)
        print(f"Port {port} is held by pid {pid} ({name})")
        if cmd:
            print(f"  {cmd[:160]}")

    if args.dry_run:
        print("Dry run — nothing was stopped.")
        return 0

    me = os.getpid()
    if me in owners:
        print("Refusing to stop my own process.")
        return 1

    results = {pid: stop(pid, args.timeout, args.force) for pid in owners}

    failed = [pid for pid, r in results.items() if r in ("denied",)]
    still = listeners(port)

    for pid, result in results.items():
        msg = {
            "gone": "already gone",
            "stopped": "stopped (SIGTERM)",
            "forced": "killed (SIGKILL)",
            "denied": "permission denied",
        }[result]
        print(f"  pid {pid}: {msg}")

    if still:
        print(f"\nPort {port} is still in use by {', '.join(map(str, still))}.")
        if failed:
            print("Try again with sudo, or stop it from the terminal it was started in.")
        return 1

    print(f"\nPort {port} is now free.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
