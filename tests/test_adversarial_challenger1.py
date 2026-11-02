import asyncio
import os
from pathlib import Path
import pytest
import aiohttp
from aiohttp import web

from core.engine.downloader import DownloadEngine, DownloadError
from core.engine.probe import (
    sanitize_headers_for_impersonation,
    get_browser_headers,
    RemoteResourceInfo,
)
from core.api.local_server import LocalHttpServer
from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadStatus
from core.scheduler.categories import get_category_subfolder, CATEGORY_ALIASES


# =========================================================================
# 1. ADVERSARIAL TESTS: sanitize_headers_for_impersonation
# =========================================================================

def test_sanitize_headers_boundary_inputs():
    """Test boundary inputs: None, empty dict."""
    assert sanitize_headers_for_impersonation(None) == {}
    assert sanitize_headers_for_impersonation({}) == {}


def test_sanitize_headers_case_insensitivity_and_permutations():
    """Verify that every casing permutation of Sec-Ch-Ua* and User-Agent is stripped."""
    headers = {
        "Sec-Ch-Ua": '"Chromium";v="124"',
        "SEC-CH-UA": '"Chromium";v="125"',
        "sEc-Ch-Ua": '"Not-A.Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "SEC-CH-UA-MOBILE": "?1",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "sec-ch-ua-platform": '"Linux"',
        "SEC-CH-UA-PLATFORM": '"macOS"',
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "user-agent": "Mozilla/5.0 (Macintosh)",
        "USER-AGENT": "curl/7.68.0",
        "uSeR-aGeNt": "Custom-Bot",
        # Strictly preserved headers
        "Cookie": "session_id=abcdef; cf_clearance=token123",
        "cookie": "legacy_val=1",
        "Referer": "https://example.com/download-page",
        "referer": "https://secondary.org/",
        "Range": "bytes=0-1048575",
        "Authorization": "Bearer secret_token",
        "X-Custom-Header": "KeepMe",
        "Accept": "*/*",
    }

    sanitized = sanitize_headers_for_impersonation(headers)

    # Check stripped keys
    for k in sanitized:
        assert k.lower() not in (
            "user-agent",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
        ), f"Key {k} should have been stripped!"

    # Check preserved keys
    assert "Cookie" in sanitized or "cookie" in sanitized
    assert "Referer" in sanitized or "referer" in sanitized
    assert "Range" in sanitized
    assert "Authorization" in sanitized
    assert sanitized["X-Custom-Header"] == "KeepMe"


def test_sanitize_headers_preserves_cf_clearance_and_complex_cookies():
    """Ensure complex Cloudflare clearance cookies and tracking headers remain intact."""
    cookie_str = (
        "__cf_bm=x123; cf_clearance=AbCdEf123456; "
        "_ga=GA1.2.3456; session=.eJw1zjsOwjAMANC7ZGZw4s"
    )
    headers = {
        "User-Agent": "TestUA",
        "Sec-Ch-Ua": "TestUAHint",
        "Cookie": cookie_str,
        "Referer": "https://protected-site.org/portal",
    }
    sanitized = sanitize_headers_for_impersonation(headers)
    assert "User-Agent" not in sanitized
    assert "Sec-Ch-Ua" not in sanitized
    assert sanitized["Cookie"] == cookie_str
    assert sanitized["Referer"] == "https://protected-site.org/portal"


# =========================================================================
# 2. ADVERSARIAL TESTS: POST /add_batch API Endpoint Edge Cases
# =========================================================================

@pytest.mark.asyncio
async def test_add_batch_empty_and_malformed_json():
    """Stress test malformed payloads on /add_batch."""
    test_port = 19781
    server = LocalHttpServer(host="127.0.0.1", port=test_port)

    async with server:
        async with aiohttp.ClientSession() as client:
            # 1. Invalid non-JSON body
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                data="Not JSON at all",
                headers={"Content-Type": "text/plain"},
            ) as resp:
                assert resp.status == 400
                data = await resp.json()
                assert data["status"] == "error"

            # 2. JSON array instead of object
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json=["item1", "item2"],
            ) as resp:
                assert resp.status == 400
                data = await resp.json()
                assert data["status"] == "error"

            # 3. Missing 'items' key
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"auto_start": True},
            ) as resp:
                assert resp.status == 400
                data = await resp.json()
                assert data["status"] == "error"
                assert "items" in data["message"]

            # 4. 'items' is not a list
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": "invalid_string"},
            ) as resp:
                assert resp.status == 400
                data = await resp.json()
                assert data["status"] == "error"

            # 5. Empty items list
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": []},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                assert data["count"] == 0
                assert data["tasks"] == []


