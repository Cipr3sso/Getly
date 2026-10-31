"""
Build and Packaging System for Getly.
Generates:
1. HD Brand Icons (assets/app_icon.ico, assets/app_icon.png, extension/icons/)
2. Compiled Application Directory (dist/Getly/Getly.exe)
3. Windows Setup Installer (dist/Getly_Setup.exe) via Inno Setup or native UAC bootstrapper
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from ui.icon_generator import ensure_app_icons

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"


def step_1_generate_icons():
    print("=" * 70)
    print("[1/3] GENERAZIONE ICONE ALTA DEFINIZIONE GETLY")
    print("=" * 70)
    icons = ensure_app_icons(project_root=PROJECT_ROOT, force=True)
    ico_path = icons.get("ico")
    png_path = icons.get("png")
    print(f"  -> Icona ICO generata: {ico_path} ({os.path.getsize(ico_path)} bytes)")
    print(f"  -> Icona PNG generata: {png_path} ({os.path.getsize(png_path)} bytes)")
    return ico_path


def step_2_build_pyinstaller(ico_path: Path):
    print("\n" + "=" * 70)
    print("[2/3] COMPILAZIONE APPLICAZIONE CON PYINSTALLER (--onedir)")
    print("=" * 70)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=Getly",
        "--windowed",
        "--noconfirm",
        "--clean",
        f"--icon={ico_path}",
        "--add-data=assets;assets",
        "--add-data=extension;extension",
        "--hidden-import=libtorrent",
        "--hidden-import=quickjs",
        "--hidden-import=bs4",
        "--hidden-import=pystray",
        "--hidden-import=PIL",
        "--collect-all=flet",
        "--collect-all=flet_desktop",
        "main.py",
    ]

    print("Esecuzione comando PyInstaller:\n" + " ".join(cmd) + "\n")
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller build failed with exit code {res.returncode}")

    target_exe = DIST_DIR / "Getly" / "Getly.exe"
    if not target_exe.exists():
        raise FileNotFoundError(f"Expected compiled executable not found at {target_exe}")

    print(f"\n[+] Compilazione completata con successo: {target_exe}")
    return target_exe


def find_inno_setup_compiler() -> Path | None:
    """Searches for ISCC.exe in standard Windows locations and system PATH."""
    candidates = [
        shutil.which("iscc"),
        shutil.which("ISCC"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c).resolve()
    return None


def step_3_build_installer(ico_path: Path):
    print("\n" + "=" * 70)
    print("[3/3] GENERAZIONE INSTALLER WINDOWS (Getly_Setup.exe)")
    print("=" * 70)

    iscc_path = find_inno_setup_compiler()
    setup_output = DIST_DIR / "Getly_Setup.exe"

    if iscc_path:
        print(f"Trovato compilatore Inno Setup: {iscc_path}")
        iss_script = PROJECT_ROOT / "installer.iss"
        res = subprocess.run([str(iscc_path), str(iss_script)], cwd=str(PROJECT_ROOT))
        if res.returncode == 0 and setup_output.exists():
            print(f"\n[+] Inno Setup Installer generato con successo: {setup_output} ({setup_output.stat().st_size} bytes)")
            return setup_output
        print("Avviso: Inno Setup ha restituito un errore, utilizzo del bootstrapper nativo di fallback...")

    # Fallback to native bootstrapper installer
    print("Compilazione installer standalone nativo (UAC Elevated Bootstrapper)...")
    getly_dir = DIST_DIR / "Getly"
    payload_zip = DIST_DIR / "payload.zip"

    print(f"Creazione archivio payload: {payload_zip}...")
    with zipfile.ZipFile(payload_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for file in getly_dir.rglob("*"):
            if file.is_file():
                arcname = file.relative_to(getly_dir)
                zf.write(file, arcname)

    print(f"Payload compresso generato ({payload_zip.stat().st_size} bytes)")

    cmd_bootstrapper = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=Getly_Setup",
        "--onefile",
        "--windowed",
        "--noconfirm",
        f"--icon={ico_path}",
        f"--add-data={payload_zip};.",
        "--uac-admin",
        "native_installer.py",
    ]

    res = subprocess.run(cmd_bootstrapper, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"Native installer build failed with exit code {res.returncode}")

    if not setup_output.exists():
        raise FileNotFoundError(f"Setup installer not found at {setup_output}")

    print(f"\n[+] Installer nativo standalone generato con successo: {setup_output} ({setup_output.stat().st_size} bytes)")
    return setup_output


def main():
    ico_path = step_1_generate_icons()
    step_2_build_pyinstaller(ico_path)
    setup_path = step_3_build_installer(ico_path)

    print("\n" + "=" * 70)
    print("RIEPILOGO DISTRIBUZIONE GETLY")
    print("=" * 70)
    print(f"1. Cartella Applicazione Compilata: {DIST_DIR / 'Getly'}")
    print(f"2. File Eseguibile Principale:      {DIST_DIR / 'Getly' / 'Getly.exe'}")
    print(f"3. Installer Ufficiale Windows:     {setup_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
