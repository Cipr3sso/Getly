import asyncio
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
import urllib.parse

from core.network.session_pool import MultiInterfaceSessionPool
from core.cnl.models import CNLDecryptedPackage
from core.engine.downloader import DownloadEngine
from .task import DownloadTask, DownloadStatus
from .state_store import StateStore
from .categories import detect_category, get_category_subfolder, CATEGORY_TORRENTS
from core.extensions.manager import ExtensionManager

logger = logging.getLogger(__name__)


class QueueManager:
    """
    Manages the batch download queue, enforces concurrency semaphores,
    supports pause/resume/cancel lifecycles, and connects the CNL2 interception
    server to the multi-interface download engine.
    """

    def __init__(
        self,
        session_pool: MultiInterfaceSessionPool,
        state_store: StateStore,
        default_download_dir: Path,
        max_active_tasks: int = 3,
        workers_per_interface: int = 2,
        on_task_added: Optional[Callable[[DownloadTask], Any]] = None,
        extension_manager: Optional[ExtensionManager] = None,
    ):
        self.session_pool = session_pool
        self.state_store = state_store
        self.default_download_dir = Path(default_download_dir)
        self.max_active_tasks = max_active_tasks
        self.workers_per_interface = workers_per_interface
        self.on_task_added = on_task_added
        self.extension_manager = extension_manager

        self._task_queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
        self._tasks: Dict[str, DownloadTask] = {}
        self._active_engines: Dict[str, DownloadEngine] = {}
        self._semaphore = asyncio.Semaphore(max_active_tasks)
        self._manager_task: Optional[asyncio.Task] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        """Starts the queue processor and loads pending/interrupted tasks from storage."""
        if self._is_running:
            return

        if not self.session_pool.is_initialized:
            await self.session_pool.initialize()

        self._is_running = True
        self._manager_task = asyncio.create_task(self._process_queue())

        # Load all tasks from DB into memory cache
        all_db_tasks = self.state_store.get_all_tasks()
        for task in all_db_tasks:
            self._tasks[task.task_id] = task

        # Re-enqueue pending or interrupted downloading tasks
        pending = self.state_store.get_pending_tasks()
        logger.info("Loaded %d pending tasks from database", len(pending))
        for task in pending:
            if task.status != DownloadStatus.PAUSED:
                task.status = DownloadStatus.PENDING
                await self._task_queue.put(task)

    def get_all_tasks(self) -> List[DownloadTask]:
        """Returns all tracked download tasks (active, paused, completed, failed)."""
        # If in-memory tasks is populated, sort by created_at DESC
        if self._tasks:
            return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)
        return self.state_store.get_all_tasks()

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Retrieves a task by ID."""
        return self._tasks.get(task_id) or self.state_store.get_task(task_id)

    async def add_url(
        self,
        url: str,
        destination_dir: Optional[Path] = None,
        package_name: Optional[str] = None,
        filename: Optional[str] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
        category: Optional[str] = None,
        start_paused: bool = False,
        task_type: Optional[str] = None,
        selected_files: Optional[List[int]] = None,
        info_hash: Optional[str] = None,
        priority: int = 4,
    ) -> DownloadTask:
        """Adds a single URL to the download queue, organized into category subdirectories."""
        clean_filename = None
        if filename:
            clean_filename = os.path.basename(str(filename).replace("\\", "/"))

        clean_url = url.strip()

        # Check if an extension or streaming provider can resolve this URL into direct streams/files
        source_ip = None
        if hasattr(self, "session_pool") and self.session_pool:
            if getattr(self.session_pool, "primary_adapter", None):
                source_ip = self.session_pool.primary_adapter.ip
            elif getattr(self.session_pool, "all_adapters", None) and len(self.session_pool.all_adapters) > 0:
                source_ip = self.session_pool.all_adapters[0].ip

        resolved = None
        if self.extension_manager:
            try:
                resolved = self.extension_manager.resolve_url(clean_url, source_address=source_ip)
            except TypeError:
                resolved = self.extension_manager.resolve_url(clean_url)
            except Exception as exc:
                logger.warning("Error resolving URL through extensions: %s", exc)

        if not resolved:
            try:
                from core.extensions.engine import is_video_streaming_url, resolve_with_ytdlp
                if is_video_streaming_url(clean_url):
                    resolved = resolve_with_ytdlp(clean_url, source_address=source_ip)
            except Exception as exc:
                logger.debug("Streaming bridge resolution error: %s", exc)

        task_headers = None
        parsed_total_size = 0
        if resolved and (resolved.url or resolved.files):
            direct_url = resolved.url or (resolved.files[0].url if resolved.files else "")
            direct_filename = resolved.filename or (resolved.files[0].name if resolved.files else "")
            if direct_url:
                logger.info(
                    "Stream resolved '%s' -> '%s' (filename: %s)",
                    clean_url,
                    direct_url,
                    direct_filename,
                )
                clean_url = direct_url
                url = direct_url
                if not clean_filename and direct_filename:
                    clean_filename = direct_filename

                if resolved.headers:
                    task_headers = dict(resolved.headers)
                elif resolved.files and resolved.files[0].headers:
                    task_headers = dict(resolved.files[0].headers)

                if resolved.files and resolved.files[0].size:
                    parsed_total_size = resolved.files[0].size

                if task_headers:
                    for h_k, h_v in task_headers.items():
                        lk = h_k.lower()
                        if lk == "cookie" and not cookies:
                            cookies = h_v
                        elif lk == "user-agent" and not user_agent:
                            user_agent = h_v
                        elif lk == "referer" and not referer:
                            referer = h_v

        if "clen=" in clean_url:
            try:
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(clean_url).query)
                if "clen" in qs:
                    parsed_total_size = int(qs["clen"][0])
            except Exception:
                pass

        is_torrent = (
            task_type == "TORRENT"
            or clean_url.startswith("magnet:")
            or clean_url.lower().endswith(".torrent")
            or (clean_filename and clean_filename.lower().endswith(".torrent"))
            or category == CATEGORY_TORRENTS
        )
        resolved_task_type = "TORRENT" if is_torrent else (task_type or "HTTP")

        # Detect category if not explicitly given
        task_category = category or (CATEGORY_TORRENTS if is_torrent else detect_category(clean_filename or url))

        # Route to category subfolder if destination_dir not explicitly provided or is default
        if destination_dir is None or Path(destination_dir) == self.default_download_dir:
            dest = get_category_subfolder(self.default_download_dir, task_category)
        else:
            dest = Path(destination_dir)

        dest.mkdir(parents=True, exist_ok=True)

        initial_status = DownloadStatus.PAUSED if start_paused else DownloadStatus.PENDING

        task = DownloadTask(
            url=url,
            destination_dir=dest,
            package_name=package_name,
            filename=clean_filename,
            status=initial_status,
            total_size=parsed_total_size,
            cookies=cookies,
            referer=referer,
            user_agent=user_agent,
            headers=task_headers,
            category=task_category,
            task_type=resolved_task_type,
            selected_files=selected_files,
            info_hash=info_hash,
            priority=priority,
        )
        self.state_store.save_task(task)
        self._tasks[task.task_id] = task

        if not start_paused:
            await self._task_queue.put(task)
            logger.info("Queued task %s [%s] for URL: %s in category %s (dest: %s)", task.task_id, resolved_task_type, url, task_category, dest)
        else:
            logger.info("Added task %s [%s] in PAUSED state for URL: %s in category %s (dest: %s)", task.task_id, resolved_task_type, url, task_category, dest)

        if self.on_task_added:
            try:
                res = self.on_task_added(task)
                if asyncio.iscoroutine(res):
                    asyncio.create_task(res)
            except Exception as exc:
                logger.debug("Error in on_task_added callback: %s", exc)

        return task

    async def on_cnl_package(self, package: CNLDecryptedPackage) -> List[DownloadTask]:
        """
        Callback invoked by CNLServer when a decrypted container arrives.
        Automatically enqueues all contained URLs.
        """
        logger.info(
            "Processing CNL package '%s' with %d URLs",
            package.package_name,
            len(package.urls),
        )
        target_dir = self.default_download_dir
        if package.package_name:
            target_dir = target_dir / package.package_name

        tasks = []
        for url in package.urls:
            task = await self.add_url(
                url=url,
                destination_dir=target_dir,
                package_name=package.package_name,
            )
            tasks.append(task)
        return tasks

    async def pause_task(self, task_id: str) -> bool:
        """Pauses a task if downloading or queued."""
        task = self.get_task(task_id)
        if not task:
            return False

        task.status = DownloadStatus.PAUSED
        self.state_store.update_task_progress(task_id, task.downloaded_bytes, DownloadStatus.PAUSED)

        # Cancel running engine if currently executing
        engine = self._active_engines.get(task_id)
        if engine:
            await engine.cancel()
            logger.info("Paused active task %s", task_id)
            return True
        logger.info("Marked pending task %s as PAUSED", task_id)
        return True

    async def resume_task(self, task_id: str) -> bool:
        """Resumes a paused or failed task."""
        task = self.get_task(task_id)
        if not task:
            return False

        task.status = DownloadStatus.PENDING
        task.error_message = None
        self.state_store.update_task_status(task_id, DownloadStatus.PENDING)
        await self._task_queue.put(task)
        logger.info("Resumed task %s and re-queued", task_id)
        return True

    async def retry_task(self, task_id: str) -> bool:
        """Alias for resume_task on failed tasks."""
        return await self.resume_task(task_id)

    async def cancel_task(self, task_id: str) -> bool:
        """Cancels an active or pending task."""
        task = self.get_task(task_id)
        if not task:
            return False

        task.status = DownloadStatus.CANCELLED
        self.state_store.update_task_status(task_id, DownloadStatus.CANCELLED)

        engine = self._active_engines.get(task_id)
        if engine:
            await engine.cancel()
        logger.info("Cancelled task %s", task_id)
        return True

    async def delete_task(self, task_id: str) -> bool:
        """Cancels and deletes a task from storage and memory."""
        await self.cancel_task(task_id)
        self._tasks.pop(task_id, None)
        self.state_store.delete_task(task_id)
        logger.info("Deleted task %s", task_id)
        return True

    def get_torrent_files_status(self, task_id: str) -> List[Dict[str, Any]]:
        """Returns live file status for a torrent download task."""
        task = self.get_task(task_id)
        if not task:
            return []
        engine = self._active_engines.get(task_id)
        if engine and hasattr(engine, "update_files_status"):
            return engine.update_files_status()
        return task.files_status or []

    def set_file_priority(self, task_id: str, file_index: int, priority: int) -> bool:
        """Sets priority for an individual file in a torrent task on the fly."""
        task = self.get_task(task_id)
        if not task:
            return False

        if task.files_status:
            for f in task.files_status:
                if f.get("index") == file_index:
                    f["priority"] = priority
                    break

        if task.selected_files is not None:
            sel_set = set(task.selected_files)
            if priority == 0 and file_index in sel_set:
                sel_set.remove(file_index)
            elif priority > 0 and file_index not in sel_set:
                sel_set.add(file_index)
            task.selected_files = sorted(list(sel_set))

        self.state_store.save_task(task)

        engine = self._active_engines.get(task_id)
        if engine and hasattr(engine, "set_file_priority"):
            return engine.set_file_priority(file_index, priority)

        return True

    async def pause_all(self) -> None:
        """Pauses all active and pending tasks."""
        for task_id in list(self._tasks.keys()):
            task = self._tasks[task_id]
            if task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PENDING):
                await self.pause_task(task_id)

    def get_interface_transferred_bytes(self) -> Dict[str, int]:
        """
        Aggregates transferred bytes across all currently active download engines,
        indexed by adapter name.
        """
        totals: Dict[str, int] = {}
        for engine in list(self._active_engines.values()):
            per_engine = engine.get_adapter_bytes()
            for ad_name, b in per_engine.items():
                totals[ad_name] = totals.get(ad_name, 0) + b
        return totals

    async def _process_queue(self) -> None:
        """Worker loop that dequeues tasks and runs them with concurrency control."""
        while self._is_running:
            try:
                task = await self._task_queue.get()
            except asyncio.CancelledError:
                break

            # If task was paused or cancelled while waiting in the queue, skip it
            if task.status in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                self._task_queue.task_done()
                continue

            asyncio.create_task(self._run_task_guarded(task))

    async def _run_task_guarded(self, task: DownloadTask) -> None:
        async with self._semaphore:
            # If user paused or cancelled just before acquiring semaphore, abort
            if task.status in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                self._task_queue.task_done()
                return

            task.status = DownloadStatus.DOWNLOADING
            self.state_store.update_task_status(task.task_id, DownloadStatus.DOWNLOADING)

            def on_progress(downloaded: int, total: int, percentage: float) -> None:
                task.downloaded_bytes = downloaded
                task.total_size = total

            if task.task_type == "TORRENT" or task.url.strip().startswith("magnet:") or task.url.strip().lower().endswith(".torrent"):
                from core.engine.torrent_engine import TorrentDownloadEngine, TorrentSessionManager
                interface_ips = [a.ip for a in self.session_pool.all_adapters]
                session_mgr = TorrentSessionManager.get_instance(interface_ips=interface_ips)
                engine = TorrentDownloadEngine(
                    task=task,
                    session_manager=session_mgr,
                    on_progress=on_progress,
                )
            else:
                engine = DownloadEngine(
                    url=task.url,
                    destination_dir=task.destination_dir,
                    session_pool=self.session_pool,
                    custom_filename=task.filename,
                    workers_per_interface=self.workers_per_interface,
                    on_progress=on_progress,
                    cookies=task.cookies,
                    referer=task.referer,
                    user_agent=task.user_agent,
                    custom_headers=task.headers,
                )
            self._active_engines[task.task_id] = engine

            try:
                success = await engine.start()
                # Check if task was paused/cancelled during execution
                if task.status in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                    return

                if success:
                    task.status = DownloadStatus.COMPLETED
                    task.speed_bps = 0.0
                    task.upload_speed_bps = 0.0
                    task.seeds = 0
                    task.peers = 0
                    if engine.target_filepath:
                        task.filename = engine.target_filepath.name
                    task.total_size = engine.info.total_size if engine.info else task.total_size
                    task.downloaded_bytes = task.total_size
                    self.state_store.save_task(task)
                    logger.info("Task %s completed: %s", task.task_id, task.filename)
                else:
                    task.status = DownloadStatus.FAILED
                    task.error_message = "Download ended incompletely"
                    self.state_store.update_task_status(task.task_id, DownloadStatus.FAILED, task.error_message)
                    logger.warning("Task %s failed", task.task_id)
            except asyncio.CancelledError:
                # Handle cancellation without marking as failure if user paused/cancelled
                if task.status not in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                    task.status = DownloadStatus.FAILED
                    task.error_message = "Download cancelled"
                    self.state_store.update_task_status(task.task_id, DownloadStatus.FAILED, task.error_message)
            except Exception as exc:
                if task.status not in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                    task.status = DownloadStatus.FAILED
                    task.error_message = str(exc)
                    self.state_store.update_task_status(task.task_id, DownloadStatus.FAILED, str(exc))
                    logger.error("Exception running task %s: %s", task.task_id, exc)
            finally:
                self._active_engines.pop(task.task_id, None)
                self._task_queue.task_done()

    async def stop(self) -> None:
        """Stops the queue manager and cancels active downloads."""
        self._is_running = False
        if self._manager_task:
            self._manager_task.cancel()

        for task_id, engine in list(self._active_engines.items()):
            await engine.cancel()

        self._active_engines.clear()
        logger.info("QueueManager stopped.")

    async def clear_inactive_tasks(self) -> int:
        """Removes all COMPLETED, FAILED, CANCELLED, and 0-byte PAUSED tasks from memory and DB."""
        to_remove = []
        for task_id, task in list(self._tasks.items()):
            if task.status in (DownloadStatus.COMPLETED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                to_remove.append(task_id)
            elif task.status == DownloadStatus.PAUSED and (task.downloaded_bytes == 0 or task.downloaded_bytes is None):
                to_remove.append(task_id)

        for tid in to_remove:
            self._tasks.pop(tid, None)
            self._active_engines.pop(tid, None)

        deleted = self.state_store.delete_inactive_tasks()
        logger.info("Cleared %d inactive tasks from queue and store", deleted)
        return deleted
