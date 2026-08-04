"""
USB enumeration — cross-platform USB device detection.

Combines pyserial's list_ports (works everywhere) with
optional pyusb for additional VID/PID details.

The VID/PID database maps USB identifiers to hardware board types.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.logger import get_logger

logger = get_logger("usb_enumerator")


@dataclass
class USBDevice:
    """Raw USB device info before board identification."""
    port: str
    vid: str | None = None           # e.g. "0x10C4"
    pid: str | None = None           # e.g. "0xEA60"
    serial_number: str | None = None
    manufacturer: str | None = None
    product: str | None = None
    description: str | None = None
    # Populated after VID/PID lookup
    vendor_name: str | None = None
    board_hint: str | None = None    # e.g. "esp32", "arduino-uno"
    chip_name: str | None = None


class VIDPIDDatabase:
    """
    USB VID/PID lookup database.
    Maps VID → vendor info → PID → board info.
    """

    def __init__(self, db_path: str = "configs/vid_pid_db.json") -> None:
        self._db: dict[str, Any] = {}
        self._load(db_path)

    def _load(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw_db = json.load(f)
            # Normalize all keys in the DB on load
            self._db = {}
            for k, v in raw_db.items():
                if k.startswith("_"):
                    self._db[k] = v
                    continue
                k_norm = _normalize_hex(k)
                if "products" in v:
                    v["products"] = {_normalize_hex(pk): pv for pk, pv in v["products"].items()}
                self._db[k_norm] = v
            logger.debug("VID/PID database loaded", path=path, entries=len(self._db))
        except FileNotFoundError:
            logger.warning("VID/PID database not found", path=path)
        except Exception as exc:
            logger.error("Failed to load VID/PID database", path=path, error=str(exc))

    def lookup(self, vid: str | None, pid: str | None) -> dict[str, Any]:
        """
        Look up device info from VID/PID.

        Args:
            vid: USB Vendor ID as hex string (e.g., "0x10C4" or "10C4")
            pid: USB Product ID as hex string

        Returns:
            Dict with vendor_name, board_hint, chip keys, or empty dict if not found.
        """
        if not vid:
            return {}

        vid_norm = _normalize_hex(vid)
        pid_norm = _normalize_hex(pid) if pid else None

        vendor_data = self._db.get(vid_norm, {})
        if not vendor_data:
            return {}

        result = {"vendor_name": vendor_data.get("vendor", "")}

        if pid_norm and "products" in vendor_data:
            product_data = vendor_data["products"].get(pid_norm, {})
            if product_data:
                result.update({
                    "board_hint": product_data.get("board_hint"),
                    "chip_name": product_data.get("chip"),
                    "product_name": product_data.get("name"),
                })

        return result


def _normalize_hex(value: str) -> str:
    """Normalize hex string to '0xXXXX' lowercase format."""
    value = value.strip()
    if value.startswith(("0x", "0X")):
        num = int(value, 16)
    else:
        try:
            num = int(value, 16)
        except ValueError:
            return value.lower()
    return f"0x{num:04x}"


class USBEnumerator:
    """
    Cross-platform USB serial device enumerator.

    Uses pyserial's list_ports as the primary source (works on all platforms).
    Optionally enriches with pyusb for additional details.
    """

    def __init__(self, vid_pid_db: VIDPIDDatabase | None = None) -> None:
        self._db = vid_pid_db or VIDPIDDatabase()
        self._platform = platform.system()

    def enumerate(self) -> list[USBDevice]:
        """
        Enumerate all connected USB serial devices.

        Returns:
            List of USBDevice objects with port, VID/PID, and board hint.
        """
        devices: list[USBDevice] = []

        try:
            import serial.tools.list_ports
            ports = serial.tools.list_ports.comports()
        except Exception as exc:
            logger.error("Failed to enumerate serial ports", error=str(exc))
            return []

        for port_info in ports:
            vid = _int_to_hex(port_info.vid) if port_info.vid else None
            pid = _int_to_hex(port_info.pid) if port_info.pid else None

            device = USBDevice(
                port=port_info.device,
                vid=vid,
                pid=pid,
                serial_number=port_info.serial_number,
                manufacturer=port_info.manufacturer,
                product=port_info.product,
                description=port_info.description,
            )

            # Enrich with VID/PID database
            if vid:
                lookup = self._db.lookup(vid, pid)
                device.vendor_name = lookup.get("vendor_name")
                device.board_hint = lookup.get("board_hint")
                device.chip_name = lookup.get("chip_name")
                # Use product name from DB if not available from port info
                if not device.product and lookup.get("product_name"):
                    device.product = lookup["product_name"]

            devices.append(device)
            logger.debug(
                "USB device found",
                port=device.port,
                vid=vid,
                pid=pid,
                board_hint=device.board_hint,
                manufacturer=device.manufacturer,
            )

        logger.info("USB enumeration complete", device_count=len(devices))
        return devices

    def enumerate_by_vid_pid(self, vid: str, pid: str | None = None) -> list[USBDevice]:
        """Enumerate only devices matching a specific VID (and optionally PID)."""
        all_devices = self.enumerate()
        vid_norm = _normalize_hex(vid)
        return [
            d for d in all_devices
            if d.vid and _normalize_hex(d.vid) == vid_norm
            and (pid is None or (d.pid and _normalize_hex(d.pid) == _normalize_hex(pid)))
        ]


def _int_to_hex(value: int) -> str:
    """Convert integer VID/PID to '0xXXXX' format."""
    return f"0x{value:04x}"
