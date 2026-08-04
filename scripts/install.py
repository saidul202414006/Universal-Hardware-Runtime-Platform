#!/usr/bin/env python3
"""
Universal Hardware Runtime — Installer Script.

Detects OS, checks dependencies, configures permissions,
and installs Python/Node.js dependencies.
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def print_step(msg: str):
    print(f"\n\033[1;36m==>\033[0m \033[1m{msg}\033[0m")


def print_success(msg: str):
    print(f"  \033[1;32m✓ {msg}\033[0m")


def print_warning(msg: str):
    print(f"  \033[1;33m! {msg}\033[0m")


def print_error(msg: str):
    print(f"  \033[1;31m✗ {msg}\033[0m")
    sys.exit(1)


def run_cmd(cmd: list[str], cwd: str = ".") -> bool:
    try:
        subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)
        return True
    except subprocess.CalledProcessError:
        return False
    except FileNotFoundError:
        return False


def check_dependencies():
    print_step("Checking System Dependencies")

    # Python
    if sys.version_info < (3, 11):
        print_error(f"Python 3.11+ is required. Found {platform.python_version()}")
    print_success(f"Python {platform.python_version()} found")

    # Node.js
    if not shutil.which("node"):
        print_error("Node.js is required to build the dashboard. Please install Node.js 18+")
    print_success("Node.js found")

    # uv
    if not shutil.which("uv"):
        print_warning("'uv' Python package manager not found. Using standard pip.")
        return "pip"
    print_success("'uv' found")
    return "uv"


def setup_python(pkg_manager: str):
    print_step("Setting up Python Virtual Environment")

    if pkg_manager == "uv":
        if not run_cmd(["uv", "venv", ".venv"]):
            print_error("Failed to create virtual environment with uv")
        if not run_cmd(
            ["uv", "pip", "install", "-e", ".[all]"], env=dict(os.environ, UV_LINK_MODE="copy")
        ):
            print_error("Failed to install Python dependencies")
    else:
        if not run_cmd([sys.executable, "-m", "venv", ".venv"]):
            print_error("Failed to create virtual environment with venv")
        pip_path = ".venv/bin/pip" if os.name != "nt" else r".venv\Scripts\pip.exe"
        if not run_cmd([pip_path, "install", "-e", ".[all]"]):
            print_error("Failed to install Python dependencies")

    print_success("Python dependencies installed")


def build_dashboard():
    print_step("Building Web Dashboard")

    dashboard_dir = Path("apps/dashboard")
    if not dashboard_dir.exists():
        print_warning("Dashboard directory not found, skipping build")
        return

    if not run_cmd(["npm", "install"], cwd=str(dashboard_dir)):
        print_error("Failed to install Node.js dependencies")

    if not run_cmd(["npm", "run", "build"], cwd=str(dashboard_dir)):
        print_error("Failed to build dashboard")

    print_success("Dashboard built successfully")


def check_hardware_tools():
    print_step("Checking Hardware Tools")

    tools = {
        "esptool": "esptool (ESP32/ESP8266)",
        "arduino-cli": "arduino-cli (Arduino)",
    }

    for cmd, desc in tools.items():
        if shutil.which(cmd):
            print_success(f"{desc} found")
        else:
            print_warning(f"{desc} not found in PATH. You may need to install it manually.")


def linux_permissions():
    if platform.system() != "Linux":
        return

    # Check if Termux
    if "com.termux" in os.environ.get("PREFIX", "") or os.path.exists("/data/data/com.termux"):
        print_step("Termux Environment Detected")
        print_warning("In Termux, use 'termux-usb' for serial access. No dialout group needed.")
        return

    print_step("Linux Permissions Check")
    import grp

    try:
        dialout = grp.getgrnam("dialout")
        if os.getlogin() not in dialout.gr_mem:
            print_warning("User is not in 'dialout' group. Serial ports may not be accessible.")
            print_warning("Fix: sudo usermod -aG dialout $USER")
        else:
            print_success("User is in 'dialout' group")
    except KeyError:
        print_warning("Group 'dialout' does not exist")


def main():
    print("\n\033[1;32m=== Universal Hardware Runtime Platform Installer ===\033[0m")

    # Ensure we're in the project root
    if not Path("pyproject.toml").exists():
        print_error("Please run this script from the project root directory")

    pkg_manager = check_dependencies()
    setup_python(pkg_manager)
    build_dashboard()
    check_hardware_tools()
    linux_permissions()

    print("\n\033[1;32mInstallation Complete!\033[0m")
    print("\nTo start the server:")
    print("  source .venv/bin/activate")
    print("  uhr-server")


if __name__ == "__main__":
    main()
