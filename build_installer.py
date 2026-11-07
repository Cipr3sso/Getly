"""
Build and Packaging System for Getly.
Generates:
1. HD Brand Icons (assets/app_icon.ico, assets/app_icon.png, extensions/chrome/icons/)
2. Compiled Python Headless Engine (dist/Getly/GetlyEngine/GetlyEngine.exe)
3. Compiled WinUI 3 Desktop Application (dist/Getly/Getly.exe)
4. Official Windows Setup Installer (dist/Getly_Setup.exe) via Inno Setup or native UAC bootstrapper
"""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

from core.system.icon_generator import ensure_app_icons

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
GETLY_DIST_DIR = DIST_DIR / "Getly"


def step_1_generate_icons():
    print("=" * 70)
    print("[1/4] GENERAZIONE ICONE ALTA DEFINIZIONE GETLY")
    print("=" * 70)
    icons = ensure_app_icons(project_root=PROJECT_ROOT, force=True)
    ico_path = icons.get("ico")
    png_path = icons.get("png")
    print(f"  -> Icona ICO generata: {ico_path} ({os.path.getsize(ico_path)} bytes)")
    print(f"  -> Icona PNG generata: {png_path} ({os.path.getsize(png_path)} bytes)")
    return ico_path


def step_2_build_engine(ico_path: Path):
    print("\n" + "=" * 70)
    print("[2/4] COMPILAZIONE MOTORE HEADLESS (PyInstaller --onedir)")
    print("=" * 70)

    # Clean existing engine build if any
    engine_dist = GETLY_DIST_DIR / "GetlyEngine"
    if engine_dist.exists():
        shutil.rmtree(engine_dist, ignore_errors=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=GetlyEngine",
        "--onedir",
        "--windowed",
        "--noconfirm",
        "--clean",
        f"--icon={ico_path}",
        "--add-data=core;core",
        "--add-data=config;config",
        "--hidden-import=libtorrent",
        "--hidden-import=curl_cffi",
        "--hidden-import=aiohttp",
        "--hidden-import=quickjs",
        "--hidden-import=bs4",
        "--hidden-import=cryptography",
        f"--distpath={GETLY_DIST_DIR}",
        "engine_service.py",
    ]

    print("Esecuzione comando PyInstaller per GetlyEngine:\n" + " ".join(cmd) + "\n")
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f"PyInstaller build failed with exit code {res.returncode}")

    target_exe = engine_dist / "GetlyEngine.exe"
    if not target_exe.exists():
        raise FileNotFoundError(f"Expected compiled engine executable not found at {target_exe}")

    # Ensure ffmpeg.exe is bundled with the engine for standalone media stream remuxing
    ffmpeg_host = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if not ffmpeg_host:
        candidate_paths = [
            Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        ]
        for c in candidate_paths:
            if c.exists():
                ffmpeg_host = str(c)
                break
    if ffmpeg_host and Path(ffmpeg_host).exists():
        dest_ffmpeg = engine_dist / "ffmpeg.exe"
        try:
            shutil.copy2(ffmpeg_host, dest_ffmpeg)
            print(f"  -> ffmpeg.exe incluso nel pacchetto del motore: {dest_ffmpeg}")
        except Exception as e:
            print(f"  -> Nota: Impossibile copiare ffmpeg.exe ({e})")

    print(f"\n[+] Motore compilato con successo: {target_exe}")
    return target_exe


def step_3_build_frontend(ico_path: Path):
    print("\n" + "=" * 70)
    print("[3/4] COMPILAZIONE FRONTEND WINUI 3 (.NET 8 Release x64)")
    print("=" * 70)

    dotnet_exe = shutil.which("dotnet") or r"C:\Users\carlo\.dotnet\dotnet.exe"
    if not os.path.exists(dotnet_exe):
        raise FileNotFoundError("dotnet CLI non trovato!")

    cmd = [
        dotnet_exe,
        "publish",
        "src/Getly.UI",
        "-c",
        "Release",
        "-r",
        "win-x64",
        "--self-contained",
        "true",
        "-p:PublishSingleFile=false",
        "-p:AssemblyName=Getly",
        "-o",
        str(GETLY_DIST_DIR),
    ]

    print("Esecuzione compilazione .NET WinUI 3:\n" + " ".join(cmd) + "\n")
    res = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if res.returncode != 0:
        raise RuntimeError(f".NET publish failed with exit code {res.returncode}")

    target_exe = GETLY_DIST_DIR / "Getly.exe"
    if not target_exe.exists():
        raise FileNotFoundError(f"Expected compiled WinUI 3 executable not found at {target_exe}")

    # Copy assets and extensions to distribution
    assets_dest = GETLY_DIST_DIR / "assets"
    assets_dest.mkdir(parents=True, exist_ok=True)
    if (PROJECT_ROOT / "assets" / "app_icon.ico").exists():
        shutil.copy2(PROJECT_ROOT / "assets" / "app_icon.ico", assets_dest / "app_icon.ico")
    if (PROJECT_ROOT / "assets" / "app_icon.png").exists():
        shutil.copy2(PROJECT_ROOT / "assets" / "app_icon.png", assets_dest / "app_icon.png")

    ext_dest = GETLY_DIST_DIR / "extension"
    ext_dest2 = GETLY_DIST_DIR / "extensions" / "chrome"
    ext_src = PROJECT_ROOT / "extensions" / "chrome"
    if ext_src.exists():
        if ext_dest.exists():
            shutil.rmtree(ext_dest, ignore_errors=True)
        shutil.copytree(ext_src, ext_dest)
        if ext_dest2.exists():
            shutil.rmtree(ext_dest2, ignore_errors=True)
        ext_dest2.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ext_src, ext_dest2)

    print(f"\n[+] Frontend WinUI 3 compilato con successo: {target_exe}")
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
        os.path.expandvars(r"%USERPROFILE%\AppData\Local\Programs\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c).resolve()
    return None


def step_4_build_installer(ico_path: Path):
    print("\n" + "=" * 70)
    print("[4/4] GENERAZIONE INSTALLER WINDOWS (Getly_Setup.exe)")
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
    getly_dir = GETLY_DIST_DIR
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
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    ico_path = step_1_generate_icons()
    step_2_build_engine(ico_path)
    step_3_build_frontend(ico_path)
    setup_path = step_4_build_installer(ico_path)

    print("\n" + "=" * 70)
    print("RIEPILOGO DISTRIBUZIONE GETLY")
    print("=" * 70)
    print(f"1. Cartella Applicazione Compilata: {GETLY_DIST_DIR}")
    print(f"2. File Eseguibile WinUI 3:        {GETLY_DIST_DIR / 'Getly.exe'}")
    print(f"3. Motore Headless Compilato:      {GETLY_DIST_DIR / 'GetlyEngine' / 'GetlyEngine.exe'}")
    print(f"4. Installer Ufficiale Windows:     {setup_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
