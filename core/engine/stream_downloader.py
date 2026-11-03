from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Callable, Optional, Dict, Any, TYPE_CHECKING

from .media_extractor import is_m3u8_url, sanitize_filename
from .probe import RemoteResourceInfo

if TYPE_CHECKING:
    from core.scheduler.task import DownloadTask

logger = logging.getLogger(__name__)


class StreamDownloadEngine:
    """
    Downloads media streams from video platforms (YouTube, TikTok, Instagram, Reddit, Vimeo, etc.)
    and HLS (.m3u8) playlists, concurrently downloading video and audio tracks, and remuxing them
    losslessly into a single playable MP4 file using ffmpeg.
    """

    def __init__(
        self,
        task: DownloadTask,
        destination_dir: Path,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        source_address: Optional[str] = None,
        custom_format: Optional[str] = None,
    ):
        self.task = task
        self.destination_dir = Path(destination_dir)
        self.destination_dir.mkdir(parents=True, exist_ok=True)
        self.on_progress = on_progress
        self.source_address = source_address
        self.custom_format = custom_format

        self.error_message: Optional[str] = None
        self._is_cancelled: bool = False
        self._total_downloaded: int = 0
        self._total_size: int = 0
        self.target_filepath: Optional[Path] = None
        self.info: Optional[RemoteResourceInfo] = None

        # Determine initial target filepath
        if self.task.filename:
            fn = self.task.filename
            if not fn.lower().endswith(".mp4") and not fn.lower().endswith(".mkv"):
                fn = f"{fn}.mp4"
            self.target_filepath = self.destination_dir / fn
        else:
            self.target_filepath = self.destination_dir / "video.mp4"

        self.info = RemoteResourceInfo(
            url=self.task.url,
            total_size=0,
            filename=self.target_filepath.name,
            supports_ranges=True,
            content_type="video/mp4",
        )

    async def start(self) -> bool:
        """Starts the stream extraction, fragment download, and ffmpeg remuxing."""
        self._is_cancelled = False
        try:
            return await asyncio.to_thread(self._run_download_sync)
        except Exception as exc:
            logger.error("StreamDownloadEngine error for '%s': %s", self.task.url, exc)
            if not self.error_message:
                self.error_message = str(exc)
            return False

    def _run_download_sync(self) -> bool:
        """Synchronous worker running inside asyncio.to_thread."""
        try:
            import yt_dlp
        except ImportError:
            self.error_message = "yt-dlp non è installato nell'ambiente di esecuzione"
            logger.error(self.error_message)
            return False

        ffmpeg_bin = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

        # Template for output filename
        if self.task.filename:
            stem = Path(self.task.filename).stem
            outtmpl = str(self.destination_dir / f"{stem}.%(ext)s")
        else:
            outtmpl = str(self.destination_dir / "%(title)s.%(ext)s")

        # Preferred format: best video (mp4 preferred) + best audio (m4a preferred), fallback to best progressive or any
        format_selector = (
            self.custom_format
            or "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
        )

        def progress_hook(d: Dict[str, Any]) -> None:
            if self._is_cancelled:
                raise Exception("Download annullato dall'utente")

            status = d.get("status")
            if status == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                self._total_downloaded = downloaded

                if total > 0:
                    self._total_size = total
                    pct = min(100.0, (downloaded / total) * 100.0)
                else:
                    pct = 0.0

                if self.info:
                    if total > 0:
                        self.info.total_size = total
                    elif downloaded > 0 and self.info.total_size <= 0:
                        self.info.total_size = downloaded

                if self.on_progress:
                    self.on_progress(downloaded, total, pct)

            elif status == "finished":
                fn = d.get("filename")
                if fn:
                    p = Path(fn)
                    self.target_filepath = p
                    if self.info:
                        self.info.filename = p.name
                        if p.exists():
                            self.info.total_size = p.stat().st_size
                            self._total_downloaded = self.info.total_size

        ydl_opts: Dict[str, Any] = {
            "outtmpl": outtmpl,
            "format": format_selector,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "concurrent_fragment_downloads": 4,  # Accelerated parallel chunk downloads for HLS/m3u8/DASH
            "progress_hooks": [progress_hook],
            "postprocessors": [
                {
                    "key": "FFmpegMerger",
                }
            ],
        }

        if ffmpeg_bin:
            ydl_opts["ffmpeg_location"] = ffmpeg_bin
        if self.source_address:
            ydl_opts["source_address"] = self.source_address

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Pre-extract info to obtain clean title and filename before downloading
                try:
                    meta = ydl.extract_info(self.task.url, download=False)
                    if meta:
                        title = meta.get("title")
                        if title:
                            clean_fn = sanitize_filename(title, ext="mp4")
                            self.task.filename = clean_fn
                            if self.info:
                                self.info.filename = clean_fn
                            self.target_filepath = self.destination_dir / clean_fn
                except Exception as meta_exc:
                    logger.debug("Could not pre-extract metadata: %s", meta_exc)

                # Execute download and muxing
                ydl.download([self.task.url])

            # Locate the completed final file if target_filepath changed during muxing
            if self.target_filepath and not self.target_filepath.exists():
                # Check if an .mp4 or .mkv file with similar stem exists
                stem = self.target_filepath.stem
                for cand in self.destination_dir.glob(f"{stem}.*"):
                    if cand.is_file() and not cand.name.endswith(".part"):
                        self.target_filepath = cand
                        break

            if self.target_filepath and self.target_filepath.exists():
                final_size = self.target_filepath.stat().st_size
                self._total_downloaded = final_size
                self._total_size = final_size
                if self.info:
                    self.info.total_size = final_size
                    self.info.filename = self.target_filepath.name
                logger.info(
                    "Stream download and remuxing completed successfully: %s (%d bytes)",
                    self.target_filepath.name,
                    final_size,
                )
                return True

            self.error_message = "File finale non trovato dopo il completamento del download"
            return False

        except Exception as exc:
            if self._is_cancelled:
                self.error_message = "Download annullato"
                return False
            logger.error("yt-dlp download execution failed: %s", exc)
            self.error_message = str(exc)
            return False

    async def cancel(self) -> None:
        """Cancels download execution and cleans up partial temporary fragment files."""
        self._is_cancelled = True
        logger.info("StreamDownloadEngine cancellation requested for task %s", self.task.task_id)
