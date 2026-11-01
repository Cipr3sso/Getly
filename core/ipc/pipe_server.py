"""
Getly Windows Named Pipe IPC Server (\\.\\pipe\\GetlyEngine).
Provides Line-Delimited JSON (NDJSON) bidirectional asynchronous communication
between the headless download engine service and the graphical client UI.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import psutil

from core.engine.speed_limiter import SpeedLimiter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus

logger = logging.getLogger(__name__)

DEFAULT_PIPE_NAME = r"\\.\pipe\GetlyEngine"


def serialize_task(task: DownloadTask) -> Dict[str, Any]:
    """Serializes a DownloadTask into a JSON-compatible dictionary."""
    status_str = task.status.value if hasattr(task.status, "value") else str(task.status)
    return {
        "task_id": task.task_id,
        "url": task.url,
        "filename": task.filename,
        "destination_dir": str(task.destination_dir) if task.destination_dir else "",
        "status": status_str,
        "total_size": task.total_size,
        "downloaded_bytes": task.downloaded_bytes,
        "speed_bps": getattr(task, "speed_bps", 0.0),
        "upload_speed_bps": getattr(task, "upload_speed_bps", 0.0),
        "progress_percentage": round(task.progress_percentage, 2),
        "progress_ratio": round(task.progress_ratio, 4),
        "category": task.category,
        "task_type": task.task_type,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "seeds": getattr(task, "seeds", 0),
        "peers": getattr(task, "peers", 0),
        "pieces_total": getattr(task, "pieces_total", 0),
        "pieces_completed": getattr(task, "pieces_completed", 0),
        "priority": getattr(task, "priority", 4),
        "info_hash": getattr(task, "info_hash", None),
    }


class NamedPipeServer:
    """
    Asynchronous Windows Named Pipe Server supporting multiple concurrent clients,
    NDJSON protocol, and transparent UI client reconnections.
    """

    def __init__(
        self,
        queue_manager: QueueManager,
        session_pool: MultiInterfaceSessionPool,
        pipe_name: str = DEFAULT_PIPE_NAME,
    ):
        self.queue_manager = queue_manager
        self.session_pool = session_pool
        self.pipe_name = pipe_name

        self._connected_clients: Set[asyncio.StreamWriter] = set()
        self._servers: List[Any] = []
        self._is_running = False
        self._lock = asyncio.Lock()

        # Method Dispatch Table
        self._router: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "ping": self._rpc_ping,
            "tasks.list": self._rpc_tasks_list,
            "task.get": self._rpc_task_get,
            "task.add": self._rpc_task_add,
            "task.pause": self._rpc_task_pause,
            "task.resume": self._rpc_task_resume,
            "task.delete": self._rpc_task_delete,
            "task.update_url": self._rpc_task_update_url,
            "torrent.get_files": self._rpc_torrent_get_files,
            "torrent.set_priority": self._rpc_torrent_set_priority,
            "system.set_speed_limit": self._rpc_system_set_speed_limit,
            "system.get_stats": self._rpc_system_get_stats,
            "system.toggle_adapter": self._rpc_system_toggle_adapter,
        }

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def connected_client_count(self) -> int:
        return len(self._connected_clients)

    async def start(self) -> None:
        """Starts listening for pipe connections."""
        if self._is_running:
            return

        loop = asyncio.get_running_loop()
        self._is_running = True

        def protocol_factory():
            reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(
                reader,
                lambda r, w: asyncio.create_task(self._handle_client_connection(r, w)),
            )
            return protocol

        if hasattr(loop, "start_serving_pipe") and sys.platform == "win32":
            self._servers = await loop.start_serving_pipe(protocol_factory, self.pipe_name)
            logger.info("NamedPipeServer: listening on Windows Named Pipe: %s", self.pipe_name)
        else:
            # Fallback for non-Windows platforms (e.g. Unix Domain Sockets or Loopback TCP)
            if sys.platform != "win32" and not self.pipe_name.startswith(r"\\"):
                srv = await asyncio.start_unix_server(
                    lambda r, w: asyncio.create_task(self._handle_client_connection(r, w)),
                    path=self.pipe_name,
                )
                self._servers = [srv]
                logger.info("NamedPipeServer: listening on Unix socket: %s", self.pipe_name)
            else:
                # Fallback to local TCP port 19890 if named pipes unavailable
                srv = await asyncio.start_server(
                    lambda r, w: asyncio.create_task(self._handle_client_connection(r, w)),
                    host="127.0.0.1",
                    port=19890,
                )
                self._servers = [srv]
                logger.info("NamedPipeServer: fallback listening on TCP 127.0.0.1:19890")

    async def stop(self) -> None:
        """Stops the pipe server and disconnects active clients."""
        self._is_running = False
        async with self._lock:
            for writer in list(self._connected_clients):
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
            self._connected_clients.clear()

        for srv in self._servers:
            try:
                srv.close()
            except Exception:
                pass
        self._servers.clear()
        logger.info("NamedPipeServer: stopped.")

    async def broadcast(self, message: Dict[str, Any]) -> None:
        """Broadcasts an asynchronous event message (NDJSON) to all connected clients."""
        if not self._connected_clients:
            return

        payload = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        dead_clients = []

        async with self._lock:
            for writer in list(self._connected_clients):
                try:
                    writer.write(payload)
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError, OSError):
                    dead_clients.append(writer)
                except Exception as exc:
                    logger.debug("Error broadcasting to client: %s", exc)
                    dead_clients.append(writer)

            for w in dead_clients:
                self._connected_clients.discard(w)
                try:
                    w.close()
                except Exception:
                    pass

    async def _handle_client_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handles a newly connected client pipe session."""
        async with self._lock:
            self._connected_clients.add(writer)
        logger.info("NamedPipeServer: client connected (active clients: %d)", len(self._connected_clients))

        try:
            while self._is_running:
                line = await reader.readline()
                if not line:
                    break  # EOF / client disconnected

                line_str = line.decode("utf-8").strip()
                if not line_str:
                    continue

                response = await self._process_request_line(line_str)
                if response is not None:
                    response_payload = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
                    writer.write(response_payload)
                    await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:
            logger.warning("NamedPipeServer: client session exception: %s", exc)
        finally:
            async with self._lock:
                self._connected_clients.discard(writer)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("NamedPipeServer: client disconnected (remaining clients: %d)", len(self._connected_clients))

    async def _process_request_line(self, line: str) -> Optional[Dict[str, Any]]:
        """Parses and dispatches a single NDJSON line to the method router."""
        req_id = None
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                return {"id": None, "result": None, "error": "Request must be a JSON object"}

            req_id = data.get("id")
            method = data.get("method")
            params = data.get("params") or {}

            if not method:
                return {"id": req_id, "result": None, "error": "Missing 'method' field in request"}

            handler = self._router.get(method)
            if not handler:
                return {"id": req_id, "result": None, "error": f"Unknown RPC method: '{method}'"}

            res = handler(params)
            if asyncio.iscoroutine(res):
                res = await res

            return {"id": req_id, "result": res, "error": None}
        except json.JSONDecodeError as exc:
            return {"id": req_id, "result": None, "error": f"Invalid JSON format: {exc}"}
        except Exception as exc:
            logger.error("Error executing RPC method %s: %s", data.get("method") if "data" in locals() else "?", exc, exc_info=True)
            return {"id": req_id, "result": None, "error": str(exc)}

    # =========================================================================
    # RPC METHOD HANDLERS
    # =========================================================================

    def _rpc_ping(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"pong": True, "timestamp": time.time()}

    def _rpc_tasks_list(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Returns serialized list of all active, paused, completed, and failed tasks."""
        all_tasks = self.queue_manager.get_all_tasks()
        return [serialize_task(t) for t in all_tasks]

    def _rpc_task_get(self, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        task_id = params.get("task_id")
        if not task_id:
            raise ValueError("Missing 'task_id' parameter")
        task = self.queue_manager.get_task(task_id)
        if not task:
            return None
        return serialize_task(task)

    async def _rpc_task_add(self, params: Dict[str, Any]) -> Dict[str, Any]:
        url = params.get("url")
        if not url:
            raise ValueError("Missing 'url' parameter")

        dest_dir_str = params.get("dest_dir")
        dest_dir = Path(dest_dir_str) if dest_dir_str else None
        category = params.get("category")
        filename = params.get("filename")
        start_paused = bool(params.get("start_paused", False))
        priority = int(params.get("priority", 4))

        task = await self.queue_manager.add_url(
            url=url,
            destination_dir=dest_dir,
            filename=filename,
            category=category,
            start_paused=start_paused,
            priority=priority,
        )
        return serialize_task(task)

    async def _rpc_task_pause(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("task_id")
        if not task_id:
            raise ValueError("Missing 'task_id' parameter")
        success = await self.queue_manager.pause_task(task_id)
        return {"success": success, "task_id": task_id}

    async def _rpc_task_resume(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("task_id")
        if not task_id:
            raise ValueError("Missing 'task_id' parameter")
        success = await self.queue_manager.resume_task(task_id)
        return {"success": success, "task_id": task_id}

    async def _rpc_task_delete(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("task_id")
        if not task_id:
            raise ValueError("Missing 'task_id' parameter")
        delete_files = bool(params.get("delete_files", False))
        success = await self.queue_manager.delete_task(task_id, delete_files=delete_files)
        return {"success": success, "task_id": task_id, "delete_files": delete_files}

    async def _rpc_task_update_url(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("task_id")
        new_url = params.get("new_url")
        if not task_id or not new_url:
            raise ValueError("Missing 'task_id' or 'new_url' parameter")
        restart = bool(params.get("restart", True))

        success = await self.queue_manager.update_task_url(task_id, new_url, restart=restart)
        return {"success": success, "task_id": task_id, "new_url": new_url}

    def _rpc_torrent_get_files(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        task_id = params.get("task_id")
        if not task_id:
            raise ValueError("Missing 'task_id' parameter")
        return self.queue_manager.get_torrent_files_status(task_id)

    def _rpc_torrent_set_priority(self, params: Dict[str, Any]) -> Dict[str, Any]:
        task_id = params.get("task_id")
        file_index = params.get("file_index")
        priority = params.get("priority")
        if task_id is None or file_index is None or priority is None:
            raise ValueError("Missing 'task_id', 'file_index' or 'priority' parameter")
        success = self.queue_manager.set_file_priority(task_id, int(file_index), int(priority))
        return {
            "success": success,
            "task_id": task_id,
            "file_index": int(file_index),
            "priority": int(priority),
        }

    def _rpc_system_set_speed_limit(self, params: Dict[str, Any]) -> Dict[str, Any]:
        limit_bps = int(params.get("limit_bps", 0))
        limit_bps = max(0, limit_bps)

        # 1. Update HTTP Token Bucket Rate Limiter
        SpeedLimiter.get_instance().set_limit(limit_bps)

        # 2. Update BitTorrent Session Limiter
        try:
            from core.engine.torrent_engine import TorrentSessionManager, LIBTORRENT_AVAILABLE
            if LIBTORRENT_AVAILABLE:
                mgr = TorrentSessionManager.get_instance()
                mgr.set_download_rate_limit(limit_bps)
        except Exception as exc:
            logger.debug("NamedPipeServer: Could not apply limit to torrent session: %s", exc)

        return {"success": True, "limit_bps": limit_bps}

    def _rpc_system_toggle_adapter(self, params: Dict[str, Any]) -> Dict[str, Any]:
        adapter_name = params.get("adapter_name")
        enabled = bool(params.get("enabled", True))
        if not adapter_name:
            raise ValueError("Missing 'adapter_name' parameter")
        if enabled:
            success = self.session_pool.enable_adapter(adapter_name)
        else:
            success = self.session_pool.disable_adapter(adapter_name)
        return {"adapter_name": adapter_name, "enabled": enabled, "success": success}

    def _rpc_system_get_stats(self, params: Dict[str, Any]) -> Dict[str, Any]:
        # Aggregate speeds across all tasks
        all_tasks = self.queue_manager.get_all_tasks()
        active_tasks = [t for t in all_tasks if t.status == DownloadStatus.DOWNLOADING]

        total_dl = sum(getattr(t, "speed_bps", 0.0) for t in active_tasks)
        total_ul = sum(getattr(t, "upload_speed_bps", 0.0) for t in active_tasks)

        # Memory usage
        proc = psutil.Process()
        mem_info = proc.memory_info()
        sys_mem = psutil.virtual_memory()
        memory_stats = {
            "process_rss_bytes": mem_info.rss,
            "process_rss_mb": round(mem_info.rss / (1024 * 1024), 2),
            "system_total_bytes": sys_mem.total,
            "system_available_bytes": sys_mem.available,
            "system_percent": sys_mem.percent,
        }

        # Active Network Adapters
        adapters_data = []
        for ad in getattr(self.session_pool, "active_adapters", []):
            adapters_data.append({
                "name": ad.name,
                "friendly_name": ad.friendly_name,
                "ip": ad.ip,
                "is_primary": ad.is_primary,
                "speed_mbps": ad.speed_mbps,
                "is_vpn": getattr(ad, "is_vpn", False),
            })

        counts = {
            "total": len(all_tasks),
            "downloading": len(active_tasks),
            "completed": sum(1 for t in all_tasks if t.status == DownloadStatus.COMPLETED),
            "paused": sum(1 for t in all_tasks if t.status == DownloadStatus.PAUSED),
            "failed": sum(1 for t in all_tasks if t.status == DownloadStatus.FAILED),
        }

        return {
            "active_adapters": adapters_data,
            "vpn_shield_enabled": getattr(self.session_pool, "vpn_shield_enabled", True),
            "download_speed_bps": total_dl,
            "upload_speed_bps": total_ul,
            "speed_limit_bps": SpeedLimiter.get_instance().get_limit(),
            "memory": memory_stats,
            "task_counts": counts,
        }


# =============================================================================
# CLIENT CONNECTION HELPER
# =============================================================================

async def connect_pipe_client(
    pipe_name: str = DEFAULT_PIPE_NAME,
    timeout: float = 5.0,
) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """
    Connects to the Getly Windows Named Pipe server and returns (StreamReader, StreamWriter).
    Supports transparent cross-platform fallback for testing environments.
    """
    loop = asyncio.get_running_loop()

    if hasattr(loop, "create_pipe_connection") and sys.platform == "win32":
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.wait_for(
            loop.create_pipe_connection(lambda: protocol, pipe_name),
            timeout=timeout,
        )
        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        return reader, writer
    else:
        # Fallback Unix domain socket or local TCP
        if sys.platform != "win32" and not pipe_name.startswith(r"\\"):
            return await asyncio.wait_for(
                asyncio.open_unix_connection(pipe_name),
                timeout=timeout,
            )
        return await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", 19890),
            timeout=timeout,
        )