@pytest.mark.asyncio
async def test_add_batch_invalid_urls_and_path_traversal(tmp_path: Path):
    """Test adversarial items: invalid protocols, javascript injection, path traversal filenames."""
    ad1 = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "batch_adv.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    test_port = 19782
    server = LocalHttpServer(host="127.0.0.1", port=test_port, queue_manager=qm)

    items = [
        # Missing URL
        {"filename": "missing_url.zip"},
        # Invalid schemes
        {"url": "ftp://ftp.funet.fi/pub/test.zip", "filename": "ftp.zip"},
        {"url": "javascript:alert('xss')", "filename": "xss.html"},
        {"url": "file:///C:/Windows/notepad.exe", "filename": "local.exe"},
        {"url": "", "filename": "empty.zip"},
        # Malicious filename with traversal
        {
            "url": "https://example.com/test.zip",
            "filename": "../../../../../etc/shadow",
            "file_size": "5242880", # string size
        },
        # Windows backslash path traversal
        {
            "url": "https://example.com/calc.exe",
            "filename": "..\\..\\Windows\\System32\\calc.exe",
            "file_size": -100, # negative size
        },
        # Filename with query params
        {
            "url": "https://example.com/media.mp4",
            "filename": "video_sample.mp4?auth=secret123&expire=9999",
            "file_size": "invalid_non_int",
        },
    ]

    async with server:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": items, "auto_start": False},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                # Only the 3 valid URLs (https) should be accepted, invalid schemes skipped
                assert data["count"] == 3
                assert len(data["tasks"]) == 3

                filenames = [t["filename"] for t in data["tasks"]]
                # Verify path traversal was sanitized
                assert "shadow" in filenames
                assert "../../../../../etc/shadow" not in filenames
                assert "calc.exe" in filenames
                assert "..\\..\\Windows\\System32\\calc.exe" not in filenames
                # Verify query params stripped from filename
                assert "video_sample.mp4" in filenames

    await pool.close()


@pytest.mark.asyncio
async def test_add_batch_confirmation_threshold_and_auto_start_variations(tmp_path: Path):
    """Test auto_start boolean vs string variations, and confirmation threshold triggering WAITING status."""
    ad1 = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "batch_threshold.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    qm.require_confirmation = False
    qm.confirmation_threshold_mb = 100 # 100 MB threshold

    test_port = 19783
    server = LocalHttpServer(host="127.0.0.1", port=test_port, queue_manager=qm)

    items = [
        # Item 1: Small file (50 MB), auto_start=True -> should start/PENDING
        {
            "url": "https://example.com/small.iso",
            "filename": "small.iso",
            "file_size": 50 * 1024 * 1024,
            "auto_start": True,
        },
        # Item 2: Large file (250 MB > 100 MB threshold), auto_start=True -> should trigger WAITING
        {
            "url": "https://example.com/large.iso",
            "filename": "large.iso",
            "file_size": 250 * 1024 * 1024,
            "auto_start": True,
        },
        # Item 3: String auto_start="false" -> should be WAITING
        {
            "url": "https://example.com/manual.zip",
            "filename": "manual.zip",
            "file_size": 10 * 1024 * 1024,
            "auto_start": "false",
        },
        # Item 4: Global auto_start overridden by item auto_start
        {
            "url": "https://example.com/explicit_false.zip",
            "filename": "explicit_false.zip",
            "file_size": 1 * 1024 * 1024,
            "auto_start": False,
        },
    ]

    async with server:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": items, "auto_start": True},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["count"] == 4

                tasks = {t["filename"]: t["status"] for t in data["tasks"]}
                assert tasks["small.iso"] in ("PENDING", "DOWNLOADING")
                assert tasks["large.iso"] == "WAITING"
                assert tasks["manual.zip"] == "WAITING"
                assert tasks["explicit_false.zip"] == "WAITING"

    await pool.close()


