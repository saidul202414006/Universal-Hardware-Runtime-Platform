"""
Adapter Base Class — the interface all hardware adapters must implement.

Adapters are the ONLY components allowed to touch hardware tools
(esptool, arduino-cli, openocd). They bridge the Runtime Core to the tools.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from abc import ABC, abstractmethod
from typing import Any

from packages.logger import get_logger
from packages.types.base import ErrorDetail
from packages.types.device import Device, DeviceInfo
from packages.types.plugin import AdapterConfig

logger = get_logger("adapter_base")


class AdapterError(Exception):
    """Exception raised by adapters, containing a normalized ErrorDetail."""

    def __init__(self, detail: ErrorDetail) -> None:
        super().__init__(detail.message)
        self.detail = detail


class BaseAdapter(ABC):
    """
    Abstract interface for all hardware tool adapters.
    """

    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        self._tool_path = config.tool_path

    async def initialize(self) -> bool:
        """
        Verify the underlying tool is installed and usable.
        Returns True if ready, False otherwise.
        """
        if not self._tool_path:
            self._tool_path = shutil.which(self.config.tool_name) or ""

        if not self._tool_path or not os.path.exists(self._tool_path):
            logger.warning(f"Adapter tool '{self.config.tool_name}' not found in PATH")
            return False

        try:
            self.config.tool_version = await self.get_tool_version()
            self.config.enabled = True
            self.config.healthy = True
            logger.info(
                f"Adapter '{self.config.name}' ready",
                tool=self._tool_path,
                version=self.config.tool_version,
            )
            return True
        except Exception as exc:
            logger.error(
                f"Adapter '{self.config.name}' failed to initialize",
                error=str(exc),
            )
            self.config.healthy = False
            return False

    async def _run_subprocess(
        self,
        args: list[str],
        cwd: str | None = None,
        timeout: float | None = None,
        task: Any = None,  # Task instance for progress/logs
    ) -> tuple[int, str]:
        """
        Run a subprocess asynchronously and capture output.
        If a task is provided, logs are appended to it in real-time.
        """
        cmd_str = " ".join(args)
        logger.debug(f"Running command: {cmd_str}")

        if task:
            task.append_log(f"$ {cmd_str}")

        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
        except OSError as exc:
            raise AdapterError(
                ErrorDetail(
                    error_code="TOOL_EXECUTION_FAILED",
                    message=f"Failed to execute {args[0]}: {exc}",
                    root_cause=type(exc).__name__,
                    suggested_fix="Check if the tool is installed and executable",
                )
            )

        output_lines = []
        timeout = timeout or self.config.timeout_seconds

        try:

            async def read_stream() -> None:
                assert process.stdout is not None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    line_str = line.decode("utf-8", errors="replace").rstrip()
                    output_lines.append(line_str)
                    if task:
                        task.append_log(line_str)
                        # Optionally parse progress here if the adapter provides a hook
                        self.parse_progress(line_str, task)

            await asyncio.wait_for(read_stream(), timeout=timeout)
            await process.wait()

            return_code = process.returncode or 0
            full_output = "\n".join(output_lines)

            if return_code != 0:
                raise AdapterError(self.normalize_error(return_code, full_output))

            return return_code, full_output

        except TimeoutError:
            try:
                process.kill()
            except OSError:
                pass
            raise AdapterError(
                ErrorDetail(
                    error_code="TOOL_TIMEOUT",
                    message=f"Command timed out after {timeout} seconds",
                    root_cause="Timeout",
                    suggested_fix="Increase timeout in adapter config or check device connection",
                    retryable=True,
                    tool_output="\n".join(output_lines),
                )
            )

    @abstractmethod
    async def get_tool_version(self) -> str:
        """Execute the tool to get its version string."""
        ...

    @abstractmethod
    async def identify(self, device: Device) -> DeviceInfo:
        """Query the device to get detailed hardware info."""
        ...

    @abstractmethod
    async def build(self, project_path: str, target_board: str, task: Any) -> bool:
        """Compile firmware for the specified board."""
        ...

    @abstractmethod
    async def flash(self, device: Device, firmware_path: str, task: Any, **kwargs: Any) -> bool:
        """Flash firmware to the device."""
        ...

    @abstractmethod
    async def erase(self, device: Device, task: Any) -> bool:
        """Erase all flash memory on the device."""
        ...

    @abstractmethod
    def normalize_error(self, return_code: int, output: str) -> ErrorDetail:
        """Convert a tool-specific error output into a standardized ErrorDetail."""
        ...

    def parse_progress(self, line: str, task: Any) -> None:
        """
        Optional hook to parse tool output and update task progress.
        Override in specific adapters.
        """
        pass
