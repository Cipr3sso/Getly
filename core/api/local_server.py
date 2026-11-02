import asyncio
import logging
import os
from pathlib import Path
from typing import Callable, Awaitable, Optional, List, Any, Dict
from aiohttp import web

from core.cnl.server import CNLServer, cnl_cors_middleware, PackageCallback, DownloadCallback
from core.cnl.models import CNLDecryptedPackage

logger = logging.getLogger(__name__)


class LocalHttpServer(CNLServer):
    """
    Local asynchronous HTTP server for Getly browser integration,
    supporting WebExtensions (Manifest V3), Click'n'Load (CNL2), FlashGot,
    and direct REST API calls.
    Listens by default on 127.0.0.1:9666 with CORS support.
    """

    def _setup_routes(self) -> None:
        # 1. Register standard CNL2 & base API routes
        super()._setup_routes()

        # 2. Browser Integration Endpoints
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_post("/add", self._handle_add)
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

        download_payload = {
            "url": url,
            "filename": clean_filename,
            "category": data.get("category"),
            "destination_dir": data.get("destination_dir"),
            "headers": headers,
            "referer": referer,
            "cookies": cookies,
            "user_agent": user_agent,
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
