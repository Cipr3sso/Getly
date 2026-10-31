"""
Gopeed Extension System for Resilient Downloader.
Provides QuickJS sandboxing, Gopeed manifest parsing, and link resolution hooks.
"""

from .models import (
    ExtensionManifest,
    InstalledExtension,
    ResolvedFile,
    ResolvedResource,
    ScriptConfig,
)
from .manifest import parse_manifest, load_manifest_from_file, url_matches_pattern
from .engine import QuickJSSandbox, QUICKJS_AVAILABLE
from .manager import ExtensionManager

__all__ = [
    "ExtensionManifest",
    "InstalledExtension",
    "ResolvedFile",
    "ResolvedResource",
    "ScriptConfig",
    "parse_manifest",
    "load_manifest_from_file",
    "url_matches_pattern",
    "QuickJSSandbox",
    "QUICKJS_AVAILABLE",
    "ExtensionManager",
]
