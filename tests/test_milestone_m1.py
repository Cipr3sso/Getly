import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import aiohttp
from aiohttp import web

from core.engine.downloader import DownloadEngine, DownloadError
from core.engine.probe import (
    get_browser_headers,
    sanitize_headers_for_impersonation,
    RemoteResourceInfo,
)
from core.engine.worker import ChunkWorker
from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadStatus, DownloadTask
from core.ipc.pipe_server import NamedPipeServer


def test_sanitize_headers_for_impersonation():
    """Verifies that headers conflicting with curl_cffi TLS impersonation profile are stripped."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0",
        "Sec-Ch-Ua": '"Chromium";v="124", "Not.A/Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Cookie": "cf_clearance=secret_clearance_token; session=auth_123",
        "Referer": "https://protected-site.com/gallery",
        "Range": "bytes=1048576-2097151",
        "Accept": "*/*",
    }

    sanitized = sanitize_headers_for_impersonation(headers)

    # Must strip discordant browser client hints and user-agent
    assert "User-Agent" not in sanitized
    assert "user-agent" not in sanitized
    assert "Sec-Ch-Ua" not in sanitized
    assert "Sec-Ch-Ua-Mobile" not in sanitized
    assert "Sec-Ch-Ua-Platform" not in sanitized

    # Must strictly preserve Cookie (including cf_clearance), Referer, Range, and Accept
    assert sanitized["Cookie"] == "cf_clearance=secret_clearance_token; session=auth_123"
    assert sanitized["Referer"] == "https://protected-site.com/gallery"
    assert sanitized["Range"] == "bytes=1048576-2097151"
    assert sanitized["Accept"] == "*/*"


def test_get_browser_headers_suppresses_hardcoded_sec_ch_ua():
    """Verifies get_browser_headers does not inject hardcoded Sec-Ch-Ua if authentic custom headers are provided."""
    # 1. Without custom headers: defaults to Chromium headers with Sec-Ch-Ua
    headers_default = get_browser_headers("https://example.com/file.zip")
    assert "Sec-Ch-Ua" in headers_default
    assert "Chromium" in headers_default["Sec-Ch-Ua"]

    # 2. With authentic client headers: must NOT inject hardcoded Sec-Ch-Ua
    custom = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
        "Cookie": "session=abc",
    }
    headers_custom = get_browser_headers(
        "https://example.com/file.zip",
        custom_headers=custom,
    )
    assert "Sec-Ch-Ua" not in headers_custom
    assert "Sec-Ch-Ua-Mobile" not in headers_custom
    assert "Sec-Ch-Ua-Platform" not in headers_custom
    assert headers_custom["Cookie"] == "session=abc"
    assert headers_custom["User-Agent"] == custom["User-Agent"]


@pytest.mark.asyncio
async def test_single_stream_downgrade_strips_range_header(tmp_path: Path):
    """
    Verifies that when multi-chunk Range download fails with 403 Range rejection,
    the engine executes the automatic downgrade to single-stream sequential download
    and strips the Range header completely.
    """
    received_requests = []

    async def mock_handler(request: web.Request) -> web.Response:
        received_requests.append(dict(request.headers))
        if "Range" in request.headers or "range" in request.headers:
            return web.Response(status=403, text="Range requests forbidden by Cloudflare")
        return web.Response(status=200, body=b"A" * 50000)

    app = web.Application()
    app.router.add_route("*", "/protected_file.bin", mock_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = 19672
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        adapter = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
        pool = MultiInterfaceSessionPool(adapters=[adapter])
        await pool.initialize()

        info = RemoteResourceInfo(
            url=f"http://127.0.0.1:{port}/protected_file.bin",
            total_size=50000,
            supports_ranges=True,
            filename="protected_file.bin",
        )

        engine = DownloadEngine(
            url=f"http://127.0.0.1:{port}/protected_file.bin",
            session_pool=pool,
            destination_dir=tmp_path,
            workers_per_interface=1,
        )
        engine.info = info

        success = await engine.start()
        assert success is True

        downloaded = tmp_path / "protected_file.bin"
        assert downloaded.exists()
        assert downloaded.stat().st_size == 50000

        # The final single-stream request that succeeded must NOT contain a Range header
        last_req = received_requests[-1]
        assert "Range" not in last_req
        assert "range" not in last_req

        await pool.close()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_error_message_preservation_on_zero_bytes(tmp_path: Path):
    """
    Verifies that when a download fails with 0 bytes transferred,
    task.error_message records the actual HTTP error (e.g. 'HTTP 403 Forbidden (Cloudflare WAF)')
    instead of the generic 'Download ended incompletely'.
    """
    async def forbidden_handler(request: web.Request) -> web.Response:
        return web.Response(status=403, text="Cloudflare WAF Challenge", reason="Forbidden")

    app = web.Application()
    app.router.add_route("*", "/blocked.bin", forbidden_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    port = 19673
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        adapter = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
        pool = MultiInterfaceSessionPool(adapters=[adapter])
        await pool.initialize()

        db_path = tmp_path / "state.db"
        store = StateStore(db_path=db_path)
        download_dir = tmp_path / "downloads"
        download_dir.mkdir()

        qm = QueueManager(
            session_pool=pool,
            state_store=store,
            default_download_dir=download_dir,
        )
        await qm.start()

        task = await qm.add_url(
            url=f"http://127.0.0.1:{port}/blocked.bin",
            auto_start=True,
            total_size=100000,
        )

        # Allow queue manager to attempt download
        for _ in range(50):
            await asyncio.sleep(0.1)
            updated = qm.get_task(task.task_id)
            if updated and updated.status == DownloadStatus.FAILED:
                task = updated
                break

        assert task.status == DownloadStatus.FAILED
        assert task.downloaded_bytes == 0
        # Must preserve the real HTTP 403 Forbidden error message, not generic 'Download ended incompletely'
        assert task.error_message is not None
        assert "403" in task.error_message
        assert "Forbidden" in task.error_message
        assert "Download ended incompletely" not in task.error_message

        await qm.stop()
        await pool.close()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_named_pipe_system_set_settings():
    """Verifies that system.set_settings RPC updates QueueManager configuration."""
    from unittest.mock import MagicMock
    mock_qm = MagicMock()
    mock_qm.require_confirmation = False
    mock_qm.confirmation_threshold_mb = 100
    mock_qm.auto_categorize = True
    mock_qm.get_all_tasks.return_value = []

    mock_pool = MagicMock()
    mock_pool.active_adapters = []
    mock_pool.vpn_shield_enabled = True

    server = NamedPipeServer(
        queue_manager=mock_qm,
        session_pool=mock_pool,
        pipe_name="test_settings_pipe",
    )

    # 1. Call system.set_settings
    res = server._rpc_system_set_settings({
        "require_confirmation": True,
        "confirmation_threshold_mb": 500,
        "auto_categorize": False,
    })

    assert res["status"] == "ok"
    assert mock_qm.require_confirmation is True
    assert mock_qm.confirmation_threshold_mb == 500
    assert mock_qm.auto_categorize is False

    # 2. Call system.get_stats to verify settings are returned
    stats = server._rpc_system_get_stats({})
    assert stats["require_confirmation"] is True
    assert stats["confirmation_threshold_mb"] == 500
    assert stats["auto_categorize"] is False


@pytest.mark.asyncio
async def test_worker_curl_cffi_download_error_handling(tmp_path: Path):
    """Verifies that ChunkWorker raises DownloadError when curl_cffi receives an HTTP error status >= 400."""
    from core.engine.chunk_planner import ChunkPlanner

    planner = ChunkPlanner(total_size=1000)
    chunk = list(planner.chunks.values())[0]

    adapter = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    mock_session = MagicMock()

    worker = ChunkWorker(
        worker_id=1,
        adapter=adapter,
        session=mock_session,
        url="https://example.com/blocked",
        planner=planner,
        storage=MagicMock(),
    )

    # Simulate aiohttp returning 403
    mock_aio_resp = AsyncMock()
    mock_aio_resp.status = 403
    mock_session.get.return_value.__aenter__.return_value = mock_aio_resp

    # Mock curl_cffi session returning 403 challenge
    mock_curl_resp = MagicMock()
    mock_curl_resp.status_code = 403
    mock_curl_resp.reason = "Forbidden"

    mock_curl_session = AsyncMock()
    mock_curl_session.get.return_value = mock_curl_resp
    mock_curl_session.__aenter__.return_value = mock_curl_session

    with patch("core.engine.worker.HAVE_CURL_CFFI", True), \
         patch("core.engine.worker.CurlAsyncSession", return_value=mock_curl_session):
        success = await worker._download_chunk(chunk)
        assert success is False
        assert worker.last_error == "HTTP 403 Forbidden (Cloudflare WAF)"
