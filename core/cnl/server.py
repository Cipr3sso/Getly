import asyncio
import logging
import os
from typing import Callable, Awaitable, Optional, List, Any, Dict
from aiohttp import web

from .models import CNL2Payload, CNLDecryptedPackage
from .decryptor import CNLDecryptor, CNLDecryptionError

logger = logging.getLogger(__name__)

PackageCallback = Callable[[CNLDecryptedPackage], Awaitable[None]]
DownloadCallback = Callable[[Dict[str, Any]], Awaitable[Any]]


@web.middleware
async def cnl_cors_middleware(request: web.Request, handler) -> web.Response:
    """
    Handles CORS preflight requests (OPTIONS) and injects permissive CORS headers
    to allow browsers on external origins (like filecrypt.co) to communicate with localhost:9666.
    """
    if request.method == "OPTIONS":
        response = web.Response(status=200)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Requested-With, Origin, Accept"
    )
    response.headers["Access-Control-Max-Age"] = "86400"
    return response


class CNLServer:
    """
    Lightweight local HTTP server adhering to Click'n'Load (CNL2) specification.
    Listens by default on 127.0.0.1:9666 to capture and decrypt links from Filecrypt, etc.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 9666,
        on_package_received: Optional[PackageCallback] = None,
        on_download_requested: Optional[DownloadCallback] = None,
        on_focus_requested: Optional[Callable[[], Any]] = None,
    ):
        self.host = host
        self.port = port
        self.on_package_received = on_package_received
        self.on_download_requested = on_download_requested
        self.on_focus_requested = on_focus_requested

        self._app = web.Application(middlewares=[cnl_cors_middleware])
        self._setup_routes()
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    def _setup_routes(self) -> None:
        self._app.router.add_get("/", self._handle_root)
        self._app.router.add_get("/flash/", self._handle_flash_info)
        self._app.router.add_get("/flash/check", self._handle_flash_check)
        self._app.router.add_get("/crossdomain.xml", self._handle_crossdomain)
        self._app.router.add_post("/flash/add", self._handle_flash_add)
        self._app.router.add_post("/flash/addcrypted2", self._handle_flash_addcrypted2)
        self._app.router.add_post("/api/add_download", self._handle_api_add_download)
        self._app.router.add_get("/api/focus", self._handle_api_focus)
        self._app.router.add_post("/api/focus", self._handle_api_focus)

    async def _handle_api_focus(self, request: web.Request) -> web.Response:
        """Endpoint to bring existing Getly window to front on double-click / single-instance launch."""
        logger.info("Received single-instance focus request via /api/focus")
        if self.on_focus_requested:
            try:
                res = self.on_focus_requested()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.error("Error in on_focus_requested callback: %s", exc)
        return web.json_response({
            "status": "ok",
            "message": "Getly window focus requested",
        })

    async def _handle_root(self, request: web.Request) -> web.Response:
        return web.Response(text="Getly - CNL2 Service Running\r\n")

    async def _handle_flash_info(self, request: web.Request) -> web.Response:
        return web.Response(text="JDownloader\r\n", content_type="text/plain")

    async def _handle_flash_check(self, request: web.Request) -> web.Response:
        # Filecrypt checks this endpoint to verify CNL client availability
        return web.Response(text="success\r\n", content_type="text/plain")

    async def _handle_crossdomain(self, request: web.Request) -> web.Response:
        xml_policy = (
            '<?xml version="1.0"?>\n'
            '<!DOCTYPE cross-domain-policy SYSTEM "http://www.macromedia.com/xml/dtds/cross-domain-policy.dtd">\n'
            "<cross-domain-policy>\n"
            '  <allow-access-from domain="*" />\n'
            "</cross-domain-policy>\n"
        )
        return web.Response(text=xml_policy, content_type="text/xml")

    async def _handle_api_add_download(self, request: web.Request) -> web.Response:
        """
        REST API endpoint for WebExtension / Native Messaging / CLI integration.
        Receives JSON:
        {
            "url": "https://example.com/file.zip",
            "filename": "file.zip",           # optional
            "referer": "https://example.com", # optional
            "cookies": "key=value; ...",      # optional
            "user_agent": "...",              # optional
            "destination_dir": "..."          # optional
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

        # Sanitize filename: strictly extract clean basename handling Windows and POSIX separators
        raw_filename = data.get("filename")
        if raw_filename and isinstance(raw_filename, str):
            clean_filename = os.path.basename(raw_filename.replace("\\", "/"))
            data["filename"] = clean_filename

        logger.info("API add_download received: %s (filename: %s)", url, data.get("filename"))

        if self.on_download_requested:
            try:
                task = await self.on_download_requested(data)
                if isinstance(task, dict):
                    task_id = task.get("task_id", "queued")
                    filename = task.get("filename", data.get("filename"))
                else:
                    task_id = getattr(task, "task_id", str(task) if task else "queued")
                    filename = getattr(task, "filename", data.get("filename"))
                return web.json_response({
                    "status": "ok",
                    "task_id": task_id,
                    "filename": filename,
                    "message": "Download successfully processed",
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

    async def _handle_flash_add(self, request: web.Request) -> web.Response:
        """Handles unencrypted flash/add requests."""
        data = await request.post()
        urls_raw = data.get("urls") or data.get("link") or ""
        package_name = data.get("package")

        urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]
        logger.info("Received unencrypted flash/add: %d URLs (package: %s)", len(urls), package_name)

        package = CNLDecryptedPackage(
            package_name=str(package_name) if package_name else None,
            urls=urls,
        )

        if self.on_package_received:
            try:
                await self.on_package_received(package)
            except Exception as exc:
                logger.error("Error in on_package_received callback: %s", exc)

        return web.Response(text="success\r\n", content_type="text/plain")

    async def _handle_flash_addcrypted2(self, request: web.Request) -> web.Response:
        """Handles standard encrypted CNL2 payloads."""
        data = await request.post()

        crypted = data.get("crypted")
        jk = data.get("jk")
        source = data.get("source")
        passwords = data.get("passwords")
        package_name = data.get("package")

        if not crypted or not jk:
            logger.warning("Rejected /flash/addcrypted2: Missing 'crypted' or 'jk' parameter")
            return web.Response(
                status=400,
                text="Missing required parameters: 'crypted' and 'jk'\r\n",
                content_type="text/plain",
            )

        payload = CNL2Payload(
            crypted=str(crypted),
            jk=str(jk),
            source=str(source) if source else None,
            passwords=str(passwords) if passwords else None,
            package=str(package_name) if package_name else None,
        )

        try:
            package = CNLDecryptor.decrypt(payload)
        except CNLDecryptionError as exc:
            logger.error("CNL2 decryption failed: %s", exc)
            return web.Response(
                status=500,
                text=f"Decryption error: {exc}\r\n",
                content_type="text/plain",
            )

        if self.on_package_received:
            try:
                await self.on_package_received(package)
            except Exception as exc:
                logger.error("Error in on_package_received callback: %s", exc)

        return web.Response(text="success\r\n", content_type="text/plain")

    async def start(self) -> None:
        """Starts the aiohttp CNL HTTP server."""
        if self._is_running:
            return

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        self._is_running = True
        logger.info("CNL2 Server listening on http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        """Stops the server gracefully."""
        if not self._is_running:
            return

        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._site = None
        self._is_running = False
        logger.info("CNL2 Server stopped.")

    async def __aenter__(self) -> "CNLServer":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
