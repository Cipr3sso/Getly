import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Union
import urllib.request
import zipfile

from .models import ExtensionManifest, InstalledExtension, ResolvedResource, ScriptConfig
from .manifest import load_manifest_from_file, parse_manifest, url_matches_pattern
from .engine import QuickJSSandbox, QUICKJS_AVAILABLE, resolve_with_ytdlp, is_video_streaming_url
from core.system.paths import get_extensions_storage_dir

logger = logging.getLogger(__name__)

DEFAULT_EXTENSIONS_DIR = Path("data/extensions")


class ExtensionManager:
    """
    Manages installed Gopeed JavaScript extensions:
    - Installs extensions from local ZIP/JS, remote GitHub URLs or release packages.
    - Automatic npm install and bundle build when package.json is detected.
    - Monorepo subfolder installation support (user/repo#subfolder).
    - Manages enable/disable state and storage persistence.
    - Evaluates URL match patterns and executes matching onResolve scripts via QuickJS.
    """

    def __init__(self, storage_dir: Optional[Path] = None):
        self.storage_dir = Path(storage_dir) if storage_dir else get_extensions_storage_dir()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.storage_dir / "extensions_state.json"

        self._extensions: Dict[str, InstalledExtension] = {}
        self._disabled_ids: set[str] = set()
        self._settings_map: Dict[str, Dict[str, Any]] = {}

        if QUICKJS_AVAILABLE:
            self.sandbox = QuickJSSandbox()
        else:
            self.sandbox = None
            logger.warning("QuickJS is not available. Extension scripts will not execute.")

        self._load_state()
        self.load_extensions()

    def _load_state(self) -> None:
        """Loads enabled/disabled state and settings from state JSON file."""
        if not self.state_file.exists():
            return
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._disabled_ids = set(data.get("disabled", []))
            self._settings_map = data.get("settings", {})
        except Exception as exc:
            logger.debug("Could not read extensions state file: %s", exc)

    def _save_state(self) -> None:
        """Saves current state to state JSON file."""
        try:
            data = {
                "disabled": list(self._disabled_ids),
                "settings": self._settings_map,
            }
            self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Failed to write extensions state file: %s", exc)

    def load_extensions(self) -> List[InstalledExtension]:
        """Scans the extensions directory, loads all manifests, and updates memory cache."""
        self._extensions.clear()

        for item in self.storage_dir.iterdir():
            if not item.is_dir():
                continue

            manifest_file = item / "manifest.json"
            if not manifest_file.exists():
                continue

            try:
                manifest = load_manifest_from_file(manifest_file)
                ext_id = manifest.name
                is_enabled = ext_id not in self._disabled_ids

                inst = InstalledExtension(
                    id=ext_id,
                    dir_path=item,
                    manifest=manifest,
                    enabled=is_enabled,
                )
                self._extensions[ext_id] = inst
            except Exception as exc:
                logger.warning("Error loading extension from '%s': %s", item, exc)

        logger.info("Loaded %d Gopeed extension(s)", len(self._extensions))
        return list(self._extensions.values())

    def list_extensions(self) -> List[InstalledExtension]:
        """Returns all installed extensions."""
        return list(self._extensions.values())

    def get_extension(self, ext_id: str) -> Optional[InstalledExtension]:
        """Retrieves an extension by ID."""
        return self._extensions.get(ext_id)

    def enable_extension(self, ext_id: str) -> bool:
        """Enables an extension."""
        if ext_id in self._disabled_ids:
            self._disabled_ids.remove(ext_id)
            self._save_state()
        if ext_id in self._extensions:
            self._extensions[ext_id].enabled = True
            logger.info("Enabled extension '%s'", ext_id)
            return True
        return False

    def disable_extension(self, ext_id: str) -> bool:
        """Disables an extension from matching or resolving downloads."""
        self._disabled_ids.add(ext_id)
        self._save_state()
        if ext_id in self._extensions:
            self._extensions[ext_id].enabled = False
            logger.info("Disabled extension '%s'", ext_id)
            return True
        return False

    def delete_extension(self, ext_id: str) -> bool:
        """Deletes an extension directory from disk and updates state."""
        ext = self._extensions.pop(ext_id, None)
        if ext and ext.dir_path.exists():
            try:
                shutil.rmtree(ext.dir_path)
            except Exception as exc:
                logger.error("Failed to delete extension directory '%s': %s", ext.dir_path, exc)
                return False

        if ext_id in self._disabled_ids:
            self._disabled_ids.remove(ext_id)
        if ext_id in self._settings_map:
            del self._settings_map[ext_id]
        self._save_state()
        logger.info("Deleted extension '%s'", ext_id)
        return True

    def update_extension_settings(self, ext_id: str, new_settings: Dict[str, Any]) -> None:
        """Updates user settings for an extension and saves state to disk."""
        self._settings_map[ext_id] = dict(new_settings)
        self._save_state()

    def get_extension_settings(self, ext_id: str) -> Dict[str, Any]:
        """Retrieves active settings for an extension, merged with manifest defaults."""
        ext = self.get_extension(ext_id)
        merged: Dict[str, Any] = {}
        if ext and ext.manifest.settings:
            for s in ext.manifest.settings:
                if isinstance(s, dict):
                    name = s.get("name")
                    default_val = s.get("default")
                    if name:
                        merged[name] = default_val
        saved = self._settings_map.get(ext_id, {})
        merged.update(saved)
        return merged

    def _auto_build_if_needed(self, target_dir: Path, manifest: ExtensionManifest) -> None:
        """
        Inspects package.json. If dependencies or a build script are declared,
        or if the entry script is missing, runs npm install and npm run build / npx esbuild.
        """
        pkg_json = target_dir / "package.json"
        if not pkg_json.exists():
            return

        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
        except Exception:
            return

        needs_build = False
        for script in manifest.scripts:
            entry_path = target_dir / script.entry
            if not entry_path.exists():
                needs_build = True
                break

        scripts = pkg_data.get("scripts", {})
        if "build" in scripts or pkg_data.get("dependencies"):
            needs_build = True

        if not needs_build:
            return

        npm_bin = shutil.which("npm") or shutil.which("npm.cmd")
        npx_bin = shutil.which("npx") or shutil.which("npx.cmd")

        if not npm_bin:
            logger.info("npm not detected on system PATH; skipping auto-bundle for extension '%s'", manifest.name)
            return

        logger.info("Executing automatic npm install for extension '%s'...", manifest.name)
        try:
            res_install = subprocess.run(
                [npm_bin, "install", "--ignore-scripts"],
                cwd=str(target_dir),
                capture_output=True,
                text=True,
                timeout=120,
                shell=(sys.platform == "win32"),
            )
            if res_install.returncode != 0:
                logger.warning("npm install returned warning for '%s': %s", manifest.name, res_install.stderr[:200])

            if "build" in scripts:
                logger.info("Executing npm run build for extension '%s'...", manifest.name)
                res_build = subprocess.run(
                    [npm_bin, "run", "build"],
                    cwd=str(target_dir),
                    capture_output=True,
                    text=True,
                    timeout=120,
                    shell=(sys.platform == "win32"),
                )
                if res_build.returncode == 0:
                    logger.info("npm build successfully generated bundle for extension '%s'", manifest.name)
                else:
                    logger.warning("npm build failed for '%s': %s", manifest.name, res_build.stderr[:200])
            elif npx_bin:
                for script in manifest.scripts:
                    out_entry = target_dir / script.entry
                    if not out_entry.exists():
                        for cand in ["src/index.js", "src/index.ts", "index.js", "index.ts"]:
                            cand_file = target_dir / cand
                            if cand_file.exists():
                                out_entry.parent.mkdir(parents=True, exist_ok=True)
                                logger.info("Bundling %s -> %s using npx esbuild...", cand, script.entry)
                                subprocess.run(
                                    [npx_bin, "esbuild", str(cand_file), "--bundle", "--platform=browser", f"--outfile={out_entry}"],
                                    cwd=str(target_dir),
                                    capture_output=True,
                                    timeout=60,
                                    shell=(sys.platform == "win32"),
                                )
                                break
        except Exception as exc:
            logger.warning("Auto-build exception for extension '%s': %s", manifest.name, exc)

    def install_from_zip(
        self,
        zip_source: Union[str, Path, bytes],
        subfolder: Optional[str] = None,
    ) -> InstalledExtension:
        """
        Installs an extension from a ZIP archive (file path or in-memory bytes).
        Automatically handles subfolder monorepo paths and builds with npm if required.
        """
        if isinstance(zip_source, bytes):
            zf = zipfile.ZipFile(io.BytesIO(zip_source))
        else:
            zf = zipfile.ZipFile(zip_source)

        with zf:
            namelist = zf.namelist()

            if subfolder:
                norm_sub = subfolder.strip("/").replace("\\", "/") + "/"
                manifest_candidates = [n for n in namelist if norm_sub in n and n.endswith("manifest.json")]
            else:
                manifest_candidates = [n for n in namelist if n.endswith("manifest.json")]

            if not manifest_candidates:
                # If subfolder search was specific and failed, fallback to any manifest.json
                manifest_candidates = [n for n in namelist if n.endswith("manifest.json")]

            if not manifest_candidates:
                raise ValueError("ZIP archive does not contain a 'manifest.json' file")

            manifest_name = min(manifest_candidates, key=len)
            manifest_bytes = zf.read(manifest_name)
            manifest = parse_manifest(manifest_bytes)

            prefix = ""
            if "/" in manifest_name:
                prefix = manifest_name.rsplit("manifest.json", 1)[0]

            ext_dir_name = re.sub(r"[^\w\-_]", "_", manifest.name)
            target_dir = self.storage_dir / ext_dir_name
            if target_dir.exists():
                shutil.rmtree(target_dir)
            target_dir.mkdir(parents=True, exist_ok=True)

            # Extract files, stripping prefix if archive was wrapped in a folder
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel_path = member.filename
                if prefix and rel_path.startswith(prefix):
                    rel_path = rel_path[len(prefix):]
                if not rel_path or rel_path.startswith("/"):
                    rel_path = rel_path.lstrip("/")
                if not rel_path:
                    continue

                out_file = target_dir / rel_path
                out_file.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, open(out_file, "wb") as dest:
                    dest.write(source.read())

        # Step B: Auto-bundle with npm / esbuild if package.json requires it
        self._auto_build_if_needed(target_dir, manifest)

        self.load_extensions()
        inst = self.get_extension(manifest.name)
        if not inst:
            raise RuntimeError(f"Failed to load newly installed extension '{manifest.name}'")
        return inst

    def install_from_js(
        self,
        js_source: Union[str, Path, bytes],
        name: Optional[str] = None,
        match_urls: Optional[List[str]] = None,
    ) -> InstalledExtension:
        """
        Installs a standalone JavaScript file as a Gopeed extension,
        generating a minimal manifest.json wrapper.
        """
        if isinstance(js_source, (str, Path)) and Path(js_source).exists():
            js_code = Path(js_source).read_text(encoding="utf-8")
            ext_name = name or Path(js_source).stem
        elif isinstance(js_source, bytes):
            js_code = js_source.decode("utf-8", errors="replace")
            ext_name = name or "custom-js-extension"
        else:
            js_code = str(js_source)
            ext_name = name or "custom-js-extension"

        clean_name = re.sub(r"[^\w\-_]", "_", ext_name)
        target_dir = self.storage_dir / clean_name
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write index.js
        entry_file = target_dir / "index.js"
        entry_file.write_text(js_code, encoding="utf-8")

        # Write manifest.json
        manifest_data = {
            "name": clean_name,
            "version": "1.0.0",
            "author": "Local",
            "description": "Script di risoluzione JavaScript personalizzato",
            "scripts": [
                {
                    "event": "onResolve",
                    "match": match_urls or ["*"],
                    "entry": "index.js",
                }
            ],
        }
        (target_dir / "manifest.json").write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        self.load_extensions()
        inst = self.get_extension(clean_name)
        if not inst:
            raise RuntimeError(f"Failed to load created extension '{clean_name}'")
        return inst

    def install_from_url(self, raw_url: str) -> InstalledExtension:
        """
        Installs an extension from a remote URL:
        - GitHub repo URL (e.g. https://github.com/user/repo) -> queries Release API first for pre-compiled assets.
        - Fallbacks to repository archive and auto-builds with package.json.
        - Direct .zip file URL -> downloads and unpacks.
        - Direct .js script URL -> downloads and wraps.
        - Monorepo subfolder URLs (user/repo#subfolder).
        """
        clean_url = raw_url.strip()

        # Parse subfolder if present (#subfolder)
        subfolder = None
        if "#" in clean_url:
            clean_url, subfolder = clean_url.split("#", 1)
            clean_url = clean_url.strip()
            subfolder = subfolder.strip()

        # Shorthand user/repo
        shorthand_match = re.match(r"^([a-zA-Z0-9_\-\.]+)/([a-zA-Z0-9_\-\.]+)$", clean_url)
        if shorthand_match:
            user, repo = shorthand_match.groups()
            clean_url = f"https://github.com/{user}/{repo}"

        github_match = re.match(r"^https?://github\.com/([^/]+)/([^/]+?)(?:/(?:tree|commits?)/([^/]+))?/?$", clean_url)
        if github_match:
            user, repo, branch = github_match.groups()
            branch = branch or "main"

            # Step A: Query GitHub Releases API for pre-compiled .zip assets
            release_api_url = f"https://api.github.com/repos/{user}/{repo}/releases/latest"
            try:
                logger.info("Checking GitHub Releases API for pre-built package: %s", release_api_url)
                req = urllib.request.Request(
                    release_api_url,
                    headers={
                        "User-Agent": "Getly/1.0",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                with urllib.request.urlopen(req, timeout=8) as resp:
                    if resp.status == 200:
                        rel_data = json.loads(resp.read().decode("utf-8"))
                        assets = rel_data.get("assets", [])
                        zip_assets = [a for a in assets if a.get("name", "").lower().endswith(".zip")]
                        if zip_assets:
                            chosen_asset = zip_assets[0]
                            for a in zip_assets:
                                aname = a.get("name", "").lower()
                                if any(k in aname for k in ("dist", "bundle", "package", "extension", "gopeed")):
                                    chosen_asset = a
                                    break
                            download_url = chosen_asset.get("browser_download_url")
                            if download_url:
                                logger.info("Found pre-compiled release package: %s", download_url)
                                return self._download_and_install_zip(download_url, subfolder=subfolder)
            except Exception as exc:
                logger.debug("GitHub Release lookup failed or no releases (%s); falling back to source archive...", exc)

            # Step B: Source Archive Fallback
            archive_url = f"https://github.com/{user}/{repo}/archive/refs/heads/{branch}.zip"
            logger.info("Downloading GitHub source archive: %s", archive_url)
            try:
                return self._download_and_install_zip(archive_url, subfolder=subfolder)
            except Exception as exc:
                if branch == "main":
                    fallback_url = f"https://github.com/{user}/{repo}/archive/refs/heads/master.zip"
                    logger.info("Retrying GitHub archive with master branch: %s", fallback_url)
                    return self._download_and_install_zip(fallback_url, subfolder=subfolder)
                raise exc

        # Direct ZIP URL
        if clean_url.lower().endswith(".zip") or "/archive/" in clean_url:
            return self._download_and_install_zip(clean_url, subfolder=subfolder)

        # Direct JS URL
        if clean_url.lower().endswith(".js") or "raw.githubusercontent.com" in clean_url:
            req = urllib.request.Request(clean_url, headers={"User-Agent": "Getly/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                code_text = resp.read().decode("utf-8", errors="replace")
                script_name = clean_url.split("/")[-1].split("?")[0].replace(".js", "")
                return self.install_from_js(code_text, name=script_name)

        # Default fallback: try fetching as ZIP first, then JS
        try:
            return self._download_and_install_zip(clean_url, subfolder=subfolder)
        except Exception:
            req = urllib.request.Request(clean_url, headers={"User-Agent": "Getly/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                try:
                    return self.install_from_zip(data, subfolder=subfolder)
                except Exception:
                    code_text = data.decode("utf-8", errors="replace")
                    return self.install_from_js(code_text)

    def _download_and_install_zip(self, url: str, subfolder: Optional[str] = None) -> InstalledExtension:
        """Downloads a remote ZIP archive and unpacks it."""
        req = urllib.request.Request(url, headers={"User-Agent": "Getly/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
            return self.install_from_zip(data, subfolder=subfolder)

    def resolve_url(self, raw_url: str, source_address: Optional[str] = None) -> Optional[ResolvedResource]:
        """
        Tests active extensions against raw_url.
        If a matching onResolve script is found, runs it in QuickJS and returns ResolvedResource.
        Falls back to yt-dlp provider if JS script fails, encounters expired tokens, or returns no valid streams.
        Passes source_address to bind outbound resolver IP to the primary network adapter.
        """
        is_streaming = is_video_streaming_url(raw_url)
        matched_ext = False

        if self.sandbox:
            for ext in self._extensions.values():
                if not ext.enabled:
                    continue

                for script in ext.manifest.scripts:
                    if script.event != "onResolve":
                        continue

                    # Check if raw_url matches any configured pattern
                    matches = False
                    if not script.match_urls or "*" in script.match_urls:
                        matches = True
                    else:
                        for pattern in script.match_urls:
                            if url_matches_pattern(raw_url, pattern):
                                matches = True
                                break

                    if not matches:
                        continue

                    matched_ext = True
                    entry_file = ext.dir_path / script.entry
                    if not entry_file.exists():
                        logger.warning(
                            "Extension '%s' entry script not found: %s",
                            ext.id,
                            entry_file,
                        )
                        continue

                    try:
                        script_code = entry_file.read_text(encoding="utf-8")
                        settings = self.get_extension_settings(ext.id)
                        logger.info("Executing resolver extension '%s' for URL: %s", ext.id, raw_url)

                        resolved = self.sandbox.resolve_url(
                            script_code=script_code,
                            raw_url=raw_url,
                            settings=settings,
                            ext_id=ext.id,
                        )
                        if resolved and (resolved.url or resolved.files):
                            logger.info(
                                "Extension '%s' successfully resolved '%s' -> '%s' (filename: %s, files: %d)",
                                ext.id,
                                raw_url,
                                resolved.url,
                                resolved.filename,
                                len(resolved.files),
                            )
                            return resolved
                        else:
                            logger.warning(
                                "Extension '%s' returned 0 valid files for '%s'",
                                ext.id,
                                raw_url,
                            )
                    except Exception as exc:
                        logger.error("Error running extension '%s' resolver: %s", ext.id, exc)

        # Fallback to yt-dlp if an extension matched (e.g. YouTube extension failed/expired) or if it's a known streaming URL
        if matched_ext or is_streaming:
            logger.info("Invoking yt-dlp streaming bridge fallback for '%s'...", raw_url)
            fallback = resolve_with_ytdlp(raw_url, source_address=source_address)
            if fallback and (fallback.url or fallback.files):
                logger.info(
                    "yt-dlp fallback successfully resolved '%s' -> '%s' (filename: %s)",
                    raw_url,
                    fallback.url,
                    fallback.filename,
                )
                return fallback

        return None
