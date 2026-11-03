import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any, Tuple
import urllib.parse

from core.network.session_pool import MultiInterfaceSessionPool
from core.cnl.models import CNLDecryptedPackage
from core.engine.downloader import DownloadEngine
from .task import DownloadTask, DownloadStatus
from .state_store import StateStore
from .categories import detect_category, get_category_subfolder, CATEGORY_TORRENTS, CATEGORY_ALIASES
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

        # Confirmation threshold and auto-categorization settings
        self.require_confirmation: bool = False
        self.confirmation_threshold_mb: int = 100
        self.auto_categorize: bool = True

        # Bandwidth attribution per network adapter for Getly's downloads
        self._last_adapter_bytes: Dict[str, int] = {}
        self._last_adapter_time: float = time.monotonic()
        self._adapter_speeds: Dict[str, Tuple[float, float]] = {}

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

        # Startup health check & file reconciliation
        await self.run_startup_health_check()

        # Re-enqueue pending or interrupted downloading tasks
        pending = self.state_store.get_pending_tasks()
        logger.info("Loaded %d pending tasks from database", len(pending))
        for task in pending:
            if task.status not in (DownloadStatus.PAUSED, DownloadStatus.DELETED, DownloadStatus.CANCELLED):
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

    async def run_startup_health_check(self) -> Dict[str, Any]:
        """
        Runs comprehensive startup validation:
        1. Tests SQLite database integrity (PRAGMA integrity_check).
        2. Validates download directory structure and write permissions.
        3. Reconciles existing files on disk vs Database:
           - Automatically indexes any files present in the download directory that are not yet tracked.
           - Verifies existing completed tasks on disk; flags missing files as 'File rimosso o spostato' in DELETED.
        """
        report: Dict[str, Any] = {
            "database_ok": False,
            "directories_ok": False,
            "indexed_files": 0,
            "missing_files_detected": 0,
        }

        # 1. Database Integrity Check
        try:
            report["database_ok"] = self.state_store.verify_integrity()
        except Exception as exc:
            logger.error("Startup database check failed: %s", exc)

        # 2. Directory Validation
        try:
            self.default_download_dir.mkdir(parents=True, exist_ok=True)
            test_file = self.default_download_dir / ".getly_health_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            report["directories_ok"] = True
        except Exception as exc:
            logger.error("Startup directory check failed: %s", exc)

        # 3. Check for existing completed tasks whose files are missing on disk
        for task in list(self._tasks.values()):
            if task.filename:
                target_path = Path(task.destination_dir) / task.filename
                if task.status == DownloadStatus.COMPLETED and not target_path.exists():
                    task.status = DownloadStatus.DELETED
                    task.error_message = "File rimosso o spostato"
                    self.state_store.update_task_status(task.task_id, DownloadStatus.DELETED, error="File rimosso o spostato")
                    report["missing_files_detected"] += 1
                    logger.info("Startup check: File for task %s missing on disk (%s). Moved to DELETED.", task.task_id, target_path)

        # 4. Scan download directory and subdirectories for untracked files
        known_file_paths = set()
        for task in self._tasks.values():
            if task.filename:
                try:
                    known_file_paths.add(str((Path(task.destination_dir) / task.filename).resolve()).lower())
                except Exception:
                    pass

        ignored_extensions = {
            ".getly", ".part", ".tmp", ".crdownload", ".aria2", ".torrent",
            ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".log", ".txt", ".json", ".lock", ".bak"
        }

        db_file = None
        if hasattr(self, "state_store") and getattr(self.state_store, "db_path", None):
            try:
                db_file = self.state_store.db_path.resolve()
            except Exception:
                pass

        try:
            dirs_to_scan = [self.default_download_dir]
            if self.default_download_dir.exists():
                for sub in self.default_download_dir.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        dirs_to_scan.append(sub)

            for d in dirs_to_scan:
                if not d.exists():
                    continue
                for entry in d.iterdir():
                    if not entry.is_file() or entry.name.startswith("."):
                        continue
                    if entry.suffix.lower() in ignored_extensions:
                        continue

                    try:
                        resolved_entry = entry.resolve()
                        if db_file and resolved_entry == db_file:
                            continue
                        entry_resolved = str(resolved_entry).lower()
                    except Exception:
                        continue

                    if entry_resolved not in known_file_paths:
                        try:
                            file_size = entry.stat().st_size
                            created_time = entry.stat().st_ctime
                        except Exception:
                            file_size = 0
                            created_time = time.time()

                        if file_size > 0:
                            if entry.parent.name.lower() == "torrents" or entry.suffix.lower() == ".torrent":
                                cat = CATEGORY_TORRENTS
                                t_type = "TORRENT"
                            else:
                                cat = detect_category(entry.name)
                                t_type = "HTTP"
                            imported_task = DownloadTask(
                                url=f"file://{entry.name}",
                                destination_dir=entry.parent,
                                filename=entry.name,
                                status=DownloadStatus.COMPLETED,
                                total_size=file_size,
                                downloaded_bytes=file_size,
                                category=cat,
                                task_type=t_type,
                                created_at=created_time,
                            )
                            self.state_store.save_task(imported_task)
                            self._tasks[imported_task.task_id] = imported_task
                            known_file_paths.add(entry_resolved)
                            report["indexed_files"] += 1
                            logger.info("Startup check: Indexed untracked file into manager: %s (%d bytes)", entry.name, file_size)
        except Exception as exc:
            logger.warning("Startup scan for untracked files failed: %s", exc)

        logger.info(
            "Startup Health Check completed: DB=%s, Dirs=%s, Indexed=%d, MissingMoved=%d",
            report["database_ok"],
            report["directories_ok"],
            report["indexed_files"],
            report["missing_files_detected"],
        )
        return report

    def check_files_status(self) -> None:
        """
        Verifies on-disk file existence for COMPLETED and DELETED tasks.
        If a COMPLETED task's file was moved or deleted on disk, marks it as DELETED with 'File rimosso o spostato'.
        If a DELETED task marked with 'File rimosso o spostato' has its file restored, clears the error message.
        """
        for task in list(self._tasks.values()):
            if not task.filename:
                continue
            try:
                target_path = Path(task.destination_dir) / task.filename
                file_exists = target_path.exists()
                if not file_exists and task.destination_dir:
                    alt_torrents = Path(task.destination_dir) / "Torrents" / task.filename
                    if alt_torrents.exists():
                        target_path = alt_torrents
                        file_exists = True
                        task.destination_dir = str(alt_torrents.parent)
                    elif hasattr(self, "default_download_dir"):
                        def_torrents = self.default_download_dir / "Torrents" / task.filename
                        if def_torrents.exists():
                            target_path = def_torrents
                            file_exists = True
                            task.destination_dir = str(def_torrents.parent)

                if task.status == DownloadStatus.COMPLETED and not file_exists:
                    logger.info("File %s moved or deleted on disk. Moving task %s to DELETED.", target_path, task.task_id)
                    task.status = DownloadStatus.DELETED
                    task.error_message = "File rimosso o spostato"
                    self.state_store.update_task_status(task.task_id, DownloadStatus.DELETED, error="File rimosso o spostato")
                elif task.status == DownloadStatus.DELETED and task.error_message == "File rimosso o spostato" and file_exists:
                    logger.info("File %s restored on disk for task %s.", target_path, task.task_id)
                    task.error_message = None
                    self.state_store.update_task_status(task.task_id, DownloadStatus.DELETED, error=None)
            except Exception:
                pass

    def get_adapter_transfer_rates(self) -> Dict[str, Tuple[float, float]]:
        """
        Returns real-time (download_speed_bps, upload_speed_bps) per network adapter
        generated strictly by Getly's active download engines.
        """
        now = time.monotonic()
        dt = now - self._last_adapter_time
        if dt < 0.1:
            return dict(self._adapter_speeds)

        active_engines = list(self._active_engines.values())
        if not active_engines:
            self._last_adapter_bytes.clear()
            self._adapter_speeds.clear()
            self._last_adapter_time = now
            return {}

        current_bytes: Dict[str, int] = {}
        for engine in active_engines:
            if hasattr(engine, "_adapter_downloaded"):
                for ad_name, b in getattr(engine, "_adapter_downloaded", {}).items():
                    current_bytes[ad_name] = current_bytes.get(ad_name, 0) + b

        new_speeds: Dict[str, Tuple[float, float]] = {}
        all_adapters = getattr(self.session_pool, "all_adapters", [])
        for ad in all_adapters:
            b = current_bytes.get(ad.name, 0)
            last_b = self._last_adapter_bytes.get(ad.name, 0)
            delta = b - last_b
            if delta > 0 and dt > 0:
                instant_speed = delta / dt
                old_spd = self._adapter_speeds.get(ad.name, (0.0, 0.0))[0]
                smoothed = 0.6 * instant_speed + 0.4 * old_spd if old_spd > 0 else instant_speed
                new_speeds[ad.name] = (smoothed, 0.0)
            else:
                # Check for Torrent engines that might attribute to primary
                torrent_dl = sum(
                    getattr(e.task, "speed_bps", 0.0)
                    for e in active_engines
                    if hasattr(e, "task") and not hasattr(e, "_adapter_downloaded")
                )
                torrent_ul = sum(
                    getattr(e.task, "upload_speed_bps", 0.0)
                    for e in active_engines
                    if hasattr(e, "task") and not hasattr(e, "_adapter_downloaded")
                )
                if getattr(ad, "is_primary", False) and (torrent_dl > 0 or torrent_ul > 0):
                    new_speeds[ad.name] = (torrent_dl, torrent_ul)
                else:
                    new_speeds[ad.name] = (0.0, 0.0)

        self._last_adapter_bytes = current_bytes
        self._last_adapter_time = now
        self._adapter_speeds = new_speeds
        return dict(self._adapter_speeds)

    async def add_url(
        self,
        url: str,
        destination_dir: Optional[Path] = None,
        package_name: Optional[str] = None,
        filename: Optional[str] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
        headers: Optional[dict] = None,
        category: Optional[str] = None,
        start_paused: bool = False,
        auto_start: Optional[bool] = None,
        total_size: int = 0,
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

        task_headers = dict(headers) if headers else None
        parsed_total_size = total_size or 0
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
                    task_headers = {**(task_headers or {}), **dict(resolved.headers)}
                elif resolved.files and resolved.files[0].headers:
                    task_headers = {**(task_headers or {}), **dict(resolved.files[0].headers)}

                if not parsed_total_size and resolved.files and resolved.files[0].size:
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

        if not parsed_total_size and "clen=" in clean_url:
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

        # For torrents, the category must ALWAYS be Torrents (cannot be overridden by Altro)
        if is_torrent:
            task_category = CATEGORY_TORRENTS
        else:
            # Detect category if not explicitly given, resolving aliases
            raw_category = category
            if raw_category and raw_category.strip().lower() in CATEGORY_ALIASES:
                raw_category = CATEGORY_ALIASES[raw_category.strip().lower()]
            task_category = raw_category or detect_category(clean_filename or url)

        # Route to category subfolder if destination_dir not explicitly provided or is default
        if destination_dir is None or Path(destination_dir) == self.default_download_dir:
            if self.auto_categorize:
                dest = get_category_subfolder(self.default_download_dir, task_category)
            else:
                dest = self.default_download_dir
        else:
            dest = Path(destination_dir)

        dest.mkdir(parents=True, exist_ok=True)

        # Check confirmation threshold
        needs_confirmation = self.require_confirmation
        threshold_bytes = self.confirmation_threshold_mb * 1024 * 1024
        if self.confirmation_threshold_mb > 0 and parsed_total_size > threshold_bytes:
            needs_confirmation = True

        if auto_start is False or needs_confirmation:
            initial_status = DownloadStatus.WAITING
        elif start_paused:
            initial_status = DownloadStatus.PAUSED
        else:
            initial_status = DownloadStatus.PENDING

        # Check if an existing task with the same URL already exists in non-downloading state
        for existing in list(self._tasks.values()):
            if existing.url == clean_url or existing.url == url:
                if existing.status in (DownloadStatus.FAILED, DownloadStatus.PAUSED, DownloadStatus.WAITING, DownloadStatus.CANCELLED, DownloadStatus.DELETED):
                    logger.info("Found existing task %s (status: %s) for URL %s. Updating and resuming.", existing.task_id, existing.status, url)
                    if cookies:
                        existing.cookies = cookies
                    if user_agent:
                        existing.user_agent = user_agent
                    if referer:
                        existing.referer = referer
                    if task_headers:
                        existing.headers = {**(existing.headers or {}), **task_headers}
                    if clean_filename and (not existing.filename or existing.filename.startswith("download")):
                        existing.filename = clean_filename
                    if parsed_total_size > 0 and (not existing.total_size or existing.total_size <= 0):
                        existing.total_size = parsed_total_size

                    existing.error_message = None
                    existing.status = DownloadStatus.PENDING if (auto_start is not False and not start_paused) else DownloadStatus.PAUSED
                    self.state_store.save_task(existing)
                    self._tasks[existing.task_id] = existing

                    if existing.status == DownloadStatus.PENDING:
                        await self._task_queue.put(existing)

                    if self.on_task_added:
                        try:
                            res = self.on_task_added(existing)
                            if asyncio.iscoroutine(res):
                                asyncio.create_task(res)
                        except Exception:
                            pass
                    return existing

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

        if initial_status == DownloadStatus.PENDING:
            await self._task_queue.put(task)
            logger.info("Queued task %s [%s] for URL: %s in category %s (dest: %s)", task.task_id, resolved_task_type, url, task_category, dest)
        elif initial_status == DownloadStatus.WAITING:
            logger.info("Added task %s [%s] in WAITING (pending confirmation) state for URL: %s in category %s (dest: %s)", task.task_id, resolved_task_type, url, task_category, dest)
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
        task.speed_bps = 0.0
        task.upload_speed_bps = 0.0
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

    async def delete_task(self, task_id: str, delete_files: bool = False, permanent: bool = True) -> bool:
        """
        Cancels and deletes a task.
        If permanent=True or delete_files=True:
            Purges from SQLite database and memory, optionally deleting files on disk.
        If permanent=False and delete_files=False:
            Soft-deletes the task to DownloadStatus.DELETED so it moves to the 'Eliminati' section.
        """
        task = self.get_task(task_id)
        if delete_files and task:
            try:
                target = getattr(task, "target_file", None)
                if target and target.exists():
                    target.unlink(missing_ok=True)
                    logger.info("Deleted physical file for task %s: %s", task_id, target)
            except Exception as exc:
                logger.warning("Error deleting file on disk for task %s: %s", task_id, exc)

        await self.cancel_task(task_id)

        if permanent or delete_files:
            self._tasks.pop(task_id, None)
            self.state_store.delete_task(task_id)
            logger.info("Permanently deleted task %s", task_id)
        else:
            if task:
                task.status = DownloadStatus.DELETED
                task.error_message = "Rimosso dalla lista"
                self.state_store.update_task_status(task_id, DownloadStatus.DELETED, error=task.error_message)
                self._tasks[task_id] = task
                logger.info("Soft-deleted task %s into 'Eliminati'", task_id)
        return True

    async def restore_task(self, task_id: str) -> bool:
        """Restores a soft-deleted task back to COMPLETED or PAUSED."""
        task = self.get_task(task_id)
        if not task:
            return False

        if task.total_size and task.downloaded_bytes >= task.total_size:
            target_status = DownloadStatus.COMPLETED
        else:
            target_status = DownloadStatus.PAUSED

        task.status = target_status
        task.error_message = None
        self.state_store.update_task_status(task_id, target_status, error=None)
        self._tasks[task_id] = task
        logger.info("Restored task %s to status %s", task_id, target_status)
        return True

    async def clear_deleted_tasks(self, delete_files: bool = False) -> int:
        """Permanently purges all tasks currently in 'Eliminati'."""
        deleted_ids = [t.task_id for t in list(self._tasks.values()) if t.status == DownloadStatus.DELETED]
        count = 0
        for tid in deleted_ids:
            if await self.delete_task(tid, delete_files=delete_files, permanent=True):
                count += 1
        return count

    async def update_task_url(self, task_id: str, new_url: str, restart: bool = True) -> bool:
        """
        Updates task.url for expired/temporary CDN links (e.g. YouTube, Google Drive, MEGA).
        Preserves previously written chunks and bytes on disk.
        If task was failed/error, clears error and restarts or pauses based on restart flag.
        """
        task = self.get_task(task_id)
        if not task:
            logger.warning("update_task_url: task %s not found", task_id)
            return False

        clean_url = new_url.strip()

        # Check if an extension or streaming bridge can resolve this new URL
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
            except Exception as exc:
                logger.debug("Error resolving updated URL through extensions: %s", exc)

        if not resolved:
            try:
                from core.extensions.engine import is_video_streaming_url, resolve_with_ytdlp
                if is_video_streaming_url(clean_url):
                    resolved = resolve_with_ytdlp(clean_url, source_address=source_ip)
            except Exception as exc:
                logger.debug("Streaming bridge resolution error: %s", exc)

        if resolved and (resolved.url or resolved.files):
            direct_url = resolved.url or (resolved.files[0].url if resolved.files else "")
            if direct_url:
                clean_url = direct_url
                if resolved.headers:
                    task.headers = dict(resolved.headers)
                elif resolved.files and resolved.files[0].headers:
                    task.headers = dict(resolved.files[0].headers)
                if task.headers:
                    for h_k, h_v in task.headers.items():
                        lk = h_k.lower()
                        if lk == "cookie":
                            task.cookies = h_v
                        elif lk == "user-agent":
                            task.user_agent = h_v
                        elif lk == "referer":
                            task.referer = h_v

        # Cancel active engine if running
        active_engine = self._active_engines.get(task_id)
        if active_engine:
            await active_engine.cancel()

        task.url = clean_url
        task.error_message = None

        if restart:
            task.status = DownloadStatus.PENDING
            self.state_store.save_task(task)
            await self._task_queue.put(task)
            logger.info("Updated URL for task %s and re-queued: %s", task_id, clean_url)
        else:
            task.status = DownloadStatus.PAUSED
            self.state_store.save_task(task)
            logger.info("Updated URL for task %s in PAUSED state: %s", task_id, clean_url)

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

            last_speed_time = time.time()
            last_downloaded = task.downloaded_bytes

            def on_progress(downloaded: int, total: int, percentage: float) -> None:
                nonlocal last_speed_time, last_downloaded
                now = time.time()
                dt = now - last_speed_time
                task.downloaded_bytes = downloaded
                task.total_size = total

                if dt >= 0.2:
                    delta = downloaded - last_downloaded
                    if delta >= 0:
                        instant_speed = delta / dt
                        if task.speed_bps > 0:
                            task.speed_bps = 0.6 * instant_speed + 0.4 * task.speed_bps
                        else:
                            task.speed_bps = instant_speed
                    last_speed_time = now
                    last_downloaded = downloaded

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
                    task_id=task.task_id,
                    state_store=self.state_store,
                    total_size=task.total_size,
                )
            self._active_engines[task.task_id] = engine

            try:
                success = await engine.start()
                # Check if task was paused/cancelled during execution
                if task.status in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                    return

                if success:
                    actual_downloaded = getattr(engine, "_total_downloaded", 0)
                    if hasattr(engine, "storage") and getattr(engine.storage, "filepath", None):
                        try:
                            fp = engine.storage.filepath
                            if fp.exists():
                                actual_downloaded = max(actual_downloaded, fp.stat().st_size)
                        except Exception:
                            pass

                    total = (engine.info.total_size if engine.info else 0) or actual_downloaded
                    if total <= 0:
                        task.status = DownloadStatus.FAILED
                        task.speed_bps = 0.0
                        task.upload_speed_bps = 0.0
                        task.error_message = getattr(engine, "error_message", None) or "Download terminato con 0 byte"
                        self.state_store.update_task_status(task.task_id, DownloadStatus.FAILED, task.error_message)
                        logger.warning("Task %s completed with 0 bytes: marking FAILED", task.task_id)
                    else:
                        task.status = DownloadStatus.COMPLETED
                        task.speed_bps = 0.0
                        task.upload_speed_bps = 0.0
                        task.seeds = 0
                        task.peers = 0
                        if engine.target_filepath:
                            task.filename = engine.target_filepath.name
                        task.total_size = total
                        task.downloaded_bytes = total
                        self.state_store.save_task(task)
                        logger.info("Task %s completed: %s (%d bytes)", task.task_id, task.filename, task.total_size)
                else:
                    task.status = DownloadStatus.FAILED
                    task.speed_bps = 0.0
                    task.upload_speed_bps = 0.0
                    task.error_message = getattr(engine, "error_message", None) or "Download ended incompletely"
                    self.state_store.update_task_status(task.task_id, DownloadStatus.FAILED, task.error_message)
                    logger.warning("Task %s failed: %s", task.task_id, task.error_message)
            except asyncio.CancelledError:
                task.speed_bps = 0.0
                task.upload_speed_bps = 0.0
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
