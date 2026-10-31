import os
from pathlib import Path
import sys
from unittest.mock import patch
import pytest

from core.system.paths import (
    is_frozen,
    get_bundle_dir,
    get_app_data_dir,
    get_default_download_dir,
    get_database_path,
    get_extensions_storage_dir,
)


def test_is_frozen_default():
    # In test environment, sys.frozen is not set
    assert is_frozen() is False


def test_is_frozen_when_set():
    with patch("sys.frozen", True, create=True):
        assert is_frozen() is True


def test_get_bundle_dir_dev():
    bundle_dir = get_bundle_dir()
    assert bundle_dir.is_dir()
    assert (bundle_dir / "main.py").exists()


def test_get_bundle_dir_frozen(tmp_path: Path):
    fake_meipass = tmp_path / "meipass_bundle"
    fake_meipass.mkdir()
    with patch("sys.frozen", True, create=True), patch("sys._MEIPASS", str(fake_meipass), create=True):
        assert get_bundle_dir() == fake_meipass.resolve()


def test_get_app_data_dir(tmp_path: Path):
    with patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
        app_data = get_app_data_dir()
        assert app_data == tmp_path / "Getly"
        assert app_data.is_dir()


def test_get_database_path_dev(tmp_path: Path):
    db_path = get_database_path(tmp_path)
    assert db_path == tmp_path / ".state.db"


def test_get_database_path_frozen(tmp_path: Path):
    with patch("sys.frozen", True, create=True), patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
        db_path = get_database_path(Path("C:/ReadOnly/Downloads"))
        assert db_path == tmp_path / "Getly" / "downloads.db"


def test_get_extensions_storage_dir_dev():
    ext_dir = get_extensions_storage_dir()
    assert ext_dir.name == "extensions"
    assert ext_dir.exists()


def test_get_extensions_storage_dir_frozen(tmp_path: Path):
    with patch("sys.frozen", True, create=True), patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}):
        ext_dir = get_extensions_storage_dir()
        assert ext_dir == tmp_path / "Getly" / "extensions"
        assert ext_dir.is_dir()
