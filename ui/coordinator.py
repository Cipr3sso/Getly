import asyncio
import logging
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus
from core.cnl.models import CNLDecryptedPackage
from core.extensions.manager import ExtensionManager

logger = logging.getLogger(__name__)


def format_bytes(num_bytes: float) -> str:
    """Formats byte counts into human-readable strings (KB, MB, GB)."""
    if num_bytes < 0:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.1f} {unit}" if unit != "B" else f"{int(num_bytes)} B"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} PB"


def format_speed(bps: float) -> str:
    """Formats speed in bytes per second into human-readable MB/s or KB/s."""
    return f"{format_bytes(bps)}/s"


def format_eta(seconds: Optional[float]) -> str:
    """Formats estimated remaining seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0 or seconds > 86400 * 7:
        return "--:--"
    sec = int(seconds)
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    secs = sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class UICoordinator:
    """
    Reactive State & Metrics Coordinator.
    Bridges the core engines (QueueManager, MultiInterfaceSessionPool, StateStore)
    with the presentation layer. Operates at a relaxed 2 FPS (0.5s) when downloads are active,
    with strict Idle Tick Suppression: zero periodic UI updates when 0 downloads are active,
    ensuring 0ms click latency and instantaneous UI responsiveness.
    """

    def __init__(
        self,
        queue_manager: QueueManager,
        session_pool: MultiInterfaceSessionPool,
        tick_interval_sec: float = 0.5,
        extension_manager: Optional[ExtensionManager] = None,
    ):
        self.queue_manager = queue_manager
        self.session_pool = session_pool
        self.tick_interval_sec = tick_interval_sec
        self.ask_before_download: bool = True
        self.prompt_download_handler: Optional[Callable[[dict], Any]] = None
        self.focus_handler: Optional[Callable[[], Any]] = None

        self.extension_manager = (
            extension_manager
            or getattr(queue_manager, "extension_manager", None)
            or ExtensionManager()
        )
        if hasattr(self.queue_manager, "extension_manager") and not self.queue_manager.extension_manager:
            self.queue_manager.extension_manager = self.extension_manager

        # Computed live metrics
        self.interface_speeds: Dict[str, float] = {}  # adapter_name -> bytes/sec
        self.interface_percentages: Dict[str, float] = {}  # adapter_name -> % of total speed
        self.total_aggregate_speed: float = 0.0  # bytes/sec
        self.task_speeds: Dict[str, float] = {}  # task_id -> bytes/sec
        self.task_etas: Dict[str, str] = {}  # task_id -> formatted ETA
        self.intercepted_packages: List[CNLDecryptedPackage] = []

        # Internal state for differential calculations
        self._prev_interface_bytes: Dict[str, int] = {}
        self._prev_task_bytes: Dict[str, int] = {}
        self._last_tick_time: float = time.time()
        self._ticker_task: Optional[asyncio.Task] = None
        self._is_running: bool = False
        self._prev_has_active: bool = False

        # UI Listener callbacks
        self._listeners: Set[Callable[[], None]] = set()

    def add_listener(self, listener: Callable[[], None]) -> None:
        """Registers a callback to be called on each metrics tick."""
        self._listeners.add(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        """Unregisters a previously registered UI callback."""
        self._listeners.discard(listener)

    def notify_listeners(self) -> None:
        """Notifies all registered UI views/components of updated metrics."""
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as exc:
                logger.debug("Error in UI listener: %s", exc)

    @property
    def has_active_downloads(self) -> bool:
        """Returns True if at least one task is in DOWNLOADING state."""
        tasks = self.queue_manager.get_all_tasks()
        return any(t.status == DownloadStatus.DOWNLOADING for t in tasks)

    async def start(self) -> None:
        """Starts the coordinator and the metrics sampling ticker."""
        if self._is_running:
            return
        self._is_running = True
        self._last_tick_time = time.time()
        self._ticker_task = asyncio.create_task(self._metrics_ticker())
        logger.info("UICoordinator started with relaxed tick interval %.2fs", self.tick_interval_sec)

    async def stop(self) -> None:
        """Stops the coordinator ticker."""
        self._is_running = False
        if self._ticker_task:
            self._ticker_task.cancel()
            try:
                await self._ticker_task
            except asyncio.CancelledError:
                pass
        logger.info("UICoordinator stopped.")

    async def _metrics_ticker(self) -> None:
        """
        Async loop sampling throughput deltas at relaxed 2 FPS (0.5s).
        Idle Tick Suppression: If 0 downloads are in DOWNLOADING state,
        NO periodic listener callbacks are issued to the UI.
        """
        while self._is_running:
            try:
                await asyncio.sleep(self.tick_interval_sec)
                tasks = self.queue_manager.get_all_tasks()
                has_active = any(t.status == DownloadStatus.DOWNLOADING for t in tasks)

                if has_active:
                    self._compute_metrics()
                    self.notify_listeners()
                    self._prev_has_active = True
                else:
                    if self._prev_has_active or self.total_aggregate_speed > 0:
                        # Downloads just stopped or finished: zero metrics once and notify once
                        self._compute_metrics()
                        self.notify_listeners()
                        self._prev_has_active = False
                    # Idle: do NOT call notify_listeners(). Zero background UI updates!
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Error in metrics ticker: %s", exc)

    def _compute_metrics(self) -> None:
        """Calculates per-interface and per-task throughput from delta bytes."""
        now = time.time()
        dt = max(0.001, now - self._last_tick_time)
        self._last_tick_time = now

        # 1. Compute per-interface throughput
        current_if_bytes = self.queue_manager.get_interface_transferred_bytes()
        new_if_speeds: Dict[str, float] = {}
        total_speed = 0.0

        for adapter in self.session_pool.all_adapters:
            name = adapter.name
            curr = current_if_bytes.get(name, 0)
            prev = self._prev_interface_bytes.get(name, curr)
            delta = max(0, curr - prev)
            speed = delta / dt
            new_if_speeds[name] = speed
            total_speed += speed
            self._prev_interface_bytes[name] = curr

        self.interface_speeds = new_if_speeds
        self.total_aggregate_speed = total_speed

        # Calculate percentage contribution
        new_percentages: Dict[str, float] = {}
        for name, spd in new_if_speeds.items():
            if total_speed > 0:
                new_percentages[name] = min(100.0, (spd / total_speed) * 100.0)
            else:
                new_percentages[name] = 0.0
        self.interface_percentages = new_percentages

        # 2. Compute per-task speed and ETA
        tasks = self.queue_manager.get_all_tasks()
        new_task_speeds: Dict[str, float] = {}
        new_task_etas: Dict[str, str] = {}

        for task in tasks:
            tid = task.task_id
            curr_bytes = task.downloaded_bytes
            prev_bytes = self._prev_task_bytes.get(tid, curr_bytes)

            if task.status == DownloadStatus.DOWNLOADING:
                delta = max(0, curr_bytes - prev_bytes)
                spd = delta / dt
                new_task_speeds[tid] = spd
                task.speed_bps = spd

                # Estimate remaining time
                remaining = max(0, task.total_size - curr_bytes)
                if spd > 1024 and remaining > 0:
                    eta_sec = remaining / spd
                    new_task_etas[tid] = format_eta(eta_sec)
                elif remaining == 0:
                    new_task_etas[tid] = "00:00"
                else:
                    new_task_etas[tid] = "--:--"
            else:
                new_task_speeds[tid] = 0.0
                task.speed_bps = 0.0
                new_task_etas[tid] = "--:--"

            self._prev_task_bytes[tid] = curr_bytes

        self.task_speeds = new_task_speeds
        self.task_etas = new_task_etas

    # --- UI Actions ---

    async def add_download(
        self,
        url: str,
        destination_dir: Optional[Path] = None,
        filename: Optional[str] = None,
        category: Optional[str] = None,
        start_paused: bool = False,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
        task_type: Optional[str] = None,
        selected_files: Optional[List[int]] = None,
        info_hash: Optional[str] = None,
        priority: int = 4,
    ) -> DownloadTask:
        """Adds a new URL to the download queue and notifies UI immediately."""
        clean_url = url.strip()
        task = await self.queue_manager.add_url(
            url=clean_url,
            destination_dir=destination_dir,
            filename=filename,
            category=category,
            start_paused=start_paused,
            cookies=cookies,
            referer=referer,
            user_agent=user_agent,
            task_type=task_type,
            selected_files=selected_files,
            info_hash=info_hash,
            priority=priority,
        )
        self.notify_listeners()
        return task

    async def pause_task(self, task_id: str) -> bool:
        res = await self.queue_manager.pause_task(task_id)
        self.notify_listeners()
        return res

    async def resume_task(self, task_id: str) -> bool:
        res = await self.queue_manager.resume_task(task_id)
        self.notify_listeners()
        return res

    async def retry_task(self, task_id: str) -> bool:
        res = await self.queue_manager.retry_task(task_id)
        self.notify_listeners()
        return res

    async def cancel_task(self, task_id: str) -> bool:
        res = await self.queue_manager.cancel_task(task_id)
        self.notify_listeners()
        return res

    async def delete_task(self, task_id: str) -> bool:
        res = await self.queue_manager.delete_task(task_id)
        self.notify_listeners()
        return res

    # Aliases
    async def pause_download(self, task_id: str) -> bool:
        return await self.pause_task(task_id)

    async def resume_download(self, task_id: str) -> bool:
        return await self.resume_task(task_id)

    async def cancel_download(self, task_id: str) -> bool:
        return await self.cancel_task(task_id)

    async def pause_all(self) -> None:
        await self.queue_manager.pause_all()
        self.notify_listeners()

    async def clear_inactive_tasks(self) -> int:
        """Removes completed, failed, cancelled, and 0-byte paused tasks."""
        res = await self.queue_manager.clear_inactive_tasks()
        self.notify_listeners()
        return res

    async def update_task_url(self, task_id: str, new_url: str, restart: bool = True) -> bool:
        """Updates the download URL for expired CDN links, keeping downloaded chunks."""
        res = await self.queue_manager.update_task_url(task_id, new_url, restart=restart)
        self.notify_listeners()
        return res

    async def delete_task_and_file(self, task_id: str) -> bool:
        """Cancels/deletes the task and permanently removes the file on disk."""
        task = self.queue_manager.get_task(task_id)
        if task:
            try:
                target = task.target_file
                if target and target.exists():
                    target.unlink(missing_ok=True)
                    logger.info("Deleted physical file for task %s: %s", task_id, target)
            except Exception as exc:
                logger.warning("Error deleting file on disk for task %s: %s", task_id, exc)
        return await self.delete_task(task_id)

    def set_download_limit(self, limit_bps: int) -> None:
        """Configures global throughput limiter for both HTTP and BitTorrent engines."""
        from core.engine.speed_limiter import SpeedLimiter
        from config.settings import settings as app_settings
        SpeedLimiter.get_instance().set_limit(limit_bps)
        app_settings.download_limit_bytes_per_sec = limit_bps
        try:
            from core.engine.torrent_engine import TorrentSessionManager
            mgr = TorrentSessionManager.get_instance()
            if mgr:
                mgr.set_download_rate_limit(limit_bps)
        except Exception:
            pass

    def get_download_limit(self) -> int:
        """Returns the current throughput limit in bytes/sec (0 = unlimited)."""
        from core.engine.speed_limiter import SpeedLimiter
        return SpeedLimiter.get_instance().get_limit()

    def open_file(self, task: DownloadTask) -> bool:
        """Executes or opens the completed file using the system default application."""
        try:
            target = task.target_file
            if target and target.exists():
                if platform.system() == "Windows":
                    os.startfile(str(target))
                elif platform.system() == "Darwin":
                    subprocess.Popen(["open", str(target)])
                else:
                    subprocess.Popen(["xdg-open", str(target)])
                return True
            return False
        except Exception as exc:
            logger.error("Failed to execute/open file: %s", exc)
            return False

    def toggle_adapter(self, adapter_name: str, enable: bool) -> bool:
        """Enables or disables an adapter in the session pool."""
        if enable:
            return self.session_pool.enable_adapter(adapter_name)
        else:
            return self.session_pool.disable_adapter(adapter_name)

    def is_adapter_enabled(self, adapter_name: str) -> bool:
        return self.session_pool.is_adapter_enabled(adapter_name)

    def get_adapters(self) -> List[NetworkAdapter]:
        return self.session_pool.all_adapters

    def get_tasks(
        self,
        filter_status: Optional[str] = None,
        filter_category: Optional[str] = None,
    ) -> List[DownloadTask]:
        tasks = self.queue_manager.get_all_tasks()
        if filter_category and filter_category != "ALL":
            tasks = [t for t in tasks if (t.category or "Altro") == filter_category]

        if not filter_status or filter_status == "ALL":
            return tasks
        if filter_status == "ACTIVE":
            return [t for t in tasks if t.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PENDING)]
        if filter_status == "COMPLETED":
            return [t for t in tasks if t.status == DownloadStatus.COMPLETED]
        if filter_status == "PAUSED":
            return [t for t in tasks if t.status == DownloadStatus.PAUSED]
        if filter_status == "FAILED":
            return [t for t in tasks if t.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)]
        return tasks

    def open_containing_folder(self, task: DownloadTask) -> bool:
        """Opens the folder containing the downloaded file in the native file explorer."""
        try:
            folder = Path(task.destination_dir)
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)

            if platform.system() == "Windows":
                target_file = task.target_file
                if target_file and target_file.exists():
                    subprocess.Popen(f'explorer /select,"{target_file}"')
                else:
                    os.startfile(str(folder))
                return True
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(folder)])
                return True
            else:
                subprocess.Popen(["xdg-open", str(folder)])
                return True
        except Exception as exc:
            logger.error("Failed to open destination folder: %s", exc)
            return False

    def open_folder(self, folder_path: str | Path) -> bool:
        """Opens a directory in Windows Explorer or default file manager."""
        try:
            folder = Path(folder_path)
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)
            if platform.system() == "Windows":
                os.startfile(str(folder))
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
            return True
        except Exception as exc:
            logger.error("Failed to open folder: %s", exc)
            return False

    def get_torrent_files_status(self, task_id: str) -> List[Dict[str, Any]]:
        """Returns live file status for a torrent download task."""
        if hasattr(self.queue_manager, "get_torrent_files_status"):
            return self.queue_manager.get_torrent_files_status(task_id)
        task = self.queue_manager.get_task(task_id)
        return (task.files_status if task else None) or []

    def set_file_priority(self, task_id: str, file_index: int, priority: int) -> bool:
        """Sets priority for an individual file in a torrent task on the fly."""
        if hasattr(self.queue_manager, "set_file_priority"):
            return self.queue_manager.set_file_priority(task_id, file_index, priority)
        return False

    def copy_to_clipboard(self, text: str) -> None:
        """Copies text to system clipboard via pyperclip."""
        try:
            import pyperclip
            pyperclip.copy(text)
        except Exception as exc:
            logger.debug("Failed to copy to clipboard: %s", exc)

    def on_cnl_package_intercepted(self, package: CNLDecryptedPackage) -> None:
        """Records an intercepted CNL package."""
        self.intercepted_packages.insert(0, package)
        self.notify_listeners()

    @property
    def active_extensions_count(self) -> int:
        """Returns the count of currently enabled Gopeed extensions."""
        if not self.extension_manager:
            return 0
        return sum(1 for e in self.extension_manager.list_extensions() if e.enabled)

    def get_extensions(self) -> list:
        """Returns the list of all installed extensions."""
        return self.extension_manager.list_extensions() if self.extension_manager else []

    def enable_extension(self, ext_id: str) -> bool:
        res = self.extension_manager.enable_extension(ext_id) if self.extension_manager else False
        self.notify_listeners()
        return res

    def disable_extension(self, ext_id: str) -> bool:
        res = self.extension_manager.disable_extension(ext_id) if self.extension_manager else False
        self.notify_listeners()
        return res

    def delete_extension(self, ext_id: str) -> bool:
        res = self.extension_manager.delete_extension(ext_id) if self.extension_manager else False
        self.notify_listeners()
        return res

    def install_extension_from_url(self, url: str):
        if not self.extension_manager:
            raise RuntimeError("Extension manager not initialized")
        res = self.extension_manager.install_from_url(url)
        self.notify_listeners()
        return res

    def install_extension_from_file(self, file_path: Path):
        if not self.extension_manager:
            raise RuntimeError("Extension manager not initialized")
        p = Path(file_path)
        if p.suffix.lower() == ".zip":
            res = self.extension_manager.install_from_zip(p)
        else:
            res = self.extension_manager.install_from_js(p)
        self.notify_listeners()
        return res
