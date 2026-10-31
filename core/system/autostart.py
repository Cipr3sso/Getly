"""
Windows Autostart configuration module for Getly.
Manages application startup on user login via the Windows registry (HKCU Run key).
"""

import logging
import os
from pathlib import Path
import sys

from .paths import is_frozen

logger = logging.getLogger(__name__)

REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Getly"
LEGACY_APP_NAME = "ResilientDownloader"


def get_autostart_command() -> str:
    """
    Generates the executable command for Windows autostart with `--minimized`.
    When running as a compiled executable (PyInstaller frozen), registers sys.executable directly.
    When running as Python script, prefers pythonw.exe to prevent a console window.
    """
    if is_frozen():
        exe_path = Path(sys.executable).resolve()
        return f'"{exe_path}" --minimized'

    py_dir = Path(sys.executable).parent
    pythonw_candidate = py_dir / "pythonw.exe"
    if pythonw_candidate.is_file():
        py_exe = pythonw_candidate.resolve()
    else:
        py_exe = Path(sys.executable).resolve()

    main_py = (Path(__file__).resolve().parent.parent.parent / "main.py").resolve()
    return f'"{py_exe}" "{main_py}" --minimized'


def is_autostart_enabled() -> bool:
    """
    Checks if Getly is registered in the Windows HKCU Run key.
    Returns True if registered with a valid command, False otherwise.
    """
    if sys.platform != "win32":
        return False

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(val and isinstance(val, str) and val.strip())
    except (FileNotFoundError, OSError):
        return False
    except Exception as exc:
        logger.debug("Failed to query autostart registry key: %s", exc)
        return False


def set_autostart(enabled: bool) -> bool:
    """
    Registers or unregisters Getly in the Windows HKCU Run key.
    Also cleans up any legacy "ResilientDownloader" registry entry.
    Returns True on success, False on error.
    """
    if sys.platform != "win32":
        return False

    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_KEY,
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ,
        ) as key:
            # Clean up legacy entry if present
            try:
                winreg.DeleteValue(key, LEGACY_APP_NAME)
            except (FileNotFoundError, OSError):
                pass

            if enabled:
                cmd = get_autostart_command()
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                logger.info("Registered autostart command in Windows registry: %s", cmd)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                    logger.info("Removed autostart entry '%s' from Windows registry", APP_NAME)
                except (FileNotFoundError, OSError):
                    pass
        return True
    except Exception as exc:
        logger.warning("Failed to update autostart setting (enabled=%s): %s", enabled, exc)
        return False
