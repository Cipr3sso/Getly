import asyncio
from dataclasses import dataclass, field
import logging
import math
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    chunk_id: int
    start_byte: int
    end_byte: int  # Inclusive
    bytes_downloaded: int = 0
    is_completed: bool = False
    failed_attempts: int = 0

    @property
    def size(self) -> int:
        return self.end_byte - self.start_byte + 1

    @property
    def remaining(self) -> int:
        return max(0, self.size - self.bytes_downloaded)


class ChunkPlanner:
    """
    Slices a file into adaptive chunks to minimize HTTP handshake overhead
    and prevent HTTP 429 (Too Many Requests), managing a dynamic work-stealing queue.
    """

    def __init__(
        self,
        total_size: int,
        min_chunk_size: Optional[int] = None,
        max_chunk_size: Optional[int] = None,
        active_interfaces_count: int = 1,
        existing_chunks: Optional[List[Chunk]] = None,
    ):
        self.total_size = total_size
        self.min_chunk_size = min_chunk_size
        self.max_chunk_size = max_chunk_size
        self.active_interfaces_count = max(1, active_interfaces_count)

        self.chunks: Dict[int, Chunk] = {}
        self.queue: asyncio.Queue[Chunk] = asyncio.Queue()

        if existing_chunks:
            self._load_existing_chunks(existing_chunks)
        else:
            self._generate_chunks()

    @classmethod
    def calculate_adaptive_chunk_size(
        cls, total_size: int, active_interfaces_count: int = 1
    ) -> int:
        """
        Dynamically calculates optimal chunk size based on total_size:
        - File < 20 MB: 1 chunk per active interface (or 4-8 MB chunks).
        - File between 20 MB and 500 MB: 16 MB to 32 MB chunks (targeting ~8-12 total chunks).
        - File > 500 MB: 32 MB to 64 MB chunks.
        Minimizes total consecutive HTTP requests to avoid rate limiting.
        """
        if total_size <= 0:
            return 4 * 1024 * 1024

        MB = 1024 * 1024

        if total_size < 20 * MB:
            # 1 chunk per interface, between 4 MB and 8 MB
            ifaces = max(1, active_interfaces_count)
            target_size = math.ceil(total_size / ifaces)
            return max(4 * MB, min(8 * MB, target_size))
        elif total_size <= 500 * MB:
            # Target ~8 to 12 total chunks (average 10)
            target_chunks = 10
            target_size = math.ceil(total_size / target_chunks)
            return max(16 * MB, min(32 * MB, target_size))
        else:
            # File > 500 MB: chunks between 32 MB and 64 MB
            target_chunks = max(12, math.ceil(total_size / (48 * MB)))
            target_size = math.ceil(total_size / target_chunks)
            return max(32 * MB, min(64 * MB, target_size))

    def _generate_chunks(self) -> None:
        """Divides total_size into adaptive chunks without excessive micro-slicing."""
        if self.total_size <= 0:
            # Single dummy chunk for unknown/stream size
            chunk = Chunk(chunk_id=0, start_byte=0, end_byte=0)
            self.chunks[0] = chunk
            self.queue.put_nowait(chunk)
            return

        if self.min_chunk_size is not None and self.max_chunk_size is not None:
            # User or test explicitly configured bounds
            target_chunks = 10
            chunk_size = max(
                self.min_chunk_size,
                min(self.max_chunk_size, self.total_size // target_chunks or self.min_chunk_size),
            )
        else:
            chunk_size = self.calculate_adaptive_chunk_size(
                self.total_size, self.active_interfaces_count
            )

        offset = 0
        chunk_id = 0
        while offset < self.total_size:
            end = min(offset + chunk_size - 1, self.total_size - 1)
            chunk = Chunk(chunk_id=chunk_id, start_byte=offset, end_byte=end)
            self.chunks[chunk_id] = chunk
            self.queue.put_nowait(chunk)
            chunk_id += 1
            offset = end + 1

        logger.debug(
            "Planned %d chunks of ~%.2f MB for total size %.2f MB",
            len(self.chunks),
            chunk_size / (1024 * 1024),
            self.total_size / (1024 * 1024),
        )

    def _load_existing_chunks(self, existing: List[Chunk]) -> None:
        """Restores state from existing chunks (for download resume)."""
        for c in existing:
            self.chunks[c.chunk_id] = c
            if not c.is_completed:
                self.queue.put_nowait(c)

    async def get_next_chunk(self) -> Optional[Chunk]:
        """Pulls the next available chunk for a worker. Returns None if all chunks finished."""
        if self.queue.empty():
            return None
        return await self.queue.get()

    def requeue_chunk(self, chunk: Chunk, max_attempts: int = 2) -> bool:
        """Puts a chunk back into the queue up to max_attempts. Returns False if abandoned."""
        chunk.failed_attempts += 1
        if not chunk.is_completed and chunk.failed_attempts <= max_attempts:
            self.queue.put_nowait(chunk)
            return True
        return False

    def mark_completed(self, chunk_id: int) -> None:
        """Marks a chunk as successfully downloaded and written."""
        if chunk_id in self.chunks:
            self.chunks[chunk_id].is_completed = True
            self.chunks[chunk_id].bytes_downloaded = self.chunks[chunk_id].size

    @property
    def total_downloaded_bytes(self) -> int:
        return sum(c.bytes_downloaded for c in self.chunks.values())

    @property
    def progress_percentage(self) -> float:
        if self.total_size <= 0:
            return 0.0
        return (self.total_downloaded_bytes / self.total_size) * 100.0

    @property
    def is_all_completed(self) -> bool:
        return all(c.is_completed for c in self.chunks.values())
