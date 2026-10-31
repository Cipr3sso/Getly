import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Pattern, Union

from .models import ExtensionManifest, ScriptConfig

logger = logging.getLogger(__name__)


def pattern_to_regex(pattern: str) -> Pattern[str]:
    r"""
    Converts a Gopeed/Web-extension URL match pattern (e.g. '*://*.example.com/*')
    into a compiled Python regular expression.
    Supports wildcard subdomains (*.domain.com -> (?:[a-zA-Z0-9-]+\.)*domain.com),
    schemes (*:// -> (?:https?|wss?)://), and trailing path wildcards (/watch/* -> /watch(?:\/.*|\?.*|$)).
    """
    p = pattern.strip()
    if p in ("*", "<all_urls>"):
        return re.compile(r"^(?:https?|wss?)://.*$", re.IGNORECASE)

    # 1. Handle Scheme
    if p.startswith("*://"):
        scheme_regex = r"(?:https?|wss?)://"
        rest = p[4:]
    elif "://" in p:
        scheme, rest = p.split("://", 1)
        scheme_regex = re.escape(scheme) + r"://"
    else:
        scheme_regex = r"(?:(?:https?|wss?)://)?"
        rest = p

    # 2. Host and Path separation
    if "/" in rest:
        host_part, path_part = rest.split("/", 1)
        path_part = "/" + path_part
    else:
        host_part = rest
        path_part = "/*"

    # Convert host part
    if host_part.startswith("*."):
        domain = host_part[2:]
        host_regex = r"(?:[a-zA-Z0-9-]+\.)*" + re.escape(domain)
    elif host_part == "*":
        host_regex = r"[a-zA-Z0-9.\-]+"
    elif host_part.startswith("*"):
        host_regex = r".*" + re.escape(host_part[1:])
    else:
        host_regex = re.escape(host_part).replace(r"\*", r".*")

    # Convert path part
    if path_part in ("/*", "/"):
        path_regex = r"(?:/.*|\?.*|$)"
    elif path_part.endswith("/*"):
        prefix = path_part[:-2]
        prefix_escaped = re.escape(prefix).replace(r"\*", r".*")
        path_regex = prefix_escaped + r"(?:/.*|\?.*|$)"
    else:
        path_regex = re.escape(path_part).replace(r"\*", r".*").replace(r"\?", r".")

    regex_str = f"^{scheme_regex}{host_regex}{path_regex}$"
    return re.compile(regex_str, re.IGNORECASE)


def url_matches_pattern(url: str, pattern: str) -> bool:
    """Checks whether a URL matches a wildcard pattern."""
    try:
        rx = pattern_to_regex(pattern)
        return bool(rx.match(url))
    except Exception as exc:
        logger.debug("Error matching URL '%s' with pattern '%s': %s", url, pattern, exc)
        return False


def parse_manifest(data: Union[str, bytes, Dict[str, Any]]) -> ExtensionManifest:
    """
    Parses a Gopeed extension manifest.json (dict or JSON string/bytes)
    into an ExtensionManifest instance with validation.
    """
    if isinstance(data, (str, bytes)):
        manifest_dict = json.loads(data)
    elif isinstance(data, dict):
        manifest_dict = data
    else:
        raise ValueError(f"Unsupported manifest data type: {type(data)}")

    name = str(manifest_dict.get("name") or "").strip()
    if not name:
        raise ValueError("Extension manifest missing required 'name' field")

    version = str(manifest_dict.get("version") or "1.0.0").strip()
    author = str(manifest_dict.get("author") or "").strip()
    title = manifest_dict.get("title")
    description = manifest_dict.get("description")
    homepage = manifest_dict.get("homepage")
    repository = manifest_dict.get("repository")

    # Parse scripts list
    raw_scripts = manifest_dict.get("scripts", [])
    parsed_scripts: List[ScriptConfig] = []

    for s in raw_scripts:
        if not isinstance(s, dict):
            continue
        event = str(s.get("event") or "onResolve").strip()
        entry = str(s.get("entry") or "index.js").strip()

        # Parse match pattern(s)
        match_val = s.get("match")
        match_urls: List[str] = []
        if isinstance(match_val, dict):
            urls = match_val.get("urls", [])
            if isinstance(urls, list):
                match_urls.extend(str(u).strip() for u in urls if u)
            elif isinstance(urls, str):
                match_urls.append(urls.strip())
        elif isinstance(match_val, list):
            match_urls.extend(str(u).strip() for u in match_val if u)
        elif isinstance(match_val, str):
            match_urls.append(match_val.strip())

        parsed_scripts.append(
            ScriptConfig(
                event=event,
                match_urls=match_urls,
                entry=entry,
            )
        )

    # Parse optional settings schema
    raw_settings = manifest_dict.get("settings", [])
    settings = raw_settings if isinstance(raw_settings, list) else []

    return ExtensionManifest(
        name=name,
        version=version,
        author=author,
        title=title,
        description=description,
        homepage=homepage,
        repository=repository,
        scripts=parsed_scripts,
        settings=settings,
    )


def load_manifest_from_file(manifest_path: Path) -> ExtensionManifest:
    """Loads and parses manifest.json from a file path."""
    content = manifest_path.read_text(encoding="utf-8")
    return parse_manifest(content)
