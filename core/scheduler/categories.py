"""
Automatic File Classification and Category Routing for Getly.
Categorizes files by extension and MIME type into dedicated subdirectories.
"""

from pathlib import Path
import posixpath
import re
from typing import Dict, List, Optional, Set
import urllib.parse


CATEGORY_COMPRESSED = "Compressi"
CATEGORY_PROGRAMS = "Programmi"
CATEGORY_VIDEO = "Video"
CATEGORY_AUDIO = "Audio"
CATEGORY_DOCUMENTS = "Documenti"
CATEGORY_IMAGES_ISO = "Immagini_ISO"
CATEGORY_TORRENTS = "Torrents"
CATEGORY_OTHER = "Altro"

ALL_CATEGORIES: List[str] = [
    CATEGORY_COMPRESSED,
    CATEGORY_PROGRAMS,
    CATEGORY_VIDEO,
    CATEGORY_AUDIO,
    CATEGORY_DOCUMENTS,
    CATEGORY_IMAGES_ISO,
    CATEGORY_TORRENTS,
    CATEGORY_OTHER,
]

# Mapping extension (lowercase with leading dot) -> category
EXTENSION_MAP: Dict[str, str] = {
    # Compressi
    ".zip": CATEGORY_COMPRESSED,
    ".rar": CATEGORY_COMPRESSED,
    ".7z": CATEGORY_COMPRESSED,
    ".tar": CATEGORY_COMPRESSED,
    ".gz": CATEGORY_COMPRESSED,
    ".tgz": CATEGORY_COMPRESSED,
    ".bz2": CATEGORY_COMPRESSED,
    ".xz": CATEGORY_COMPRESSED,
    # Programmi
    ".exe": CATEGORY_PROGRAMS,
    ".msi": CATEGORY_PROGRAMS,
    ".bat": CATEGORY_PROGRAMS,
    ".ipa": CATEGORY_PROGRAMS,
    ".apk": CATEGORY_PROGRAMS,
    ".dmg": CATEGORY_PROGRAMS,
    ".deb": CATEGORY_PROGRAMS,
    ".appimage": CATEGORY_PROGRAMS,
    # Video
    ".mp4": CATEGORY_VIDEO,
    ".mkv": CATEGORY_VIDEO,
    ".avi": CATEGORY_VIDEO,
    ".mov": CATEGORY_VIDEO,
    ".webm": CATEGORY_VIDEO,
    ".flv": CATEGORY_VIDEO,
    ".ts": CATEGORY_VIDEO,
    # Audio
    ".mp3": CATEGORY_AUDIO,
    ".flac": CATEGORY_AUDIO,
    ".wav": CATEGORY_AUDIO,
    ".aac": CATEGORY_AUDIO,
    ".ogg": CATEGORY_AUDIO,
    ".m4a": CATEGORY_AUDIO,
    # Documenti & 3D
    ".pdf": CATEGORY_DOCUMENTS,
    ".doc": CATEGORY_DOCUMENTS,
    ".docx": CATEGORY_DOCUMENTS,
    ".ppt": CATEGORY_DOCUMENTS,
    ".pptx": CATEGORY_DOCUMENTS,
    ".xls": CATEGORY_DOCUMENTS,
    ".xlsx": CATEGORY_DOCUMENTS,
    ".txt": CATEGORY_DOCUMENTS,
    ".csv": CATEGORY_DOCUMENTS,
    ".stl": CATEGORY_DOCUMENTS,
    ".obj": CATEGORY_DOCUMENTS,
    ".step": CATEGORY_DOCUMENTS,
    # Immagini & ISO
    ".iso": CATEGORY_IMAGES_ISO,
    ".img": CATEGORY_IMAGES_ISO,
    ".bin": CATEGORY_IMAGES_ISO,
    ".jpg": CATEGORY_IMAGES_ISO,
    ".jpeg": CATEGORY_IMAGES_ISO,
    ".png": CATEGORY_IMAGES_ISO,
    ".webp": CATEGORY_IMAGES_ISO,
    ".avif": CATEGORY_IMAGES_ISO,
    # Torrents
    ".torrent": CATEGORY_TORRENTS,
}

# MIME type mapping
MIME_MAP: Dict[str, str] = {
    "application/x-bittorrent": CATEGORY_TORRENTS,
    "application/x-torrent": CATEGORY_TORRENTS,
    "application/zip": CATEGORY_COMPRESSED,
    "application/x-zip-compressed": CATEGORY_COMPRESSED,
    "application/x-rar-compressed": CATEGORY_COMPRESSED,
    "application/x-7z-compressed": CATEGORY_COMPRESSED,
    "application/x-tar": CATEGORY_COMPRESSED,
    "application/gzip": CATEGORY_COMPRESSED,
    "application/x-bzip2": CATEGORY_COMPRESSED,
    "application/x-xz": CATEGORY_COMPRESSED,
    "application/x-msdownload": CATEGORY_PROGRAMS,
    "application/x-msdos-program": CATEGORY_PROGRAMS,
    "application/vnd.microsoft.portable-executable": CATEGORY_PROGRAMS,
    "application/vnd.android.package-archive": CATEGORY_PROGRAMS,
    "application/x-apple-diskimage": CATEGORY_PROGRAMS,
    "application/x-debian-package": CATEGORY_PROGRAMS,
    "application/pdf": CATEGORY_DOCUMENTS,
    "application/msword": CATEGORY_DOCUMENTS,
    "text/plain": CATEGORY_DOCUMENTS,
    "text/csv": CATEGORY_DOCUMENTS,
    "application/vnd.ms-excel": CATEGORY_DOCUMENTS,
    "application/x-iso9660-image": CATEGORY_IMAGES_ISO,
}

