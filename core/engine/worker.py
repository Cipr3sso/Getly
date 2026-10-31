import asyncio
import email.utils
import logging
import random
from datetime import datetime, timezone
from typing import Optional, Callable
import aiohttp

from core.network.models import NetworkAdapter
from .probe import DEFAULT_HTTP_HEADERS, get_browser_headers
from .chunk_planner import Chunk, ChunkPlanner
from .storage import StorageEngine
from .host_coordinator import HostCoordinator
from .speed_limiter import SpeedLimiter

logger = logging.getLogger(__name__)


class ChunkWorker:
    """
    Dedicated worker coroutine bound to a specific network adapter and ClientSession.
    Pulls discrete chunks from ChunkPlanner, streams partial content, and pushes
    blocks to the StorageEngine I/O queue.
    Features robust Exponential Backoff, HTTP 429 / Retry-After handling, and connection pooling.
    """

    def __init__(
        self,
        worker_id: int,
        adapter: NetworkAdapter,
        session: aiohttp.ClientSession,
        url: str,
        planner: ChunkPlanner,
        storage: StorageEngine,
        read_buffer_size: int = 256 * 1024,  # 256KB buffer for reduced event loop overhead
        max_retries: int = 5,
        on_bytes_received: Optional[Callable[[int, NetworkAdapter], None]] = None,
        on_ip_forbidden: Optional[Callable[[NetworkAdapter], None]] = None,
        host_coordinator: Optional[HostCoordinator] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
        custom_headers: Optional[dict] = None,
        speed_limiter: Optional[SpeedLimiter] = None,
    ):
        self.worker_id = worker_id
        self.adapter = adapter
        self.session = session
        self.url = url
        self.planner = planner
        self.storage = storage
        self.read_buffer_size = read_buffer_size
        self.max_retries = max_retries
        self.on_bytes_received = on_bytes_received
        self.on_ip_forbidden = on_ip_forbidden
        self.host_coordinator = host_coordinator or HostCoordinator.get_instance()
        self.host = HostCoordinator.extract_host(self.url)
        self.cookies = cookies
        self.referer = referer
        self.user_agent = user_agent
        self.custom_headers = custom_headers
        self.speed_limiter = speed_limiter or SpeedLimiter.get_instance()

        self._is_stopped = False

    def stop(self) -> None:
        self._is_stopped = True

    @staticmethod
    def parse_retry_after(header_value: Optional[str]) -> Optional[float]:
        """
        Parses Retry-After header which can be either integer seconds or an HTTP-date.
        Returns float delay in seconds, or None if invalid.
        """
        if not header_value:
            return None
        val = header_value.strip()
        if val.isdigit():
            return float(val)
        try:
            dt = email.utils.parsedate_to_datetime(val)
            now = datetime.now(timezone.utc)
            return max(0.0, (dt - now).total_seconds())
        except Exception:
            return None

    async def run(self) -> None:
        """Main worker loop: pulls chunks and downloads them until queue is empty."""
        logger.debug("Worker %d [%s] started", self.worker_id, self.adapter.name)

        while not self._is_stopped:
            chunk = await self.planner.get_next_chunk()
            if chunk is None:
                # No more chunks left
                break

            success = await self._download_chunk(chunk)
            if not success and not self._is_stopped:
                # If all internal backoff retries failed, requeue chunk with cooldown
                requeued = self.planner.requeue_chunk(chunk)
                if not requeued:
                    logger.warning("Chunk %d exceeded maximum attempts and was not requeued", chunk.chunk_id)
                await asyncio.sleep(0.5)

        logger.debug("Worker %d [%s] finished", self.worker_id, self.adapter.name)

    async def _download_chunk(self, chunk: Chunk) -> bool:
        """
        Downloads a single chunk via HTTP Range request with internal retry loop,
        exponential backoff with jitter, and HTTP 429 Retry-After compliance.
        """
        end_offset = chunk.end_byte
        chunk_mb = chunk.size / (1024 * 1024)

        logger.info(
            "Worker %d [%s] preso in carico chunk %d (range %d-%d, %.2f MB)",
            self.worker_id,
            self.adapter.name,
            chunk.chunk_id,
            chunk.start_byte,
            chunk.end_byte,
            chunk_mb,
        )

        for attempt in range(self.max_retries):
            if self._is_stopped:
                return False

            start_offset = chunk.start_byte + chunk.bytes_downloaded
            if start_offset > end_offset:
                if hasattr(self.storage, "flush"):
                    await self.storage.flush()
                self.planner.mark_completed(chunk.chunk_id)
                downloaded_mb = chunk.bytes_downloaded / (1024 * 1024)
                logger.info(
                    "Worker %d [%s] completato e confermato su disco chunk %d (range %d-%d, %.2f MB scaricati)",
                    self.worker_id,
                    self.adapter.name,
                    chunk.chunk_id,
                    chunk.start_byte,
                    chunk.end_byte,
                    downloaded_mb,
                )
                return True

            headers = {
                **get_browser_headers(
                    self.url,
                    referer=self.referer,
                    cookies=self.cookies,
                    user_agent=self.user_agent,
                    custom_headers=self.custom_headers,
                ),
                "Range": f"bytes={start_offset}-{end_offset}",
            }

            try:
                await self.host_coordinator.wait_if_blocked(self.host)
                async with self.host_coordinator.connection_slot(self.host):
                    async with self.session.get(self.url, headers=headers, allow_redirects=True) as resp:
                        if str(resp.url) != self.url:
                            self.url = str(resp.url)
                        # 1. Handle HTTP 429 Too Many Requests
                        if resp.status == 429:
                            delay = self.parse_retry_after(resp.headers.get("Retry-After"))
                            if delay is None:
                                # Exponential backoff with jitter: min(2^attempt + random(0.5, 1.5), 30)
                                delay = min(2 ** attempt + random.uniform(0.5, 1.5), 30.0)
                            else:
                                delay = max(1.0, min(delay, 60.0))

                            # Trip circuit breaker globally for all workers targeting this host
                            self.host_coordinator.trip_circuit_breaker(self.host, delay)

                            logger.warning(
                                "Worker %d [%s] HTTP 429 Too Many Requests on chunk %d. "
                                "Host '%s' circuit breaker activated for %.2fs (attempt %d/%d)...",
                                self.worker_id,
                                self.adapter.name,
                                chunk.chunk_id,
                                self.host,
                                delay,
                                attempt + 1,
                                self.max_retries,
                            )
                            await asyncio.sleep(delay)
                            continue

                        # 2. Handle HTTP 401 / 403 Forbidden (e.g. IP lock)
                        if resp.status in (401, 403):
                            logger.warning(
                                "Worker %d [%s] received HTTP %d. IP may not be allowed or session is IP-bound.",
                                self.worker_id,
                                self.adapter.name,
                                resp.status,
                            )
                            if self.on_ip_forbidden:
                                self.on_ip_forbidden(self.adapter)
                            self.stop()
                            return False

                        # 3. Handle Transient Server Errors (500, 502, 503, 504)
                        if resp.status in (500, 502, 503, 504):
                            delay = self.parse_retry_after(resp.headers.get("Retry-After"))
                            if delay is None:
                                delay = min(2 ** attempt + random.uniform(0.5, 1.5), 30.0)
                            logger.warning(
                                "Worker %d [%s] HTTP %d on chunk %d. Retrying in %.2fs (attempt %d/%d)...",
                                self.worker_id,
                                self.adapter.name,
                                resp.status,
                                chunk.chunk_id,
                                delay,
                                attempt + 1,
                                self.max_retries,
                            )
                            await asyncio.sleep(delay)
                            continue

                        # 4. Handle unexpected non-success status
                        if resp.status not in (200, 206):
                            logger.warning(
                                "Worker %d [%s] unexpected HTTP %d for chunk %d (range %d-%d)",
                                self.worker_id,
                                self.adapter.name,
                                resp.status,
                                chunk.chunk_id,
                                start_offset,
                                end_offset,
                            )
                            return False

                        # 5. Stream content blocks
                        current_offset = start_offset
                        while not self._is_stopped:
                            block = await resp.content.read(self.read_buffer_size)
                            if not block:
                                break

                            block_len = len(block)
                            if self.speed_limiter:
                                await self.speed_limiter.acquire(block_len)

                            # Enqueue to positional storage
                            await self.storage.write(current_offset, block, chunk_id=chunk.chunk_id)
                            current_offset += block_len
                            chunk.bytes_downloaded += block_len

                            if self.on_bytes_received:
                                self.on_bytes_received(block_len, self.adapter)

                        # Verify full chunk was received
                        if current_offset >= end_offset + 1:
                            if hasattr(self.storage, "flush"):
                                await self.storage.flush()
                            self.planner.mark_completed(chunk.chunk_id)
                            downloaded_mb = chunk.bytes_downloaded / (1024 * 1024)
                            logger.info(
                                "Worker %d [%s] completato e confermato su disco chunk %d (range %d-%d, %.2f MB scaricati)",
                                self.worker_id,
                                self.adapter.name,
                                chunk.chunk_id,
                                chunk.start_byte,
                                chunk.end_byte,
                                downloaded_mb,
                            )
                            return True
                        else:
                            logger.warning(
                                "Worker %d [%s] stream disconnected mid-chunk %d (%d/%d bytes). Retrying remaining bytes...",
                                self.worker_id,
                                self.adapter.name,
                                chunk.chunk_id,
                                chunk.bytes_downloaded,
                                chunk.size,
                            )
                            delay = min(2 ** attempt + random.uniform(0.5, 1.5), 15.0)
                            await asyncio.sleep(delay)
                            continue

            except (
                aiohttp.ClientPayloadError,
                aiohttp.ClientConnectionError,
                asyncio.TimeoutError,
                OSError,
            ) as exc:
                delay = min(2 ** attempt + random.uniform(0.5, 1.5), 15.0)
                logger.warning(
                    "Worker %d [%s] connection/payload error on chunk %d: %s. "
                    "Retrying in %.2fs (attempt %d/%d)...",
                    self.worker_id,
                    self.adapter.name,
                    chunk.chunk_id,
                    exc,
                    delay,
                    attempt + 1,
                    self.max_retries,
                )
                await asyncio.sleep(delay)
                continue

        logger.error(
            "Worker %d [%s] failed chunk %d after %d attempts",
            self.worker_id,
            self.adapter.name,
            chunk.chunk_id,
            self.max_retries,
        )
        return False
