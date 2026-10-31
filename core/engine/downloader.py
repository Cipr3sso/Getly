import asyncio
import logging
from pathlib import Path
from typing import Optional, List, Dict, Callable
import aiohttp

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from .probe import ResourceProbe, RemoteResourceInfo, DEFAULT_HTTP_HEADERS, get_browser_headers
from .storage import StorageEngine
from .chunk_planner import ChunkPlanner, Chunk
from .worker import ChunkWorker
from .host_coordinator import HostCoordinator

logger = logging.getLogger(__name__)


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
    ):
        self.url = url
        self.destination_dir = Path(destination_dir)
        self.session_pool = session_pool
        self.custom_filename = custom_filename
        self.workers_per_interface = workers_per_interface
        self.on_progress = on_progress
        self.host_coordinator = host_coordinator or HostCoordinator.get_instance()
        self.host = HostCoordinator.extract_host(self.url)
        self.cookies = cookies
        self.referer = referer
        self.user_agent = user_agent
        self.custom_headers = custom_headers

        self.info: Optional[RemoteResourceInfo] = None
        self.storage: Optional[StorageEngine] = None
        self.planner: Optional[ChunkPlanner] = None
        self.workers: List[ChunkWorker] = []
        self._worker_tasks: List[asyncio.Task] = []
        self._is_cancelled = False
        self._total_downloaded = 0
        self._adapter_downloaded: Dict[str, int] = {}
        self._ip_restricted_fallback = False

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
                )

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

        self.planner = ChunkPlanner(
            total_size=self.info.total_size,
            active_interfaces_count=len(self.session_pool.active_adapters),
        )

        # 4. Spawn workers distributed across all active network adapters
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
                logger.warning(
                    "Multi-chunk download ended with incomplete chunks for %s. Automatically falling back to single-stream sequential download.",
                    filepath.name,
                )
                if self.storage and self.storage.is_open:
                    await self.storage.flush()
                return await self._download_single_stream(primary_session, filepath)
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
                total_size=self.info.total_size,
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
        try:
            await self.host_coordinator.wait_if_blocked(self.host)
            async with self.host_coordinator.connection_slot(self.host):
                async with session.get(self.url, headers=single_headers) as resp:
                    if resp.status not in (200, 206):
                        logger.error("Single-stream download failed with HTTP %d", resp.status)
                        return False

                    cl = resp.headers.get("Content-Length")
                    if cl and cl.isdigit() and (not self.info or self.info.total_size <= 0):
                        if self.info:
                            self.info.total_size = int(cl)

                    primary_adapter = getattr(self.session_pool, "primary_adapter", None)
                    if not primary_adapter and getattr(self.session_pool, "all_adapters", None):
                        primary_adapter = self.session_pool.all_adapters[0]

                    offset = 0
                    while not self._is_cancelled:
                        block = await resp.content.read(256 * 1024)
                        if not block:
                            break
                        await self.storage.write(offset, block)
                        offset += len(block)
                        self._on_bytes_received(len(block), primary_adapter)
                        if self.on_progress:
                            total = (self.info.total_size if self.info else 0) or offset
                            pct = (offset / total * 100.0) if total > 0 else 0.0
                            self.on_progress(offset, total, pct)

            return True
        except Exception as exc:
            logger.error("Single-stream download error: %s", exc)
            return False
        finally:
            if self.storage and self.storage.is_open:
                await self.storage.flush()
                await self.storage.close()

    async def cancel(self) -> None:
        """Cancels active workers and closes storage."""
        self._is_cancelled = True
        for w in self.workers:
            w.stop()
        for t in self._worker_tasks:
            t.cancel()
        if self.storage:
            await self.storage.close()
