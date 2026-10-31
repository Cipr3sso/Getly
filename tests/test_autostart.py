import os
from pathlib import Path
import sys
from unittest.mock import MagicMock, patch
import pytest

from core.system.autostart import (
    APP_NAME,
    LEGACY_APP_NAME,
    REG_KEY,
    get_autostart_command,
    is_autostart_enabled,
    set_autostart,
)


def test_get_autostart_command_format():
    cmd = get_autostart_command()
    assert isinstance(cmd, str)
    assert "--minimized" in cmd
    # Should contain path to main.py
    assert "main.py" in cmd
    # Should have quotes protecting paths
    assert cmd.startswith('"')
    assert '" "' in cmd or '"  "' in cmd or '\" \"' in cmd


def test_get_autostart_command_prefers_pythonw(tmp_path: Path):
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("")
    fake_pythonw = tmp_path / "pythonw.exe"
    fake_pythonw.write_text("")

    with patch.object(sys, "executable", str(fake_python)):
        cmd = get_autostart_command()
        assert "pythonw.exe" in cmd


def test_get_autostart_command_fallback_python(tmp_path: Path):
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("")
    # pythonw does NOT exist in tmp_path

    with patch.object(sys, "executable", str(fake_python)):
        cmd = get_autostart_command()
        assert "python.exe" in cmd


def test_get_autostart_command_frozen(tmp_path: Path):
    fake_exe = tmp_path / "Getly.exe"
    fake_exe.write_text("")
    with patch("sys.frozen", True, create=True), patch.object(sys, "executable", str(fake_exe)):
        cmd = get_autostart_command()
        assert cmd == f'"{fake_exe.resolve()}" --minimized'
        assert "python" not in cmd
        assert "main.py" not in cmd


def test_is_autostart_enabled_non_windows():
    with patch("sys.platform", "linux"):
        assert is_autostart_enabled() is False


def test_is_autostart_enabled_true():
    mock_winreg = MagicMock()
    mock_key = MagicMock()
    mock_winreg.OpenKey.return_value.__enter__.return_value = mock_key
    mock_winreg.QueryValueEx.return_value = ('"C:\\path\\pythonw.exe" "C:\\main.py" --minimized', 1)

    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winreg": mock_winreg}):
        assert is_autostart_enabled() is True
        mock_winreg.OpenKey.assert_called_once()
        mock_winreg.QueryValueEx.assert_called_with(mock_key, APP_NAME)


def test_is_autostart_enabled_false_on_missing_key():
    mock_winreg = MagicMock()
    mock_winreg.OpenKey.side_effect = FileNotFoundError()

    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winreg": mock_winreg}):
        assert is_autostart_enabled() is False


def test_is_autostart_enabled_false_on_empty_value():
    mock_winreg = MagicMock()
    mock_key = MagicMock()
    mock_winreg.OpenKey.return_value.__enter__.return_value = mock_key
    mock_winreg.QueryValueEx.return_value = ("", 1)

    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winreg": mock_winreg}):
        assert is_autostart_enabled() is False


def test_set_autostart_enable():
    mock_winreg = MagicMock()
    mock_key = MagicMock()
    mock_winreg.OpenKey.return_value.__enter__.return_value = mock_key

    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winreg": mock_winreg}):
        success = set_autostart(True)
        assert success is True
        # Verify legacy key cleanup
        mock_winreg.DeleteValue.assert_any_call(mock_key, LEGACY_APP_NAME)
        # Verify Getly key set
        mock_winreg.SetValueEx.assert_called_once()
        args = mock_winreg.SetValueEx.call_args[0]
        assert args[0] == mock_key
        assert args[1] == APP_NAME
        assert "--minimized" in args[4]


def test_set_autostart_disable():
    mock_winreg = MagicMock()
    mock_key = MagicMock()
    mock_winreg.OpenKey.return_value.__enter__.return_value = mock_key

    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winreg": mock_winreg}):
        success = set_autostart(False)
        assert success is True
        # Verify legacy key cleanup and Getly deletion
        mock_winreg.DeleteValue.assert_any_call(mock_key, LEGACY_APP_NAME)
        mock_winreg.DeleteValue.assert_any_call(mock_key, APP_NAME)


def test_set_autostart_non_windows():
    with patch("sys.platform", "darwin"):
        assert set_autostart(True) is False
        assert set_autostart(False) is False


def test_set_autostart_handles_permission_error():
    mock_winreg = MagicMock()
    mock_winreg.OpenKey.side_effect = PermissionError("Access is denied")

    with patch("sys.platform", "win32"), patch.dict("sys.modules", {"winreg": mock_winreg}):
        assert set_autostart(True) is False
