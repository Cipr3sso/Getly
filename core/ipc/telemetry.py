"""
High-Performance Telemetry Dispatcher for Getly IPC.
Runs an asynchronous loop throttled at 10 Hz (100 ms) broadcasting
aggregate network statistics and delta-compressed task updates over Named Pipes.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.ipc.pipe_server import NamedPipeServer, serialize_task
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus

logger = logging.getLogger(__name__)


class TelemetryDispatcher:
    """
    10 Hz Telemetry Dispatcher that streams live speed metrics and delta-only task updates
    to connected UI clients via NDJSON frames.
    """

    def __init__(
        self,
        pipe_server: NamedPipeServer,
        queue_manager: QueueManager,
        session_pool: MultiInterfaceSessionPool,
        hz: float = 10.0,
    ):
        self.pipe_server = pipe_server
        self.queue_manager = queue_manager
        self.session_pool = session_pool
        self.interval = 1.0 / max(1.0, hz)

        self._task: Optional[asyncio.Task] = None
        self._is_running = False
        self._last_task_snapshots: Dict[str, Tuple[Any, int]] = {}

    @property
    def is_running(self) -> bool:
        return self._is_running

    def start(self) -> None:
        """Starts the periodic 10 Hz telemetry loop."""
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("TelemetryDispatcher: started at %.1f Hz (interval: %.3f s)", 1.0 / self.interval, self.interval)

    async def stop(self) -> None:
        """Stops the telemetry loop."""
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("TelemetryDispatcher: stopped.")

    async def _run_loop(self) -> None:
        """Main periodic broadcast loop."""
        while self._is_running:
            start_time = time.monotonic()
            try:
                await self._dispatch_tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("TelemetryDispatcher loop error: %s", exc)

            elapsed = time.monotonic() - start_time
            sleep_time = max(0.005, self.interval - elapsed)
            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    async def _dispatch_tick(self) -> None:
        """Collects metrics, computes task deltas, and broadcasts the frame if clients are connected."""
        # Only compute and broadcast when at least one client is connected
        if self.pipe_server.connected_client_count == 0:
            return

        all_tasks = self.queue_manager.get_all_tasks()
        active_tasks = [t for t in all_tasks if t.status == DownloadStatus.DOWNLOADING]

        total_dl = sum(getattr(t, "speed_bps", 0.0) for t in active_tasks)
        total_ul = sum(getattr(t, "upload_speed_bps", 0.0) for t in active_tasks)
        total_downloaded = sum(getattr(t, "downloaded_bytes", 0) for t in all_tasks)

        # Collect network interface stats
        interfaces_data = []
        for ad in getattr(self.session_pool, "all_adapters", []):
            interfaces_data.append({
                "name": ad.name,
                "friendly_name": ad.friendly_name,
                "ip": ad.ip,
                "is_up": ad.is_up,
                "speed_mbps": ad.speed_mbps,
                "is_vpn": getattr(ad, "is_vpn", False),
            })

        # Delta updates: only include tasks whose status or downloaded_bytes changed
        changed_tasks: List[Dict[str, Any]] = []
        current_task_ids = set()

        for task in all_tasks:
            current_task_ids.add(task.task_id)
            status_val = task.status.value if hasattr(task.status, "value") else str(task.status)
            curr_state = (status_val, task.downloaded_bytes)
            last_state = self._last_task_snapshots.get(task.task_id)

            if last_state != curr_state:
                self._last_task_snapshots[task.task_id] = curr_state
                changed_tasks.append(serialize_task(task))

        # Evict deleted tasks from snapshot cache
        for cached_id in list(self._last_task_snapshots.keys()):
            if cached_id not in current_task_ids:
                del self._last_task_snapshots[cached_id]

        frame = {
            "event": "tick",
            "timestamp": time.time(),
            "stats": {
                "download_speed_bps": total_dl,
                "upload_speed_bps": total_ul,
                "total_downloaded_bytes": total_downloaded,
                "interfaces": interfaces_data,
            },
            "tasks": changed_tasks,
        }

        await self.pipe_server.broadcast(frame)