@pytest.mark.asyncio
async def test_add_batch_category_alias_routing(tmp_path: Path):
    """Test category aliasing (Archivi -> Compressi, etc.) and auto_categorize toggle."""
    ad1 = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "batch_cat.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    qm.auto_categorize = True

    test_port = 19784
    server = LocalHttpServer(host="127.0.0.1", port=test_port, queue_manager=qm)

    items = [
        {"url": "https://example.com/file1.zip", "category": "Archivi"},
        {"url": "https://example.com/file2.zip", "category": "compressi"},
        {"url": "https://example.com/file3.mp4", "category": "Video"},
        {"url": "https://example.com/file4.exe", "category": "Programmi"},
    ]

    async with server:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": items, "auto_start": False},
            ) as resp:
                assert resp.status == 200

            # Verify tasks in QueueManager were routed to proper subdirectories
            all_tasks = qm.get_all_tasks()
            task_map = {t.url: t for t in all_tasks}

            assert task_map["https://example.com/file1.zip"].category == "Compressi"
            assert Path(task_map["https://example.com/file1.zip"].destination_dir).name == "Compressi"

            assert task_map["https://example.com/file2.zip"].category == "Compressi"
            assert Path(task_map["https://example.com/file2.zip"].destination_dir).name == "Compressi"

            assert task_map["https://example.com/file3.mp4"].category == "Video"
            assert Path(task_map["https://example.com/file3.mp4"].destination_dir).name == "Video"

            assert task_map["https://example.com/file4.exe"].category == "Programmi"
            assert Path(task_map["https://example.com/file4.exe"].destination_dir).name == "Programmi"

            # Now test when auto_categorize is False
            qm.auto_categorize = False
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": [{"url": "https://example.com/file5.zip", "category": "Archivi"}], "auto_start": False},
            ) as resp2:
                assert resp2.status == 200

            all_tasks2 = qm.get_all_tasks()
            task5 = next(t for t in all_tasks2 if t.url == "https://example.com/file5.zip")
            # Destination dir must remain base default_download_dir without Compressi subfolder
            assert Path(task5.destination_dir) == tmp_path

    await pool.close()


# =========================================================================
# 3. ADVERSARIAL TESTS: Range 403 Fallback & Worker Error Preservation
# =========================================================================

@pytest.mark.asyncio
async def test_adversarial_range_403_multi_chunk_downgrade(tmp_path: Path):
    """
    Simulates a CDN server that rejects all Range requests with 403,
    but accepts sequential plain GET without Range header.
    Tests that DownloadEngine downgrades to single-stream sequential download
    and completes successfully.
    """
    download_bytes = b"X" * (50 * 1024) # 50 KB payload
    received_requests = []

    async def handle_download(request: web.Request) -> web.Response:
        received_requests.append(dict(request.headers))
        if request.method == "HEAD":
            return web.Response(
                status=200,
                headers={
                    "Content-Length": str(len(download_bytes)),
                    "Accept-Ranges": "bytes",
                    "Content-Type": "application/octet-stream",
                },
            )
        # When worker sends Range header, CDN rejects with 403 (e.g. Cloudflare WAF block)
        if "Range" in request.headers or "range" in request.headers:
            return web.Response(
                status=403,
                body=b"Cloudflare WAF: Range not permitted",
                headers={"Content-Type": "text/html"},
            )
        # Plain GET without Range succeeds
        return web.Response(
            status=200,
            body=download_bytes,
            headers={
                "Content-Length": str(len(download_bytes)),
                "Content-Type": "application/octet-stream",
            },
        )

    app = web.Application()
    app.router.add_route("*", "/cdn_range_blocked.bin", handle_download)
    runner = web.AppRunner(app)
    await runner.setup()
    port = 19785
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        adapter = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
        pool = MultiInterfaceSessionPool(adapters=[adapter])
        await pool.initialize()

        test_url = f"http://127.0.0.1:{port}/cdn_range_blocked.bin"
        info = RemoteResourceInfo(
            url=test_url,
            total_size=len(download_bytes),
            supports_ranges=True,
            filename="cdn_range_blocked.bin",
        )

        engine = DownloadEngine(
            url=test_url,
            session_pool=pool,
            destination_dir=tmp_path,
            workers_per_interface=1,
        )
        engine.info = info

        success = await engine.start()
        assert success is True, "DownloadEngine should have downgraded to single-stream sequential and succeeded"

        out_file = tmp_path / "cdn_range_blocked.bin"
        assert out_file.exists()
        assert out_file.stat().st_size == len(download_bytes)
        assert out_file.read_bytes() == download_bytes

        # Verify that the final request did not have Range header
        last_req = received_requests[-1]
        assert "Range" not in last_req
        assert "range" not in last_req

        await pool.close()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_adversarial_error_message_preservation_on_waf_rejection(tmp_path: Path):
    """
    Simulates a host where BOTH multi-chunk Range and single-stream plain GET
    are rejected with HTTP 403 Cloudflare WAF.
    Verifies that DownloadEngine records the genuine worker exception
    'HTTP 403 Forbidden (Cloudflare WAF)' and NOT 'Download ended incompletely'.
    """
    async def handle_blocked(request: web.Request) -> web.Response:
        return web.Response(
            status=403,
            text="Cloudflare WAF Challenge",
            reason="Forbidden",
        )

    app = web.Application()
    app.router.add_route("*", "/cloudflare_blocked.zip", handle_blocked)
    runner = web.AppRunner(app)
    await runner.setup()
    port = 19786
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    try:
        adapter = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
        pool = MultiInterfaceSessionPool(adapters=[adapter])
        await pool.initialize()

        test_url = f"http://127.0.0.1:{port}/cloudflare_blocked.zip"
        info = RemoteResourceInfo(
            url=test_url,
            total_size=50000,
            supports_ranges=True,
            filename="cloudflare_blocked.zip",
        )

        engine = DownloadEngine(
            url=test_url,
            session_pool=pool,
            destination_dir=tmp_path,
            workers_per_interface=1,
        )
        engine.info = info

        success = await engine.start()
        assert success is False
        assert engine.error_message is not None
        assert "403" in engine.error_message
        assert "Download ended incompletely" not in engine.error_message

        # Now test QueueManager integration
        store = StateStore(tmp_path / "adv_err.db")
        qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
        await qm.start()

        task = await qm.add_url(
            test_url,
            auto_start=True,
            total_size=50000,
        )

        for _ in range(50):
            await asyncio.sleep(0.1)
            updated = qm.get_task(task.task_id)
            if updated and updated.status == DownloadStatus.FAILED:
                break

        updated = qm.get_task(task.task_id)
        assert updated.status == DownloadStatus.FAILED
        assert updated.error_message is not None
        assert "403" in updated.error_message
        assert "Download ended incompletely" not in updated.error_message

        await qm.stop()
        await pool.close()
    finally:
        await runner.cleanup()


