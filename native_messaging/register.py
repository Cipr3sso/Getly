#!/usr/bin/env python3
"""
Windows Registry Setup Script for Resilient Download Manager Native Messaging Host.
Registers the host manifest for Google Chrome, Microsoft Edge, and Brave Browser.
"""

import argparse
import json
import os
from pathlib import Path
import sys

# Fixed extension ID corresponding to the extension key in extension/manifest.json
DEFAULT_EXTENSION_ID = "kicdlfenjgbnjhldfagmoobkdnoniaok"


def register_host(extension_id: str = DEFAULT_EXTENSION_ID) -> None:
    current_dir = Path(__file__).resolve().parent
    host_py = current_dir / "host.py"
    bat_path = current_dir / "resilient_downloader.bat"
    manifest_path = current_dir / "resilient_downloader.json"
    python_exe = sys.executable

    print(f"[1/3] Generating launcher batch script: {bat_path}")
    bat_content = f'@echo off\n"{python_exe}" "{host_py}" %*\n'
    bat_path.write_text(bat_content, encoding="utf-8")

    print(f"[2/3] Generating Native Messaging manifest: {manifest_path}")
    allowed_origins = [
        f"chrome-extension://{DEFAULT_EXTENSION_ID}/",
    ]
    if extension_id and extension_id != DEFAULT_EXTENSION_ID:
        allowed_origins.append(f"chrome-extension://{extension_id}/")

    manifest_data = {
        "name": "resilient_downloader",
        "description": "Resilient Heisenberg Download Manager Native Host",
        "path": str(bat_path),
        "type": "stdio",
        "allowed_origins": allowed_origins,
    }
    manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

    print("[3/3] Registering in Windows Registry (HKCU)...")
    if sys.platform != "win32":
        print("  [!] Non-Windows platform detected. Windows Registry configuration skipped.")
        return

    import winreg

    targets = [
        ("Google Chrome", r"Software\Google\Chrome\NativeMessagingHosts\resilient_downloader"),
        ("Microsoft Edge", r"Software\Microsoft\Edge\NativeMessagingHosts\resilient_downloader"),
        ("Brave Browser", r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\resilient_downloader"),
    ]

    for browser_name, subkey_path in targets:
        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, subkey_path)
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path))
            winreg.CloseKey(key)
            print(f"  [+] Registered for {browser_name}: HKCU\\{subkey_path}")
        except Exception as exc:
            print(f"  [-] Failed to register for {browser_name}: {exc}")

    print("\n[SUCCESS] Native Messaging Host registrato con successo!")
    print(f"  Manifest Path: {manifest_path}")
    print(f"  Extension IDs abilitati: {', '.join(allowed_origins)}")


def unregister_host() -> None:
    if sys.platform != "win32":
        print("Non-Windows platform. Nothing to unregister.")
        return

    import winreg

    targets = [
        ("Google Chrome", r"Software\Google\Chrome\NativeMessagingHosts\resilient_downloader"),
        ("Microsoft Edge", r"Software\Microsoft\Edge\NativeMessagingHosts\resilient_downloader"),
        ("Brave Browser", r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\resilient_downloader"),
    ]

    for browser_name, subkey_path in targets:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey_path)
            print(f"  [+] Unregistered from {browser_name}")
        except FileNotFoundError:
            pass
        except Exception as exc:
            print(f"  [-] Could not delete {browser_name} key: {exc}")

    print("Disinstallazione chiavi completata.")


def main():
    parser = argparse.ArgumentParser(description="Register Resilient Downloader Native Messaging Host for Chromium browsers.")
    parser.add_argument("--extension-id", type=str, default=DEFAULT_EXTENSION_ID, help="Chromium Extension ID to whitelist")
    parser.add_argument("--unregister", action="store_true", help="Remove registry keys")
    args = parser.parse_args()

    if args.unregister:
        unregister_host()
    else:
        register_host(args.extension_id)


if __name__ == "__main__":
    main()
