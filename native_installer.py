"""
Getly Native Windows Setup Bootstrapper
Extracts the Getly distribution payload into C:\\Program Files\\Getly,
configures Desktop and Start Menu shortcuts, and registers with Windows Add/Remove Programs.
"""

import ctypes
import os
from pathlib import Path
import subprocess
import sys
import zipfile


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run_as_admin():
    """Relaunches the current script/executable with UAC Administrator elevation."""
    params = " ".join([f'"{arg}"' for arg in sys.argv[1:]])
    ret = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1,  # SW_SHOWNORMAL
    )
    if ret > 32:
        sys.exit(0)
    else:
        ctypes.windll.user32.MessageBoxW(
            0,
            "L'installazione di Getly richiede i privilegi di amministratore per installare in Program Files.",
            "Permessi Insufficienti",
            0x10,  # MB_ICONERROR
        )
        sys.exit(1)


def create_shortcut(target_exe: str, shortcut_path: str, icon_path: str, description: str):
    """Creates a Windows .lnk shortcut using PowerShell WScript.Shell."""
    ps_cmd = f"""
    $WshShell = New-Object -ComObject WScript.Shell
    $Shortcut = $WshShell.CreateShortcut('{shortcut_path}')
    $Shortcut.TargetPath = '{target_exe}'
    $Shortcut.WorkingDirectory = '{os.path.dirname(target_exe)}'
    $Shortcut.IconLocation = '{icon_path}'
    $Shortcut.Description = '{description}'
    $Shortcut.Save()
    """
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd], check=False)


def register_uninstaller(install_dir: Path, target_exe: Path, icon_path: Path):
    """Registers Getly in the Windows Uninstall Registry (Control Panel / Settings)."""
    try:
        import winreg

        reg_path = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Getly"
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, reg_path, 0, winreg.KEY_ALL_ACCESS) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Getly")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "1.0.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "Getly Open Source")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(icon_path))
            
            uninst_cmd = (
                f'powershell.exe -WindowStyle Hidden -Command '
                f'"Stop-Process -Name Getly -Force -ErrorAction SilentlyContinue; '
                f'Start-Sleep -Seconds 1; '
                f'Remove-Item -Path \'{install_dir}\' -Recurse -Force -ErrorAction SilentlyContinue; '
                f'Remove-Item -Path \'$([Environment]::GetFolderPath(\'CommonDesktopDirectory\'))\\Getly.lnk\' -Force -ErrorAction SilentlyContinue; '
                f'Remove-Item -Path \'$([Environment]::GetFolderPath(\'CommonPrograms\'))\\Getly.lnk\' -Force -ErrorAction SilentlyContinue; '
                f'Remove-Item -Path \'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\Getly\' -Recurse -Force -ErrorAction SilentlyContinue"'
            )
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, uninst_cmd)
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except Exception as exc:
        print(f"Warning: could not write uninstall registry key: {exc}")


def main():
    if not is_admin():
        run_as_admin()

    # Find payload zip
    meipass = getattr(sys, "_MEIPASS", None)
    search_dirs = [Path(meipass)] if meipass else []
    search_dirs.extend([
        Path(sys.executable).parent,
        Path(__file__).resolve().parent,
        Path(__file__).resolve().parent / "dist",
    ])

    payload_path = None
    for d in search_dirs:
        candidate = d / "payload.zip"
        if candidate.exists():
            payload_path = candidate
            break

    if not payload_path:
        ctypes.windll.user32.MessageBoxW(
            0,
            "File di installazione (payload.zip) non trovato.",
            "Errore di Installazione",
            0x10,
        )
        sys.exit(1)

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    install_dir = Path(program_files) / "Getly"

    # Stop any currently running Getly processes
    subprocess.run(["taskkill", "/F", "/IM", "Getly.exe"], capture_output=True, check=False)

    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(payload_path, "r") as zf:
            zf.extractall(install_dir)
    except Exception as exc:
        ctypes.windll.user32.MessageBoxW(
            0,
            f"Impossibile copiare i file in {install_dir}:\n{exc}",
            "Errore di Installazione",
            0x10,
        )
        sys.exit(1)

    target_exe = install_dir / "Getly.exe"
    icon_path = install_dir / "assets" / "app_icon.ico"
    if not icon_path.exists():
        icon_path = target_exe

    # Shortcuts
    try:
        # Desktop shortcut
        common_desktop = os.environ.get("PUBLIC", r"C:\Users\Public") + r"\Desktop"
        desktop_lnk = Path(common_desktop) / "Getly.lnk"
        create_shortcut(str(target_exe), str(desktop_lnk), str(icon_path), "Getly - Modular Download Manager")

        # Start Menu shortcut
        common_start = os.environ.get("ProgramData", r"C:\ProgramData") + r"\Microsoft\Windows\Start Menu\Programs"
        start_lnk = Path(common_start) / "Getly.lnk"
        create_shortcut(str(target_exe), str(start_lnk), str(icon_path), "Getly - Modular Download Manager")
    except Exception as exc:
        print(f"Warning creating shortcuts: {exc}")

    # Register in Windows Add/Remove programs
    register_uninstaller(install_dir, target_exe, icon_path)

    # Success dialog and launch prompt
    result = ctypes.windll.user32.MessageBoxW(
        0,
        f"Getly e' stato installato con successo in:\n{install_dir}\n\n"
        "E' stata inclusa anche la cartella dell'estensione per Chrome, Edge e Brave.\n\n"
        "Vuoi avviare Getly adesso?",
        "Installazione Completata - Getly",
        0x24,  # MB_ICONQUESTION | MB_YESNO
    )
    if result == 6:  # IDYES
        subprocess.Popen([str(target_exe)])


if __name__ == "__main__":
    main()
