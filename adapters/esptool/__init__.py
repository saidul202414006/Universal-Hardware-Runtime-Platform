"""
esptool Adapter — handles ESP32/ESP8266 flashing and identification.
"""

from __future__ import annotations

import json
import re
from typing import Any

from packages.core.adapter_base import AdapterError, BaseAdapter
from packages.logger import get_logger
from packages.types.base import ErrorDetail
from packages.types.device import Device, DeviceInfo

logger = get_logger("adapter_esptool")


class ESPToolAdapter(BaseAdapter):
    """Adapter for Espressif's esptool.py."""

    async def get_tool_version(self) -> str:
        code, out = await self._run_subprocess([self._tool_path, "version"])
        # Expected output: "esptool.py v4.6.2" or similar
        match = re.search(r"v(\d+\.\d+\.\d+)", out)
        if match:
            return match.group(1)
        return "unknown"

    async def identify(self, device: Device) -> DeviceInfo:
        """Run `esptool.py flash_id` to get detailed chip info."""
        args = [
            self._tool_path,
            "--port",
            device.port,
            "flash_id",
        ]

        try:
            code, out = await self._run_subprocess(args)
        except AdapterError as exc:
            # Re-raise with better context
            exc.detail.message = f"Failed to identify device on {device.port}"
            raise

        info = DeviceInfo(raw_info={"esptool_output": out})

        # Parse esptool output
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Detecting chip type..."):
                info.chip_type = line.replace("Detecting chip type...", "").strip()
            elif line.startswith("Chip is "):
                if not info.chip_type:
                    info.chip_type = line.replace("Chip is ", "").split(" (")[0].strip()
            elif line.startswith("MAC: "):
                info.mac_address = line.replace("MAC: ", "").strip()
            elif line.startswith("Crystal is "):
                try:
                    info.crystal_frequency_mhz = float(
                        line.replace("Crystal is ", "").replace("MHz", "").strip()
                    )
                except ValueError:
                    pass
            elif line.startswith("Detected flash size: "):
                size_str = line.replace("Detected flash size: ", "").strip()
                if size_str == "4MB":
                    info.flash_size_bytes = 4 * 1024 * 1024
                elif size_str == "8MB":
                    info.flash_size_bytes = 8 * 1024 * 1024
                elif size_str == "16MB":
                    info.flash_size_bytes = 16 * 1024 * 1024
            elif line.startswith("Features: "):
                features_str = line.replace("Features: ", "").strip()
                info.features = [f.strip() for f in features_str.split(",")]

        return info

    async def build(self, project_path: str, target_board: str, task: Any) -> bool:
        """
        esptool does not build firmware (that's idf.py or platformio).
        This adapter only handles flashing.
        """
        raise AdapterError(
            ErrorDetail(
                error_code="UNSUPPORTED_OPERATION",
                message="esptool cannot build firmware",
                root_cause="Tool limitation",
                suggested_fix="Use the Arduino or PlatformIO adapter to build ESP32 firmware",
            )
        )

    async def flash(self, device: Device, firmware_path: str, task: Any, **kwargs: Any) -> bool:
        """Run `esptool.py write_flash`."""
        flash_address = kwargs.get("flash_address", "0x0")
        verify = kwargs.get("verify_after_flash", True)

        args = [
            self._tool_path,
            "--port",
            device.port,
            "--baud",
            str(kwargs.get("baud_rate", 460800)),
            "write_flash",
            "-z",  # compress
        ]

        if verify:
            args.append("--verify")

        args.extend([flash_address, firmware_path])

        try:
            task.update_progress(10.0, "Connecting to device...")
            code, out = await self._run_subprocess(args, task=task)
            task.update_progress(100.0, "Flash complete")
            return True
        except AdapterError:
            raise

    async def erase(self, device: Device, task: Any) -> bool:
        """Run `esptool.py erase_flash`."""
        args = [
            self._tool_path,
            "--port",
            device.port,
            "erase_flash",
        ]

        try:
            task.update_progress(10.0, "Connecting to device...")
            code, out = await self._run_subprocess(args, task=task)
            task.update_progress(100.0, "Erase complete")
            return True
        except AdapterError:
            raise

    def normalize_error(self, return_code: int, output: str) -> ErrorDetail:
        """Parse esptool errors into standard format."""
        # Check for common esptool errors
        if "A fatal error occurred: Could not open" in output:
            return ErrorDetail(
                error_code="PORT_OPEN_FAILED",
                message="Could not open serial port",
                root_cause="Permission denied or port in use",
                suggested_fix="Close serial monitor, or check user permissions (dialout group)",
                retryable=True,
                tool_output=output,
            )
        if "Failed to connect to Espressif device" in output:
            return ErrorDetail(
                error_code="CONNECTION_FAILED",
                message="Failed to connect to ESP32",
                root_cause="Device not responding to sync",
                suggested_fix="Hold BOOT button while flashing, or check USB cable",
                retryable=True,
                tool_output=output,
            )
        if "Flash verify failed" in output:
            return ErrorDetail(
                error_code="VERIFY_FAILED",
                message="Flash verification failed",
                root_cause="Written data does not match file",
                suggested_fix="Try a lower baud rate or check power supply",
                retryable=True,
                tool_output=output,
            )

        return ErrorDetail(
            error_code=f"TOOL_ERROR_{return_code}",
            message=f"esptool failed with code {return_code}",
            root_cause="Unknown tool error",
            suggested_fix="Check the tool output for details",
            retryable=False,
            tool_output=output,
        )

    def parse_progress(self, line: str, task: Any) -> None:
        """Parse esptool write_flash progress."""
        # Example line: "Writing at 0x00010000... (16 %)"
        match = re.search(r"\(\s*(\d+)\s*%\)", line)
        if match:
            percent = float(match.group(1))
            # Map 0-100% of writing to 10-90% of overall task
            task.update_progress(10.0 + (percent * 0.8), f"Writing flash: {percent}%")
