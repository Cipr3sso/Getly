import asyncio
import logging
import re
import urllib.parse
from typing import Callable, Optional, Set
import pyperclip

logger = logging.getLogger(__name__)

# Search engines and navigation sites to explicitly ignore
IGNORED_DOMAINS = (
    "google.",
    "bing.",
    "duckduckgo.",
    "yahoo.",
    "youtube.",
    "youtu.be",
    "reddit.",
    "facebook.",
    "instagram.",
    "twitter.",
    "x.com",
    "wikipedia.",
    "github.com",
)

# Valid binary / media file extensions
VALID_BINARY_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso",
    ".bin", ".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".apk",
    ".appimage", ".dat", ".mp4", ".mkv", ".avi", ".mov", ".wmv",
    ".flv", ".webm", ".mp3", ".flac", ".wav", ".aac", ".ogg",
    ".pdf", ".epub", ".torrent",
)

# Known file hosters with specific download routes
KNOWN_FILE_HOSTERS = (
    "rapidgator.net/file/",
    "mediafire.com/file/",
    "mega.nz/file/",
    "1fichier.com/?",
    "ddownload.com/",
    "turbobit.net/",
    "nitroflare.com/view/",
    "filecrypt.cc/container/",
)

# Known direct speedtest hosts
DIRECT_SPEEDTEST_HOSTS = (
    "proof.ovh.net",
    "speed.hetzner.de",
    "testfile.org",
)


class ClipboardWatcher:
    """
    Background asynchronous watcher that inspects the system clipboard
    and triggers a confirmation callback whenever a downloadable link is copied.
    """

    def __init__(
        self,
        on_link_detected: Callable[[str], None],
        poll_interval_sec: float = 1.0,
        enabled: bool = True,
    ):
        self.on_link_detected = on_link_detected
        self.poll_interval_sec = poll_interval_sec
        self.enabled = enabled

        self._seen_urls: Set[str] = set()
        self._last_raw_text: str = ""
        self._task: Optional[asyncio.Task] = None
        self._is_running = False

    def start(self) -> None:
        """Starts the clipboard polling task."""
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("ClipboardWatcher started (polling every %.1fs)", self.poll_interval_sec)

    def stop(self) -> None:
        """Stops the clipboard polling task."""
        self._is_running = False
        if self._task:
            self._task.cancel()
        logger.info("ClipboardWatcher stopped.")

    async def _poll_loop(self) -> None:
        while self._is_running:
            try:
                await asyncio.sleep(self.poll_interval_sec)
                if not self.enabled:
                    continue

                text = pyperclip.paste()
                if not text or text == self._last_raw_text:
                    continue

                self._last_raw_text = text
                clean_text = text.strip()

                if self._is_downloadable_url(clean_text) and clean_text not in self._seen_urls:
                    self._seen_urls.add(clean_text)
                    logger.info("ClipboardWatcher detected URL: %s", clean_text)
                    self.on_link_detected(clean_text)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Clipboard polling check error: %s", exc)

    def _is_downloadable_url(self, text: str) -> bool:
        """Checks if text looks like a valid downloadable URL according to strict rules."""
        # 0. Support magnet links immediately
        if text.startswith("magnet:?") or text.startswith("magnet:?xt="):
            return True

        if not text.startswith(("http://", "https://")):
            return False

        try:
            parsed = urllib.parse.urlparse(text)
        except Exception:
            return False

        netloc = parsed.netloc.lower()
        if not netloc:
            return False

        # 1. Explicitly ignore search engines, social media, and navigation sites
        if any(ignored in netloc for ignored in IGNORED_DOMAINS):
            return False

        clean_path = parsed.path.lower()

        # 2. Match URLs ending with a valid binary extension
        if any(clean_path.endswith(ext) for ext in VALID_BINARY_EXTENSIONS):
            return True

        # 3. Match known file hosters with specific download routes
        full_route = (netloc + parsed.path).lower()
        if any(hoster in full_route for hoster in KNOWN_FILE_HOSTERS):
            return True

        # 4. Match direct speedtest endpoints with file/size segments (e.g. proof.ovh.net/files/100Mb.dat)
        if any(st_host in netloc for st_host in DIRECT_SPEEDTEST_HOSTS):
            if any(term in clean_path for term in ["100mb", "1gb", "10gb", "files/", "testfile"]):
                return True

        return False
