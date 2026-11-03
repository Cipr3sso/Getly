import logging
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Known video streaming hosts supported by yt-dlp / stream extractors
STREAMING_DOMAINS = (
    "youtube.com",
    "youtu.be",
    "tiktok.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "vimeo.com",
    "reddit.com",
    "v.redd.it",
    "twitch.tv",
    "facebook.com",
    "fb.watch",
    "dailymotion.com",
    "dai.ly",
    "pinterest.com",
    "pin.it",
    "soundcloud.com",
    "bilibili.com",
    "streamable.com",
    "rumble.com",
    "odysee.com",
    "bitchute.com",
)


def is_m3u8_url(url: str) -> bool:
    """Checks whether the URL points to an HLS (.m3u8) playlist manifest."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip().lower()
    parsed = urllib.parse.urlparse(u)
    if parsed.path.endswith(".m3u8"):
        return True
    if "format=m3u8" in parsed.query or "type=m3u8" in parsed.query or ".m3u8" in parsed.path:
        return True
    return False


def is_media_stream_url(url: str) -> bool:
    """
    Returns True if the URL is a video/audio platform stream or HLS (.m3u8) playlist
    that requires stream extraction and remuxing rather than simple HTTP file download.
    """
    if not url or not isinstance(url, str):
        return False

    clean = url.strip().lower()
    if is_m3u8_url(clean):
        return True

    try:
        parsed = urllib.parse.urlparse(clean)
        netloc = parsed.netloc.lower()
        if any(d in netloc for d in STREAMING_DOMAINS):
            return True
    except Exception:
        pass

    return False


def sanitize_filename(title: str, ext: str = "mp4") -> str:
    """Sanitizes a title into a safe, valid filesystem filename on Windows/Linux."""
    clean_title = re.sub(r'[\\/*?:"<>|]', "_", title).strip()
    clean_title = re.sub(r"\s+", " ", clean_title)
    if not clean_title:
        clean_title = "video"
    ext = ext.lstrip(".")
    if not ext:
        ext = "mp4"
    return f"{clean_title}.{ext}"


class MediaExtractor:
    """
    Unified metadata extractor using yt-dlp to inspect video streams,
    resolutions, audio tracks, duration, and thumbnails across 1,800+ platforms.
    """

    @classmethod
    def extract_info(
        cls,
        url: str,
        source_address: Optional[str] = None,
        timeout: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """
        Extracts metadata from a video URL or HLS manifest without downloading.
        Returns a dictionary with title, duration, thumbnail, formats, etc.
        """
        try:
            import yt_dlp
        except ImportError:
            logger.warning("yt-dlp is not installed in the environment.")
            return None

        ydl_opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": False,
            "socket_timeout": timeout,
        }
        if source_address:
            ydl_opts["source_address"] = source_address

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                title = info.get("title") or "video"
                duration = info.get("duration")
                thumbnail = info.get("thumbnail")
                uploader = info.get("uploader") or info.get("channel") or ""
                ext = info.get("ext") or "mp4"

                # Parse clean format options
                raw_formats = info.get("formats") or []
                clean_formats: List[Dict[str, Any]] = []
                seen_heights = set()

                for f in raw_formats:
                    height = f.get("height")
                    format_id = f.get("format_id")
                    f_ext = f.get("ext") or ext
                    vcodec = f.get("vcodec") or "none"
                    acodec = f.get("acodec") or "none"
                    filesize = f.get("filesize") or f.get("filesize_approx")

                    if height and height not in seen_heights:
                        seen_heights.add(height)
                        clean_formats.append({
                            "format_id": format_id,
                            "resolution": f"{height}p",
                            "height": height,
                            "ext": f_ext,
                            "filesize": filesize,
                            "has_video": vcodec != "none",
                            "has_audio": acodec != "none",
                        })

                # Sort by resolution descending
                clean_formats.sort(key=lambda x: x.get("height") or 0, reverse=True)

                is_m3u8 = is_m3u8_url(url) or bool(info.get("protocol", "").startswith("m3u8"))

                return {
                    "url": url,
                    "title": title,
                    "filename": sanitize_filename(title, ext="mp4"),
                    "duration": duration,
                    "duration_formatted": cls._format_duration(duration),
                    "thumbnail": thumbnail,
                    "uploader": uploader,
                    "formats": clean_formats,
                    "is_m3u8": is_m3u8,
                    "original_ext": ext,
                }
        except Exception as exc:
            logger.debug("MediaExtractor.extract_info error for '%s': %s", url, exc)
            return None

    @staticmethod
    def _format_duration(seconds: Optional[float]) -> str:
        """Formats duration in seconds to HH:MM:SS or MM:SS string."""
        if not seconds or seconds <= 0:
            return "--:--"
        sec = int(seconds)
        h = sec // 3600
        m = (sec % 3600) // 60
        s = sec % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
