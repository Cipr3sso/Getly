"""
Filesystem path resolution for Getly.
Handles frozen executable environments (PyInstaller bundle vs local development),
separating read-only application files (e.g. C:\\Program Files\\Getly) from
user-writable application data (%LOCALAPPDATA%\\Getly).
"""

import os
from pathlib import Path
import sys


def is_frozen() -> bool:
    """Returns True if running inside a compiled PyInstaller executable."""
    return getattr(sys, "frozen", False)


def get_bundle_dir() -> Path:
    """
    Returns the read-only application bundle directory.
    When frozen, this points to sys._MEIPASS (onefile) or sys.executable directory (onedir).
    In development, points to the repository root directory.
    """
    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def get_app_data_dir() -> Path:
    """
    Returns the user-writable data directory (%LOCALAPPDATA%\\Getly on Windows).
    Ensures the directory exists before returning.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        base_dir = Path(local_app_data)
    else:
        base_dir = Path.home() / "AppData" / "Local"

    app_data_dir = base_dir / "Getly"
    app_data_dir.mkdir(parents=True, exist_ok=True)
    return app_data_dir


def get_default_download_dir() -> Path:
    """Returns the default download directory (~/Downloads/Getly)."""
    download_dir = Path.home() / "Downloads" / "Getly"
    download_dir.mkdir(parents=True, exist_ok=True)
    return download_dir


def get_database_path(download_dir: Path = None) -> Path:
    """
    Returns the SQLite database path.
    When frozen in Program Files, returns %LOCALAPPDATA%\\Getly\\downloads.db.
    In local development, uses download_dir / .state.db or downloads.db.
    """
    if is_frozen():
        return get_app_data_dir() / "downloads.db"
    
    target_dir = download_dir or get_default_download_dir()
    return target_dir / ".state.db"


def get_extensions_storage_dir() -> Path:
    """
    Returns the storage directory for downloaded Gopeed extensions.
    When frozen, stores inside %LOCALAPPDATA%\\Getly\\extensions.
    In development, stores in <repo>/data/extensions.
    """
    if is_frozen():
        ext_dir = get_app_data_dir() / "extensions"
    else:
        ext_dir = get_bundle_dir() / "data" / "extensions"

    ext_dir.mkdir(parents=True, exist_ok=True)
    return ext_dir