# =========================================================================
# 4. ADDITIONAL STRESS TESTS: High Volume, Corrupted Types & IPC
# =========================================================================

@pytest.mark.asyncio
async def test_add_batch_high_volume_stress(tmp_path: Path):
    """Stress test /add_batch with 100 items submitted simultaneously."""
    ad1 = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "batch_100.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    test_port = 19787
    server = LocalHttpServer(host="127.0.0.1", port=test_port, queue_manager=qm)

    items = [
        {
            "url": f"https://example.com/file_{i}.zip",
            "filename": f"file_{i}.zip",
            "file_size": 1024 * i,
            "category": "Archivi",
        }
        for i in range(100)
    ]

    async with server:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": items, "auto_start": False},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                assert data["count"] == 100
                assert len(data["tasks"]) == 100

    assert len(qm.get_all_tasks()) == 100
    await pool.close()


@pytest.mark.asyncio
async def test_add_batch_corrupted_item_types(tmp_path: Path):
    """Test items list with None items, string items, non-dict elements, null headers."""
    ad1 = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "batch_corrupt.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    test_port = 19788
    server = LocalHttpServer(host="127.0.0.1", port=test_port, queue_manager=qm)

    items = [
        None,
        "not a dict",
        12345,
        {"url": None},
        {"url": "https://example.com/good1.zip", "headers": None},
        {"url": "https://example.com/good2.zip", "headers": "invalid_headers_type"},
        {"url": "https://example.com/good3.zip", "destination_dir": None, "category": None},
        {"url": "https://example.com/good4.zip", "file_size": -9999999999},
    ]

    async with server:
        async with aiohttp.ClientSession() as client:
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": items, "auto_start": False},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                # Only the 4 valid items should be processed
                assert data["count"] == 4
                assert len(data["tasks"]) == 4

    await pool.close()


@pytest.mark.asyncio
async def test_ipc_system_set_settings_boundary_values(tmp_path: Path):
    """Test NamedPipeServer RPC system.set_settings with extreme and boundary parameters."""
    from core.ipc.pipe_server import NamedPipeServer

    ad1 = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "ipc_settings.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    pipe_server = NamedPipeServer(session_pool=pool, queue_manager=qm)

    # 1. Extreme large threshold via RPC dispatcher
    req = {
        "id": "req-1",
        "method": "system.set_settings",
        "params": {
            "require_confirmation": True,
            "confirmation_threshold_mb": 1000000,
            "auto_categorize": False,
        },
    }
    import json
    resp = await pipe_server._process_request_line(json.dumps(req))
    assert resp["error"] is None
    assert resp["result"]["status"] == "ok"
    assert qm.require_confirmation is True
    assert qm.confirmation_threshold_mb == 1000000
    assert qm.auto_categorize is False

    # 2. Boolean toggling & integer parsing
    res2 = pipe_server._rpc_system_set_settings({
        "require_confirmation": False,
        "confirmation_threshold_mb": "500",
        "auto_categorize": True,
    })
    assert res2["status"] == "ok"
    assert qm.require_confirmation is False
    assert qm.confirmation_threshold_mb == 500
    assert qm.auto_categorize is True

    # 3. Check stats RPC reflects new settings
    stats = pipe_server._rpc_system_get_stats({})
    assert stats["require_confirmation"] is False
    assert stats["confirmation_threshold_mb"] == 500
    assert stats["auto_categorize"] is True

    await pool.close()
