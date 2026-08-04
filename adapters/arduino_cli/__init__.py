"""
arduino-cli Adapter — handles Arduino compilation and flashing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from packages.core.adapter_base import AdapterError, BaseAdapter
from packages.logger import get_logger
from packages.types.base import ErrorDetail
from packages.types.device import Device, DeviceInfo

logger = get_logger("adapter_arduino")


class ArduinoCLIAdapter(BaseAdapter):
    """Adapter for arduino-cli."""

    async def get_tool_version(self) -> str:
        code, out = await self._run_subprocess([self._tool_path, "version", "--format", "json"])
        try:
            data = json.loads(out)
            return data.get("VersionString", "unknown")
        except json.JSONDecodeError:
            return "unknown"

    async def identify(self, device: Device) -> DeviceInfo:
        """Run `arduino-cli board details`."""
        if not device.info.fqbn:
            # We need the FQBN to get details. Try to detect it.
            code, out = await self._run_subprocess(
                [self._tool_path, "board", "list", "--format", "json"]
            )
            try:
                boards = json.loads(out)
                for b in boards:
                    if b.get("port", {}).get("address") == device.port:
                        if b.get("matching_boards"):
                            device.info.fqbn = b["matching_boards"][0].get("fqbn")
                            break
            except Exception:
                pass

        info = DeviceInfo()
        if device.info.fqbn:
            code, out = await self._run_subprocess(
                [self._tool_path, "board", "details", "-b", device.info.fqbn, "--format", "json"]
            )
            try:
                details = json.loads(out)
                info.raw_info = {"arduino_details": details}
                info.chip_type = details.get("build_properties", {}).get("build.mcu")
            except Exception:
                pass

        return info

    async def build(self, project_path: str, target_board: str, task: Any) -> bool:
        """Run `arduino-cli compile`."""
        args = [
            self._tool_path,
            "compile",
            "--fqbn",
            target_board,
            project_path,
        ]

        if task.params.get("clean_build"):
            args.append("--clean")

        try:
            task.update_progress(10.0, "Starting compilation...")
            code, out = await self._run_subprocess(args, task=task)
            task.update_progress(100.0, "Compilation complete")

            # Extract paths to generated binaries
            project_dir = Path(project_path)
            build_dir = project_dir / "build"
            if build_dir.exists():
                task.result = {"build_dir": str(build_dir)}

            return True
        except AdapterError:
            raise

    async def flash(self, device: Device, firmware_path: str, task: Any, **kwargs: Any) -> bool:
        """Run `arduino-cli upload`."""
        # arduino-cli upload needs the project dir or the binary dir + fqbn
        fqbn = kwargs.get("target_board") or device.info.fqbn

        if not fqbn:
            raise AdapterError(
                ErrorDetail(
                    error_code="MISSING_FQBN",
                    message="Target board (FQBN) is required to flash with arduino-cli",
                    root_cause="FQBN not provided in params and not detected on device",
                    suggested_fix="Provide target_board parameter",
                )
            )

        args = [
            self._tool_path,
            "upload",
            "-p",
            device.port,
            "--fqbn",
            fqbn,
            "--input-dir",
            str(Path(firmware_path).parent),
        ]

        try:
            task.update_progress(10.0, "Connecting to device...")
            code, out = await self._run_subprocess(args, task=task)
            task.update_progress(100.0, "Upload complete")
            return True
        except AdapterError:
            raise

    async def erase(self, device: Device, task: Any) -> bool:
        raise AdapterError(
            ErrorDetail(
                error_code="UNSUPPORTED_OPERATION",
                message="arduino-cli cannot erase flash directly",
                root_cause="Tool limitation",
                suggested_fix="Flash a blank sketch instead",
            )
        )

    def normalize_error(self, return_code: int, output: str) -> ErrorDetail:
        if "programmer is not responding" in output or "not in sync" in output:
            return ErrorDetail(
                error_code="CONNECTION_FAILED",
                message="Failed to connect to bootloader",
                root_cause="Device not responding",
                suggested_fix="Check USB connection, or press reset button right before flashing",
                retryable=True,
                tool_output=output,
            )

        if "error: compilation failed" in output.lower():
            return ErrorDetail(
                error_code="COMPILE_FAILED",
                message="Compilation failed",
                root_cause="Syntax error or missing library",
                suggested_fix="Check the compiler output for syntax errors",
                retryable=False,
                tool_output=output,
            )

        return ErrorDetail(
            error_code=f"TOOL_ERROR_{return_code}",
            message=f"arduino-cli failed with code {return_code}",
            root_cause="Unknown tool error",
            suggested_fix="Check the tool output for details",
            retryable=False,
            tool_output=output,
        )

    def parse_progress(self, line: str, task: Any) -> None:
        """Parse avrdude/bossac progress from arduino-cli output."""
        if "Reading |" in line or "Writing |" in line:
            # Simple progress estimation based on hashes
            hashes = line.count("#")
            if hashes > 0:
                percent = min(100, int((hashes / 50.0) * 100))
                task.update_progress(10.0 + (percent * 0.8), "Transferring data...")
