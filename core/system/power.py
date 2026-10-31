"""
System Power & Auto-Shutdown Management for Getly.
Controls PC shutdown operations across Windows, macOS, and Linux.
"""

import logging
import os
import platform
import subprocess

logger = logging.getLogger(__name__)


def shutdown_computer() -> bool:
    """Executes an immediate operating system shutdown."""
    sys_name = platform.system()
    try:
        if sys_name == "Windows":
            subprocess.run(["shutdown", "/s", "/t", "0"], check=True)
            return True
        elif sys_name == "Darwin":
            subprocess.run(["osascript", "-e", 'tell app "System Events" to shut down'], check=True)
            return True
        else:
            # Linux systemd / shutdown
            subprocess.run(["systemctl", "poweroff"], check=True)
            return True
    except Exception as exc:
        logger.error("Failed to execute computer shutdown: %s", exc)
        return False


def cancel_shutdown() -> bool:
    """Cancels any pending system shutdown command if supported."""
    try:
        if platform.system() == "Windows":
            subprocess.run(["shutdown", "/a"], check=False)
            return True
    except Exception as exc:
        logger.debug("Failed to cancel system shutdown: %s", exc)
    return False


def is_shutdown_supported() -> bool:
    """Checks whether system power shutdown is supported on current OS."""
    return platform.system() in ("Windows", "Darwin", "Linux")
