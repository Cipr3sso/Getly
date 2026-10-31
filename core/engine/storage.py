import asyncio
import ctypes
from ctypes import wintypes
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Optional, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Win32 Sparse File Constant: CTL_CODE(FILE_DEVICE_FILE_SYSTEM, 49, METHOD_BUFFERED, FILE_SPECIAL_ACCESS)
FSCTL_SET_SPARSE = 0x000900C4


@dataclass
class WriteCommand:
    offset: int
    data: bytes
    chunk_id: Optional[int] = None


class StorageEngine:
    """
    Dedicated I/O Queue Disk Writer.
    Implements sparse file preallocation and a single sequential writer thread
    to execute seek(offset) + write(data) safely without race conditions on Windows/Linux.
    """

    def __init__(
        self,
        filepath: Path,
        total_size: int,
        sparse: bool = True,
        queue_maxsize: int = 256,
        on_chunk_written: Optional[Callable[[int, int], None]] = None,
    ):
        self.filepath = Path(filepath)
        self.total_size = total_size
        self.sparse = sparse
        self.queue_maxsize = queue_maxsize
        self.on_chunk_written = on_chunk_written

        self._queue: Optional[asyncio.Queue] = None
        self._file = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._is_open = False
        self._bytes_written = 0

    @property
    def is_open(self) -> bool:
        return self._is_open

    @property
    def bytes_written(self) -> int:
        return self._bytes_written

    async def open(self) -> None:
        """Opens file handle, allocates sparse space, and starts I/O queue consumer."""
        if self._is_open:
            return

        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue(maxsize=self.queue_maxsize)

        # Ensure parent directory exists
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        # Open in read-write binary mode (create if not exists)
        mode = "r+b" if self.filepath.exists() else "w+b"
        self._file = open(self.filepath, mode)

        if self.sparse and self.total_size > 0:
            self._apply_sparse_allocation()

        self._is_open = True
        self._worker_task = asyncio.create_task(self._io_consumer())
        logger.debug("StorageEngine initialized for %s (%d bytes)", self.filepath, self.total_size)

    def _apply_sparse_allocation(self) -> None:
        """Preallocates file size using sparse files to prevent zero-filling wait time."""
        if sys.platform == "win32":
            try:
                import msvcrt
                hfile = wintypes.HANDLE(msvcrt.get_osfhandle(self._file.fileno()))
                bytes_returned = wintypes.DWORD(0)
                res = ctypes.windll.kernel32.DeviceIoControl(
                    hfile,
                    FSCTL_SET_SPARSE,
                    None, 0,
                    None, 0,
                    ctypes.byref(bytes_returned),
                    None,
                )
                if res != 0:
                    logger.debug("Sparse flag set on %s", self.filepath)
            except Exception as exc:
                logger.warning("Failed to set sparse flag on Windows: %s", exc)

        # Truncate to total_size
        try:
            self._file.truncate(self.total_size)
            self._file.flush()
        except OSError as exc:
            logger.warning("Truncate failed for %s: %s", self.filepath, exc)

    async def _io_consumer(self) -> None:
        """Dedicated consumer task that writes queued chunks to disk sequentially in executor."""
        while True:
            cmd: Optional[WriteCommand] = await self._queue.get()
            if cmd is None:
                # Sentinel received: stop worker
                self._queue.task_done()
                break

            try:
                # Offload blocking seek and write to threadpool
                await self._loop.run_in_executor(None, self._write_sync, cmd.offset, cmd.data)
                self._bytes_written += len(cmd.data)

                if self.on_chunk_written and cmd.chunk_id is not None:
                    try:
                        self.on_chunk_written(cmd.chunk_id, len(cmd.data))
                    except Exception as exc:
                        logger.error("Error in on_chunk_written callback: %s", exc)
            except Exception as exc:
                logger.error("Error writing block at offset %d: %s", cmd.offset, exc)
            finally:
                self._queue.task_done()

    def _write_sync(self, offset: int, data: bytes) -> None:
        """Executes seek and write synchronously within executor thread."""
        self._file.seek(offset)
        self._file.write(data)

    async def write(self, offset: int, data: bytes, chunk_id: Optional[int] = None) -> None:
        """
        Enqueues a block of data to be written at `offset`.
        Backpressures the network stream when queue is full.
        """
        if not self._is_open or not self._queue:
            raise RuntimeError("StorageEngine is not open")

        cmd = WriteCommand(offset=offset, data=data, chunk_id=chunk_id)
        await self._queue.put(cmd)

    async def flush(self) -> None:
        """Waits for all queued write commands to be completed and flushes file buffers."""
        if self._queue:
            await self._queue.join()
        if self._file and not self._file.closed:
            await self._loop.run_in_executor(None, self._file.flush)

    async def close(self) -> None:
        """Flushes pending writes, sends sentinel to consumer, and closes file."""
        if not self._is_open:
            return

        if self._queue:
            await self._queue.put(None)  # Send sentinel
            if self._worker_task:
                await self._worker_task

        if self._file and not self._file.closed:
            await self._loop.run_in_executor(None, self._file.flush)
            self._file.close()

        self._is_open = False
        logger.debug("StorageEngine closed for %s", self.filepath)

    async def __aenter__(self) -> "StorageEngine":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
