from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ScriptConfig:
    """Configuration for an individual script hook defined in manifest.json."""
    event: str = "onResolve"
    match_urls: List[str] = field(default_factory=list)
    entry: str = "index.js"


@dataclass
class ExtensionManifest:
    """Parsed Gopeed extension manifest metadata."""
    name: str
    version: str = "1.0.0"
    author: str = ""
    title: Optional[str] = None
    description: Optional[str] = None
    homepage: Optional[str] = None
    repository: Optional[str] = None
    scripts: List[ScriptConfig] = field(default_factory=list)
    settings: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return self.title or self.name


@dataclass
class ResolvedFile:
    """Individual file metadata returned by a resolver extension."""
    name: Optional[str] = None
    url: str = ""
    headers: Dict[str, str] = field(default_factory=dict)
    size: Optional[int] = None
    path: Optional[str] = None
    method: str = "GET"
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedResource:
    """Extracted stream or file resource returned by an onResolve hook."""
    url: str
    filename: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)
    files: List[ResolvedFile] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InstalledExtension:
    """Locally installed extension state and metadata."""
    id: str
    dir_path: Path
    manifest: ExtensionManifest
    enabled: bool = True
