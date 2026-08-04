"""
Platform abstraction — Linux-specific transport implementation.
Handles udev, /dev/ttyUSB*, /dev/ttyACM*, port permissions.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path


def get_serial_port_patterns() -> list[str]:
    """Return glob patterns for serial ports on Linux."""
    return [
        "/dev/ttyUSB*",   # CP210x, CH340, FTDI
        "/dev/ttyACM*",   # Arduino CDC
        "/dev/ttyS*",     # Native serial
        "/dev/rfcomm*",   # Bluetooth serial
    ]


def check_port_permissions(port: str) -> tuple[bool, str]:
    """
    Check if the current process can access the given serial port.

    Returns:
        (can_access, message)
    """
    path = Path(port)
    if not path.exists():
        return False, f"Port {port} does not exist"

    try:
        port_stat = path.stat()
        mode = port_stat.st_mode

        # Check if we're in the 'dialout' or 'uucp' group (Linux standard)
        import grp
        dialout_gid = None
        for group_name in ("dialout", "uucp", "tty"):
            try:
                dialout_gid = grp.getgrnam(group_name).gr_gid
                break
            except KeyError:
                continue

        our_uid = os.getuid()
        our_gid = os.getgid()
        our_groups = os.getgroups()

        # Owner check
        if port_stat.st_uid == our_uid and (mode & stat.S_IRUSR) and (mode & stat.S_IWUSR):
            return True, "Access via owner permissions"

        # Group check
        if port_stat.st_gid in (our_groups + [our_gid]):
            if (mode & stat.S_IRGRP) and (mode & stat.S_IWGRP):
                return True, "Access via group permissions"

        # World-readable/writable
        if (mode & stat.S_IROTH) and (mode & stat.S_IWOTH):
            return True, "Access via world permissions"

        # Try to open it directly
        try:
            fd = os.open(port, os.O_RDWR | os.O_NONBLOCK)
            os.close(fd)
            return True, "Direct open succeeded"
        except OSError as exc:
            if dialout_gid and dialout_gid not in our_groups:
                return False, (
                    f"Permission denied on {port}. "
                    f"Add user to 'dialout' group: sudo usermod -aG dialout $USER "
                    f"(then log out and back in)"
                )
            return False, f"Permission denied on {port}: {exc}"

    except Exception as exc:
        return False, f"Cannot check permissions for {port}: {exc}"


def get_udev_rule_suggestion(vid: str, pid: str) -> str:
    """Generate a udev rule suggestion for the given VID/PID."""
    vid_num = vid.replace("0x", "").replace("0X", "").upper()
    pid_num = pid.replace("0x", "").replace("0X", "").upper()
    return (
        f'SUBSYSTEM=="tty", ATTRS{{idVendor}}=="{vid_num.lower()}", '
        f'ATTRS{{idProduct}}=="{pid_num.lower()}", MODE="0666", '
        f'GROUP="dialout", TAG+="uaccess"'
    )


def is_android_termux() -> bool:
    """Detect if running inside Android Termux environment."""
    return (
        os.path.exists("/data/data/com.termux")
        or "com.termux" in os.environ.get("PREFIX", "")
        or os.path.exists("/proc/net/xt_qtaguid")
    )
