import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any
import aiohttp

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from .probe import (
    ResourceProbe,
    RemoteResourceInfo,
    DEFAULT_HTTP_HEADERS,
    get_browser_headers,
    sanitize_headers_for_impersonation,
)
from .storage import StorageEngine
from .chunk_planner import ChunkPlanner, Chunk
from .host_coordinator import HostCoordinator
from .speed_limiter import SpeedLimiter

try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    HAVE_CURL_CFFI = True
except ImportError:
    HAVE_CURL_CFFI = False

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Raised when a download request fails with an HTTP error or protocol failure."""
    pass


from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .worker import ChunkWorker


def is_single_connection_stream(url: str) -> bool:
    """
    Detects if the URL points to single-connection/token-bound CDN streams
    (e.g., googlevideo.com or URLs with signed clen parameters) where concurrent connections
    cause token revocation and HTTP 403 Forbidden.
    """
    if not url:
        return False
    u = url.lower()
    if "googlevideo.com" in u:
        return True
    if "clen=" in u and ("sig=" in u or "lsig=" in u or "expire=" in u):
        return True
    return False


class DownloadEngine:
    """
    Coordinates multi-interface chunked downloads with dynamic work-stealing,
    positional I/O queue writing, and automatic fallback for IP-restricted hosts.
    """

    def __init__(
        self,
        url: str,
        destination_dir: Path,
        session_pool: MultiInterfaceSessionPool,
        custom_filename: Optional[str] = None,
        workers_per_interface: int = 2,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
        host_coordinator: Optional[HostCoordinator] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
        custom_headers: Optional[dict] = None,
        speed_limiter: Optional[SpeedLimiter] = None,
        task_id: Optional[str] = None,
        state_store: Optional[Any] = None,
        total_size: int = 0,
    ):
        self.url = url
        self.destination_dir = Path(destination_dir)
        self.session_pool = session_pool
        self.custom_filename = custom_filename
        self.total_size = total_size
        self.workers_per_interface = workers_per_interface
        self.on_progress = on_progress
        self.host_coordinator = host_coordinator or HostCoordinator.get_instance()
        self.host = HostCoordinator.extract_host(self.url)
        self.cookies = cookies
        self.referer = referer
        self.user_agent = user_agent
        self.custom_headers = custom_headers
        self.speed_limiter = speed_limiter or SpeedLimiter.get_instance()
        self.task_id = task_id
        self.state_store = state_store

        self.info: Optional[RemoteResourceInfo] = None
        self.storage: Optional[StorageEngine] = None
        self.planner: Optional[ChunkPlanner] = None
        self.workers: List[ChunkWorker] = []
        self._worker_tasks: List[asyncio.Task] = []
        self._is_cancelled = False
        self._total_downloaded = 0
        self._adapter_downloaded: Dict[str, int] = {}
        self._ip_restricted_fallback = False
        self.error_message: Optional[str] = None

    @property
    def target_filepath(self) -> Optional[Path]:
        if not self.info:
            return None
        filename = self.custom_filename or self.info.filename
        return self.destination_dir / filename

    async def start(self) -> bool:
        """Starts the download process end-to-end."""
        if not self.session_pool.is_initialized:
            await self.session_pool.initialize()

        # 1. Use the primary adapter (lowest metric internet gateway) to probe the resource
        primary_adapter, primary_session = self.session_pool.get_primary_adapter_and_session()

        is_single_conn = is_single_connection_stream(self.url)

        if not self.info:
            parsed_total_size = 0
            if "clen=" in self.url:
                try:
                    from urllib.parse import parse_qs, urlparse
                    qs = parse_qs(urlparse(self.url).query)
                    if "clen" in qs:
                        parsed_total_size = int(qs["clen"][0])
                except Exception:
                    pass

            if is_single_conn and (parsed_total_size > 0 or self.custom_filename):
                fname = self.custom_filename or "stream_video.mp4"
                self.info = RemoteResourceInfo(
                    url=self.url,
                    filename=fname,
                    total_size=parsed_total_size,
                    supports_ranges=True,
                    content_type="video/mp4",
                )
                logger.info(
                    "Single-connection stream detected for %s (size: %d bytes). Skipping remote probe.",
                    fname,
                    parsed_total_size,
                )
            else:
                logger.info(
                    "Probing remote resource via primary adapter '%s' [%s]: %s",
                    primary_adapter.name,
                    primary_adapter.ip,
                    self.url,
                )
                self.info = await ResourceProbe.probe(
                    primary_session,
                    self.url,
                    referer=self.referer,
                    cookies=self.cookies,
                    user_agent=self.user_agent,
                    custom_headers=self.custom_headers,
                    known_total_size=self.total_size,
                )
                if self.info:
                    if self.info.total_size <= 0 and self.total_size > 0:
                        self.info.total_size = self.total_size
                    if self.custom_filename and self.info.filename == "downloaded_file":
                        self.info.filename = self.custom_filename
                if self.info and self.info.url and self.info.url != self.url:
                    logger.info("Resource redirected: %s -> %s", self.url, self.info.url)
                    self.url = self.info.url
                    self.host = HostCoordinator.extract_host(self.url)
                    if self.task_id and self.state_store:
                        try:
                            self.state_store.update_task_url(self.task_id, self.url)
                        except Exception as exc:
                            logger.debug("Could not update task url in state store: %s", exc)

        filepath = self.target_filepath
        assert filepath is not None

        # 2. Initialize positional storage
        self.storage = StorageEngine(
            filepath=filepath,
            total_size=self.info.total_size,
            sparse=True,
        )
        await self.storage.open()

        # 3. Setup Chunk Planner or Single Stream
        if is_single_conn or not self.info.supports_ranges or self.info.total_size <= 0:
            logger.info(
                "Executing download in single-stream mode for %s (is_single_conn=%s, ranges=%s, size=%d)",
                filepath.name,
                is_single_conn,
                self.info.supports_ranges,
                self.info.total_size,
            )
            return await self._download_single_stream(primary_session, filepath)

        existing_chunks = None
        if self.state_store and self.task_id:
            try:
                loaded = self.state_store.load_chunks(self.task_id)
                if loaded and sum(c.size for c in loaded) == self.info.total_size:
                    existing_chunks = loaded
                    logger.info("Resuming %d existing chunks for task %s", len(existing_chunks), self.task_id)
            except Exception as exc:
                logger.debug("Could not load existing chunks for %s: %s", self.task_id, exc)

        self.planner = ChunkPlanner(
            total_size=self.info.total_size,
            active_interfaces_count=len(self.session_pool.active_adapters),
            existing_chunks=existing_chunks,
        )
        if self.state_store and self.task_id and not existing_chunks:
            try:
                self.state_store.save_chunks(self.task_id, list(self.planner.chunks.values()))
            except Exception as exc:
                logger.debug("Could not save initial chunks: %s", exc)

        # 4. Spawn workers distributed across all active network adapters
        from .worker import ChunkWorker
        active_adapters = self.session_pool.active_adapters
        worker_id = 0

        for adapter in active_adapters:
            session = self.session_pool.get_session_by_name(adapter.name)
            if not session:
                continue

            for _ in range(self.workers_per_interface):
                worker = ChunkWorker(
                    worker_id=worker_id,
                    adapter=adapter,
                    session=session,
                    url=self.url,
                    planner=self.planner,
                    storage=self.storage,
                    on_bytes_received=self._on_bytes_received,
                    on_ip_forbidden=self._on_ip_forbidden,
                    host_coordinator=self.host_coordinator,
                    cookies=self.cookies,
                    referer=self.referer,
                    user_agent=self.user_agent,
                    custom_headers=self.custom_headers,
                    speed_limiter=self.speed_limiter,
                )
                self.workers.append(worker)
                self._worker_tasks.append(asyncio.create_task(worker.run()))
                worker_id += 1

        logger.info(
            "Launched %d workers across %d network interfaces for %s",
            len(self.workers),
            len(active_adapters),
            filepath.name,
        )

        try:
            # 5. Wait for all workers to finish their assigned queues
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
            is_complete = self.planner.is_all_completed
            if is_complete:
                logger.info("Download completed successfully: %s (%d bytes)", filepath, self.info.total_size)
                return True
            else:
                if self._is_cancelled:
                    return False
                # Collect worker errors if any worker failed
                for w in self.workers:
                    if getattr(w, "last_error", None):
                        if not self.error_message:
                            self.error_message = w.last_error
                        break
                logger.warning(
                    "Multi-chunk download ended with incomplete chunks for %s. Automatically falling back to single-stream sequential download.",
                    filepath.name,
                )
                if self.storage and self.storage.is_open:
                    await self.storage.flush()
                single_success = await self._download_single_stream(primary_session, filepath)
                if not single_success and self._total_downloaded == 0:
                    if not self.error_message:
                        for w in self.workers:
                            if getattr(w, "last_error", None):
                                self.error_message = w.last_error
                                break
                return single_success
        finally:
            # 6. Always flush pending disk writes and close storage
            if self.storage and self.storage.is_open:
                await self.storage.flush()
                await self.storage.close()

    def _on_bytes_received(self, byte_count: int, adapter: NetworkAdapter) -> None:
        self._total_downloaded += byte_count
        if adapter and adapter.name:
            self._adapter_downloaded[adapter.name] = self._adapter_downloaded.get(adapter.name, 0) + byte_count
        if self.on_progress and self.planner:
            self.on_progress(
                self.planner.total_downloaded_bytes,
                self.planner.total_size,
                self.planner.progress_percentage,
            )

    def get_adapter_bytes(self) -> Dict[str, int]:
        """Returns the dictionary of downloaded bytes per network adapter."""
        return dict(self._adapter_downloaded)

    def _on_ip_forbidden(self, adapter: NetworkAdapter) -> None:
        """Triggered when an adapter encounters 401/403. Activates fallback."""
        if not self._ip_restricted_fallback:
            logger.warning(
                "Host appears to bind session to primary IP. Switching to single-interface fallback."
            )
            self._ip_restricted_fallback = True
            # Stop workers on secondary interfaces
            for w in self.workers:
                if w.adapter.name != self.session_pool.active_adapters[0].name:
                    w.stop()

    async def _download_single_stream(self, session: aiohttp.ClientSession, filepath: Path) -> bool:
        """Fallback for servers that do not support Range requests or when multi-chunk fails."""
        if not self.storage or not self.storage.is_open:
            self.storage = StorageEngine(
                filepath=filepath,
                total_size=self.info.total_size if self.info else 0,
                sparse=True,
            )
            await self.storage.open()
        single_headers = get_browser_headers(
            self.url,
            referer=self.referer,
            cookies=self.cookies,
            user_agent=self.user_agent,
            custom_headers=self.custom_headers,
        )
        # Strip Range header to ensure clean sequential download
        single_headers.pop("Range", None)
        single_headers.pop("range", None)

        try:
            await self.host_coordinator.wait_if_blocked(self.host)
            async with self.host_coordinator.connection_slot(self.host):
                async with session.get(self.url, headers=single_headers) as resp:
                    if resp.status not in (200, 206):
                        if resp.status in (401, 403):
                            logger.warning(
                                "Single-stream aiohttp challenged with HTTP %d. Attempting curl_cffi fallback with impersonate='chrome'...",
                                resp.status,
                            )
                            if HAVE_CURL_CFFI:
                                curl_ok = await self._download_single_stream_curl(filepath)
                                if curl_ok:
                                    return True
                            self.error_message = "HTTP 403 Forbidden (Cloudflare WAF)" if resp.status == 403 else f"HTTP {resp.status} Unauthorized"
                        else:
                            reason = getattr(resp, "reason", "")
                            self.error_message = f"HTTP {resp.status} {reason}".strip()
                        logger.error("Single-stream download failed with HTTP %d: %s", resp.status, self.error_message)
                        return False

                    cl = resp.headers.get("Content-Length")
                    if cl and cl.isdigit() and (not self.info or self.info.total_size <= 0):
                        if self.info:
                            self.info.total_size = int(cl)

                    # Guard: verify we didn't receive an HTML page when expecting binary/media/archive
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    ext = filepath.suffix.lower()
                    if ext in (".mp4", ".mkv", ".webm", ".avi", ".mov", ".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".msi", ".iso") and "text/html" in ctype:
                        self.error_message = f"Ricevuta pagina HTML anziché file multimediale ({ctype})"
                        logger.error("Single-stream received HTML content-type for binary file %s: %s", filepath.name, ctype)
                        return False

                    primary_adapter = getattr(self.session_pool, "primary_adapter", None)
                    if not primary_adapter and getattr(self.session_pool, "all_adapters", None):
                        primary_adapter = self.session_pool.all_adapters[0]

                    offset = 0
                    while not self._is_cancelled:
                        block = await resp.content.read(256 * 1024)
                        if not block:
                            break
                        if self.speed_limiter:
                            await self.speed_limiter.acquire(len(block))
                        await self.storage.write(offset, block)
                        offset += len(block)
                        self._on_bytes_received(len(block), primary_adapter)
                        if self.on_progress:
                            total = (self.info.total_size if self.info else 0) or offset
                            pct = (offset / total * 100.0) if total > 0 else 0.0
                            self.on_progress(offset, total, pct)

                    if offset == 0:
                        self.error_message = "Risorsa vuota (0 byte ricevuti)"
                        logger.warning("Single-stream download received 0 bytes for %s", self.url)
                        return False

                    if self.info and (self.info.total_size <= 0 or self.info.total_size < offset):
                        self.info.total_size = offset

            return True
        except Exception as exc:
            logger.error("Single-stream download error: %s", exc)
            if not self.error_message:
                self.error_message = str(exc)
            if HAVE_CURL_CFFI and self._total_downloaded == 0:
                logger.info("Attempting curl_cffi fallback after single-stream exception: %s", exc)
                curl_ok = await self._download_single_stream_curl(filepath)
                if curl_ok:
                    return True
            return False
        finally:
            if self.storage and self.storage.is_open:
                await self.storage.flush()
                await self.storage.close()

    async def _download_single_stream_curl(self, filepath: Path) -> bool:
        """Sequential single-stream download fallback via curl_cffi with TLS browser impersonation."""
        if not HAVE_CURL_CFFI:
            return False
        try:
            cf_headers = get_browser_headers(
                self.url,
                referer=self.referer,
                cookies=self.cookies,
                user_agent=self.user_agent,
                custom_headers=self.custom_headers,
            )
            cf_headers.pop("Range", None)
            cf_headers.pop("range", None)
            req_headers = sanitize_headers_for_impersonation(cf_headers)
            if self.user_agent:
                req_headers["User-Agent"] = self.user_agent

            await self.host_coordinator.wait_if_blocked(self.host)
            async with self.host_coordinator.connection_slot(self.host):
                async with CurlAsyncSession(impersonate="chrome") as curl_session:
                    curl_resp = await curl_session.get(self.url, headers=req_headers, stream=True, allow_redirects=True)
                    if curl_resp.status_code >= 400:
                        reason = getattr(curl_resp, "reason", "")
                        if curl_resp.status_code == 403:
                            self.error_message = "HTTP 403 Forbidden (Cloudflare WAF)"
                        else:
                            self.error_message = f"HTTP {curl_resp.status_code} {reason}".strip()
                        logger.error("curl_cffi single-stream fallback failed with HTTP %d: %s", curl_resp.status_code, self.error_message)
                        return False

                    if curl_resp.status_code in (200, 206):
                        cl = curl_resp.headers.get("content-length")
                        if cl and cl.isdigit() and (not self.info or self.info.total_size <= 0):
                            if self.info:
                                self.info.total_size = int(cl)

                        # Guard: verify we didn't receive an HTML page when expecting binary/media/archive
                        ctype = (curl_resp.headers.get("content-type") or "").lower()
                        ext = filepath.suffix.lower()
                        if ext in (".mp4", ".mkv", ".webm", ".avi", ".mov", ".zip", ".rar", ".7z", ".tar", ".gz", ".exe", ".msi", ".iso") and "text/html" in ctype:
                            self.error_message = f"Ricevuta pagina HTML anziché file multimediale ({ctype})"
                            logger.error("curl_cffi single-stream received HTML for binary file %s: %s", filepath.name, ctype)
                            return False

                        primary_adapter = getattr(self.session_pool, "primary_adapter", None)
                        if not primary_adapter and getattr(self.session_pool, "all_adapters", None):
                            primary_adapter = self.session_pool.all_adapters[0]

                        offset = 0
                        async for chunk_bytes in curl_resp.aiter_content(256 * 1024):
                            if self._is_cancelled:
                                return False
                            if chunk_bytes:
                                if self.speed_limiter:
                                    await self.speed_limiter.acquire(len(chunk_bytes))
                                await self.storage.write(offset, chunk_bytes)
                                offset += len(chunk_bytes)
                                self._on_bytes_received(len(chunk_bytes), primary_adapter)
                                if self.on_progress:
                                    total = (self.info.total_size if self.info else 0) or offset
                                    pct = (offset / total * 100.0) if total > 0 else 0.0
                                    self.on_progress(offset, total, pct)

                        if offset == 0:
                            self.error_message = "Risorsa vuota (0 byte ricevuti)"
                            return False

                        if self.info and (self.info.total_size <= 0 or self.info.total_size < offset):
                            self.info.total_size = offset

                        if hasattr(self.storage, "flush"):
                            await self.storage.flush()
                        logger.info("Successfully completed single-stream download via curl_cffi for %s (%d bytes)", filepath.name, offset)
                        return True
                    return False
        except Exception as exc:
            logger.error("curl_cffi single-stream fallback error: %s", exc)
            if not self.error_message:
                self.error_message = str(exc)
            return False

    async def cancel(self) -> None:
        """Cancels active workers and closes storage."""
        self._is_cancelled = True
        for w in self.workers:
            w.stop()
        for t in self._worker_tasks:
            t.cancel()
        if self.storage:
            await self.storage.close()