CATEGORY_COLORS: Dict[str, Dict[str, str]] = {
    CATEGORY_COMPRESSED: {"color": "#C084FC", "bg": "rgba(192, 132, 252, 0.15)", "border": "rgba(192, 132, 252, 0.35)"},
    CATEGORY_PROGRAMS: {"color": "#F43F5E", "bg": "rgba(244, 63, 94, 0.15)", "border": "rgba(244, 63, 94, 0.35)"},
    CATEGORY_VIDEO: {"color": "#38BDF8", "bg": "rgba(56, 189, 248, 0.15)", "border": "rgba(56, 189, 248, 0.35)"},
    CATEGORY_AUDIO: {"color": "#FBBF24", "bg": "rgba(251, 191, 36, 0.15)", "border": "rgba(251, 191, 36, 0.35)"},
    CATEGORY_DOCUMENTS: {"color": "#34D399", "bg": "rgba(52, 211, 153, 0.15)", "border": "rgba(52, 211, 153, 0.35)"},
    CATEGORY_IMAGES_ISO: {"color": "#EC4899", "bg": "rgba(236, 72, 153, 0.15)", "border": "rgba(236, 72, 153, 0.35)"},
    CATEGORY_TORRENTS: {"color": "#A855F7", "bg": "rgba(168, 85, 247, 0.15)", "border": "rgba(168, 85, 247, 0.35)"},
    CATEGORY_OTHER: {"color": "#948FA8", "bg": "rgba(148, 143, 168, 0.15)", "border": "rgba(148, 143, 168, 0.30)"},
}


def _extract_filename_from_url(url: str) -> str:
    """Extracts the candidate filename from path or query string."""
    try:
        parsed = urllib.parse.urlparse(url)
        # Check query string for common filename parameters
        query = urllib.parse.parse_qs(parsed.query)
        for key in ("filename", "file", "name", "fn", "download"):
            if key in query and query[key]:
                candidate = query[key][0]
                if "." in candidate:
                    return candidate

        # Fallback to posix path basename
        path_name = posixpath.basename(parsed.path)
        if path_name:
            return path_name
    except Exception:
        pass
    return url


def detect_category(filename_or_url: str, mime_type: Optional[str] = None) -> str:
    """
    Intelligently detects file category from filename, URL, or MIME type.
    """
    clean_target = filename_or_url.strip() if filename_or_url else ""

    # 0. Check magnet link or torrent extension early
    if clean_target.startswith("magnet:") or clean_target.lower().endswith(".torrent"):
        return CATEGORY_TORRENTS

    # 1. Check direct MIME type if provided
    if mime_type:
        clean_mime = mime_type.split(";")[0].strip().lower()
        if clean_mime in MIME_MAP:
            return MIME_MAP[clean_mime]
        if clean_mime.startswith("video/"):
            return CATEGORY_VIDEO
        if clean_mime.startswith("audio/"):
            return CATEGORY_AUDIO
        if clean_mime.startswith("image/"):
            return CATEGORY_IMAGES_ISO
        if "wordprocessingml" in clean_mime or "spreadsheetml" in clean_mime or "presentationml" in clean_mime:
            return CATEGORY_DOCUMENTS

    # 2. Extract candidate filename
    candidate = clean_target
    if clean_target.startswith("http://") or clean_target.startswith("https://"):
        candidate = _extract_filename_from_url(clean_target)

    # 3. Check extension
    candidate_lower = candidate.lower()
    # Check compound extensions (e.g., .tar.gz)
    if candidate_lower.endswith(".tar.gz") or candidate_lower.endswith(".tar.bz2") or candidate_lower.endswith(".tar.xz"):
        return CATEGORY_COMPRESSED

    ext = Path(candidate_lower).suffix
    if ext in EXTENSION_MAP:
        return EXTENSION_MAP[ext]

    # Regex search for known extension in candidate string (e.g., file.zip?version=1)
    for known_ext, cat in EXTENSION_MAP.items():
        escaped_ext = re.escape(known_ext)
        if re.search(rf"{escaped_ext}(?:[?&#/]|$)", candidate_lower):
            return cat

    return CATEGORY_OTHER


def get_category_subfolder(base_dir: Path, category: str) -> Path:
    """
    Returns the dedicated subfolder inside base_dir for the given category.
    Example: base_dir / 'Video'
    """
    sub_name = category if category in ALL_CATEGORIES else CATEGORY_OTHER
    folder = Path(base_dir) / sub_name
    return folder
