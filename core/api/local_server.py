import asyncio
import logging
import os
from pathlib import Path
from typing import Callable, Awaitable, Optional, List, Any, Dict
from aiohttp import web

from core.cnl.server import CNLServer, cnl_cors_middleware, PackageCallback, DownloadCallback
from core.cnl.models import CNLDecryptedPackage
from core.scheduler.task import DownloadStatus

logger = logging.getLogger(__name__)


class LocalHttpServer(CNLServer):
    """
    Local asynchronous HTTP server for Getly browser integration,
    supporting WebExtensions (Manifest V3), Click'n'Load (CNL2), FlashGot,
    and direct REST API calls.
    Listens by default on 127.0.0.1:9666 with CORS support.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9666,
        on_package_received: Optional[PackageCallback] = None,
        on_download_requested: Optional[DownloadCallback] = None,
        on_focus_requested: Optional[Callable[[], Any]] = None,
        queue_manager: Optional[Any] = None,
        on_batch_requested: Optional[Callable[[Dict[str, Any]], Awaitable[Any]]] = None,
    ):
        super().__init__(
            host=host,
            port=port,
            on_package_received=on_package_received,
            on_download_requested=on_download_requested,
            on_focus_requested=on_focus_requested,
        )
        self.queue_manager = queue_manager
        self.on_batch_requested = on_batch_requested

    def _setup_routes(self) -> None:
        # 1. Register standard CNL2 & base API routes
        super()._setup_routes()

        # 2. Browser Integration Endpoints
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_post("/add", self._handle_add)
        self._app.router.add_post("/add_batch", self._handle_add_batch)
        self._app.router.add_get("/jdcheck.js", self._handle_jdcheck)
        self._app.router.add_post("/flashgot", self._handle_flashgot)

    async def _handle_health(self, request: web.Request) -> web.Response:
        """
        Health-check endpoint for browser extensions and CLI tools.
        Returns: { "status": "ok", "version": "1.0.0", "app": "Getly" }
        """
        return web.json_response({
            "status": "ok",
            "version": "1.0.0",
            "app": "Getly",
        })

    async def _handle_jdcheck(self, request: web.Request) -> web.Response:
        """Standard JDownloader / Click'n'Load detection script for web pages."""
        return web.Response(
            text="jdownloader=true;\r\n",
            content_type="application/javascript",
        )

    async def _handle_flashgot(self, request: web.Request) -> web.Response:
        """Handles FlashGot integration requests."""
        return await self._handle_flash_add(request)

    async def _handle_add(self, request: web.Request) -> web.Response:
        """
        Endpoint for browser extensions to send downloads to Getly.
        Receives JSON:
        {
            "url": "https://example.com/file.mp4",
            "filename": "file.mp4",               # optional
            "headers": {                          # optional
                "Referer": "https://...",
                "User-Agent": "...",
                "Cookie": "..."
            },
            "category": "Video",                  # optional
            "destination_dir": "..."              # optional
        }
        """
        try:
            data = await request.json()
        except Exception as exc:
            return web.json_response(
                {"status": "error", "message": f"Invalid JSON payload: {exc}"},
                status=400,
            )

        url = data.get("url")
        if not url or not isinstance(url, str) or not (
            url.startswith("http://") or url.startswith("https://") or url.startswith("magnet:")
        ):
            return web.json_response(
                {"status": "error", "message": "Missing or invalid 'url' parameter (must be http://, https:// or magnet:)"},
                status=400,
            )

        headers = data.get("headers") or {}
        if not isinstance(headers, dict):
            headers = {}

        # Resolve referer, cookies, user_agent from headers or top-level keys
        referer = data.get("referer") or headers.get("Referer") or headers.get("referer")
        cookies = data.get("cookies") or headers.get("Cookie") or headers.get("cookie")
        user_agent = data.get("user_agent") or headers.get("User-Agent") or headers.get("user-agent")

        raw_filename = data.get("filename")
        clean_filename = None
        if raw_filename and isinstance(raw_filename, str):
            clean_filename = os.path.basename(raw_filename.replace("\\", "/"))
            if "?" in clean_filename:
                clean_filename = clean_filename.split("?")[0]

        raw_file_size = data.get("file_size") or data.get("total_size")
        parsed_size = 0
        if raw_file_size is not None:
            try:
                parsed_size = int(raw_file_size)
                if parsed_size < 0:
                    parsed_size = 0
            except (ValueError, TypeError):
                parsed_size = 0

        auto_start = data.get("auto_start", True)
        if isinstance(auto_start, str):
            auto_start = auto_start.lower() not in ("false", "0", "no")
        elif not isinstance(auto_start, bool):
            auto_start = True

        download_payload = {
            "url": url,
            "filename": clean_filename,
            "category": data.get("category"),
            "destination_dir": data.get("destination_dir"),
            "headers": headers,
            "referer": referer,
            "cookies": cookies,
            "user_agent": user_agent,
            "auto_start": auto_start,
            "file_size": parsed_size,
            "total_size": parsed_size,
        }

        logger.info("Local HTTP server received /add: %s (filename: %s)", url, clean_filename)

        if self.on_download_requested:
            try:
                task = await self.on_download_requested(download_payload)
                if isinstance(task, dict):
                    task_id = task.get("task_id", "queued")
                    filename = task.get("filename", clean_filename)
                else:
                    task_id = getattr(task, "task_id", str(task) if task else "queued")
                    filename = getattr(task, "filename", clean_filename)

                return web.json_response({
                    "status": "ok",
                    "task_id": task_id,
                    "filename": filename,
                    "message": "Download successfully queued in Getly",
                })
            except Exception as exc:
                logger.error("Error in on_download_requested: %s", exc)
                return web.json_response(
                    {"status": "error", "message": f"Failed to queue download: {exc}"},
                    status=500,
                )

        return web.json_response({
            "status": "ok",
            "task_id": "received_no_handler",
            "message": "Download received but no queue manager is attached",
        })

    async def _handle_add_batch(self, request: web.Request) -> web.Response:
        """
        Endpoint for batch adding downloads from browser extensions, link grabbers, or batch dialog.
        Receives JSON:
        {
            "items": [
                {
                    "url": "https://example.com/file1.zip",
                    "filename": "file1.zip",
                    "file_size": 1048576,
                    "headers": { ... },
                    "category": "Archivi",
                    "destination_dir": "..."
                }
            ],
            "auto_start": true
        }
        Returns:
        {
            "success": true,
            "count": N,
            "tasks": [
                {
                    "task_id": "...",
                    "url": "https://example.com/file1.zip",
                    "filename": "file1.zip",
                    "status": "DOWNLOADING"
                }
            ]
        }
        """
        try:
            data = await request.json()
        except Exception as exc:
            return web.json_response(
                {"status": "error", "message": f"Invalid JSON payload: {exc}"},
                status=400,
            )

        if not isinstance(data, dict):
            return web.json_response(
                {"status": "error", "message": "Payload must be a JSON object"},
                status=400,
            )

        items = data.get("items")
        if items is None or not isinstance(items, list):
            return web.json_response(
                {"status": "error", "message": "Missing or invalid 'items' parameter (must be a list)"},
                status=400,
            )

        batch_auto_start = data.get("auto_start", True)
        if isinstance(batch_auto_start, str):
            batch_auto_start = batch_auto_start.lower() not in ("false", "0", "no")
        elif not isinstance(batch_auto_start, bool):
            batch_auto_start = True

        logger.info("Local HTTP server received /add_batch with %d items (auto_start=%s)", len(items), batch_auto_start)

        tasks = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url or not isinstance(url, str) or not (
                url.startswith("http://") or url.startswith("https://") or url.startswith("magnet:")
            ):
                continue

            headers = item.get("headers") or {}
            if not isinstance(headers, dict):
                headers = {}

            referer = item.get("referer") or headers.get("Referer") or headers.get("referer")
            cookies = item.get("cookies") or headers.get("Cookie") or headers.get("cookie")
            user_agent = item.get("user_agent") or headers.get("User-Agent") or headers.get("user-agent")

            raw_filename = item.get("filename")
            clean_filename = None
            if raw_filename and isinstance(raw_filename, str):
                clean_filename = os.path.basename(raw_filename.replace("\\", "/"))
                if "?" in clean_filename:
                    clean_filename = clean_filename.split("?")[0]

            raw_file_size = item.get("file_size") or item.get("total_size")
            parsed_size = 0
            if raw_file_size is not None:
                try:
                    parsed_size = int(raw_file_size)
                    if parsed_size < 0:
                        parsed_size = 0
                except (ValueError, TypeError):
                    parsed_size = 0

            item_auto_start = item.get("auto_start", batch_auto_start)
            if isinstance(item_auto_start, str):
                item_auto_start = item_auto_start.lower() not in ("false", "0", "no")
            elif not isinstance(item_auto_start, bool):
                item_auto_start = batch_auto_start

            category = item.get("category")
            dest_dir = item.get("destination_dir")

            download_payload = {
                "url": url,
                "filename": clean_filename,
                "category": category,
                "destination_dir": dest_dir,
                "headers": headers,
                "referer": referer,
                "cookies": cookies,
                "user_agent": user_agent,
                "auto_start": item_auto_start,
                "file_size": parsed_size,
                "total_size": parsed_size,
            }

            if self.queue_manager:
                try:
                    task = await self.queue_manager.add_url(
                        url=url,
                        destination_dir=dest_dir,
                        filename=clean_filename,
                        cookies=cookies,
                        referer=referer,
                        user_agent=user_agent,
                        headers=headers,
                        category=category,
                        auto_start=item_auto_start,
                        total_size=parsed_size,
                    )
                    t_id = getattr(task, "task_id", str(task))
                    t_name = getattr(task, "filename", clean_filename) or clean_filename or ""
                    t_stat = getattr(task, "status", DownloadStatus.PENDING)
                    status_str = t_stat.value if hasattr(t_stat, "value") else str(t_stat)
                    tasks.append({
                        "task_id": str(t_id),
                        "url": url,
                        "filename": str(t_name),
                        "status": str(status_str),
                    })
                except Exception as exc:
                    logger.error("Failed to add task to queue_manager in /add_batch: %s", exc)
            elif self.on_download_requested:
                try:
                    task = await self.on_download_requested(download_payload)
                    if isinstance(task, dict):
                        t_id = task.get("task_id", f"task_{len(tasks)+1}")
                        t_name = task.get("filename", clean_filename) or clean_filename or ""
                        status_str = task.get("status", "DOWNLOADING")
                    else:
                        t_id = getattr(task, "task_id", str(task) if task else f"task_{len(tasks)+1}")
                        t_name = getattr(task, "filename", clean_filename) or clean_filename or ""
                        t_stat = getattr(task, "status", "DOWNLOADING")
                        status_str = t_stat.value if hasattr(t_stat, "value") else str(t_stat)
                    tasks.append({
                        "task_id": str(t_id),
                        "url": url,
                        "filename": str(t_name),
                        "status": str(status_str),
                    })
                except Exception as exc:
                    logger.error("Failed to add task via on_download_requested in /add_batch: %s", exc)
            else:
                tasks.append({
                    "task_id": f"batch_{len(tasks)+1}",
                    "url": url,
                    "filename": clean_filename or "",
                    "status": "QUEUED",
                })

        return web.json_response({
            "success": True,
            "count": len(tasks),
            "tasks": tasks,
        })
