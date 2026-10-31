import asyncio
import logging
import os
import pytest
from pathlib import Path
from aiohttp import web
import aiohttp

from core.engine.storage import StorageEngine
from core.engine.chunk_planner import ChunkPlanner, Chunk
from core.engine.probe import ResourceProbe, get_browser_headers, ProbeError
from core.engine.worker import ChunkWorker
from core.engine.downloader import DownloadEngine
from core.engine.host_coordinator import HostCoordinator
from core.network.session_pool import MultiInterfaceSessionPool
from core.network.models import NetworkAdapter


@pytest.mark.asyncio
async def test_storage_engine_out_of_order_writes(tmp_path: Path):
    target_file = tmp_path / "test_output.bin"
    total_size = 1000

    storage = StorageEngine(filepath=target_file, total_size=total_size, sparse=True)
    async with storage:
        # Write block 3 (offset 600..799)
        await storage.write(600, b"C" * 200, chunk_id=3)
        # Write block 1 (offset 0..199)
        await storage.write(0, b"A" * 200, chunk_id=1)
        # Write block 2 (offset 200..399)
        await storage.write(200, b"B" * 200, chunk_id=2)
        # Write block 4 (offset 800..999)
        await storage.write(800, b"D" * 200, chunk_id=4)
        # Write block 2.5 (offset 400..599)
        await storage.write(400, b"X" * 200, chunk_id=2)
        await storage.flush()

    assert target_file.exists()
    assert target_file.stat().st_size == total_size

    content = target_file.read_bytes()
    assert content[0:200] == b"A" * 200
    assert content[200:400] == b"B" * 200
    assert content[400:600] == b"X" * 200
    assert content[600:800] == b"C" * 200
    assert content[800:1000] == b"D" * 200


def test_chunk_planner_slicing():
    total_size = 50 * 1024 * 1024  # 50MB
    planner = ChunkPlanner(total_size=total_size, min_chunk_size=4 * 1024 * 1024, max_chunk_size=16 * 1024 * 1024)

    assert len(planner.chunks) > 0

    # Verify contiguous byte ranges without overlaps or gaps
    last_end = -1
    for chunk_id in sorted(planner.chunks.keys()):
        chunk = planner.chunks[chunk_id]
        assert chunk.start_byte == last_end + 1
        assert chunk.end_byte >= chunk.start_byte
        assert chunk.size >= planner.min_chunk_size or chunk.end_byte == total_size - 1
        last_end = chunk.end_byte

    assert last_end == total_size - 1


@pytest.mark.asyncio
async def test_end_to_end_download_with_mock_range_server(tmp_path: Path):
    # Create test payload of 2MB
    test_payload = b"0123456789ABCDEF" * (128 * 1024)  # 2MB
    total_len = len(test_payload)

    # Simple mock aiohttp server supporting Range requests
    async def handle_download(request: web.Request) -> web.Response:
        range_header = request.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            parts = range_header[6:].split("-")
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else total_len - 1
            sliced = test_payload[start : end + 1]
            return web.Response(
                body=sliced,
                status=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{total_len}",
                    "Content-Length": str(len(sliced)),
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": 'attachment; filename="mock_file.dat"',
                },
            )
        return web.Response(
            body=test_payload,
            headers={
                "Content-Length": str(total_len),
                "Accept-Ranges": "bytes",
                "Content-Disposition": 'attachment; filename="mock_file.dat"',
            },
        )

    app = web.Application()
    app.router.add_get("/testfile.dat", handle_download)

    server_port = 19777
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", server_port)
    await site.start()

    try:
        # Create a session pool with loopback adapter for test
        loopback_adapter = NetworkAdapter(
            name="loopback",
            friendly_name="Loopback",
            ip="127.0.0.1",
        )
        pool = MultiInterfaceSessionPool(adapters=[loopback_adapter])
        await pool.initialize()

        dest_dir = tmp_path / "downloads"
        engine = DownloadEngine(
            url=f"http://127.0.0.1:{server_port}/testfile.dat",
            destination_dir=dest_dir,
            session_pool=pool,
            workers_per_interface=2,
        )

        success = await engine.start()
        assert success

        downloaded_file = dest_dir / "mock_file.dat"
        assert downloaded_file.exists()
        assert downloaded_file.stat().st_size == total_len
        assert downloaded_file.read_bytes() == test_payload

        await pool.close()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_probe_recovers_gracefully_on_head_403_and_sends_headers():
    received_headers = {}

    async def handle_probe(request: web.Request) -> web.Response:
        nonlocal received_headers
        received_headers = {k.lower(): v for k, v in request.headers.items()}

        # Reject all HEAD requests with 403 Forbidden (mimicking restrictive CDNs)
        if request.method == "HEAD":
            return web.Response(status=403, text="Forbidden for HEAD")

        # Accept GET with Range: bytes=0-0
        range_header = request.headers.get("Range")
        if range_header == "bytes=0-0":
            return web.Response(
                status=206,
                body=b"X",
                headers={
                    "Content-Range": "bytes 0-0/5242880",
                    "Content-Length": "1",
                    "Accept-Ranges": "bytes",
                    "Content-Disposition": 'attachment; filename="protected_file.iso"',
                },
            )
        return web.Response(status=404)

    app = web.Application()
    app.router.add_route("*", "/probe_test", handle_probe)

    server_port = 19788
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", server_port)
    await site.start()

    try:
        async with aiohttp.ClientSession() as session:
            info = await ResourceProbe.probe(session, f"http://127.0.0.1:{server_port}/probe_test")

            # Verify that probe recovered from HEAD 403 and extracted metadata via GET Range fallback
            assert info.supports_ranges is True
            assert info.total_size == 5242880
            assert info.filename == "protected_file.iso"

            # Verify realistic browser headers and dynamic Referer (case-insensitive)
            assert "Chrome/125" in received_headers.get("user-agent", "")
            assert received_headers.get("accept") == "*/*"
            assert received_headers.get("accept-encoding") == "identity"
            assert received_headers.get("referer") == f"http://127.0.0.1:{server_port}/"
            assert "Chromium" in received_headers.get("sec-ch-ua", "")
            assert received_headers.get("sec-fetch-mode") == "cors"
    finally:
        await runner.cleanup()


def test_dynamic_referer_and_client_hints():
    url = "https://testfile.org/100MB"
    headers = get_browser_headers(url)
    assert headers["Referer"] == "https://testfile.org/"
    assert headers["Sec-Ch-Ua"] == '"Chromium";v="125", "Not.A/Brand";v="24"'
    assert headers["Sec-Ch-Ua-Mobile"] == "?0"
    assert headers["Sec-Ch-Ua-Platform"] == '"Windows"'
    assert headers["Sec-Fetch-Dest"] == "empty"
    assert headers["Sec-Fetch-Mode"] == "cors"
    assert headers["Sec-Fetch-Site"] == "cross-site"
    assert headers["Accept-Encoding"] == "identity"
    assert headers["Connection"] == "keep-alive"


def test_adaptive_chunk_sizing_rules():
    MB = 1024 * 1024

    # Rule 1: File < 20 MB (1 chunk per active interface, bound between 4-8 MB)
    size_small_1if = ChunkPlanner.calculate_adaptive_chunk_size(10 * MB, active_interfaces_count=1)
    assert 4 * MB <= size_small_1if <= 8 * MB

    size_small_2if = ChunkPlanner.calculate_adaptive_chunk_size(10 * MB, active_interfaces_count=2)
    assert size_small_2if == 5 * MB

    # Rule 2: File between 20 MB and 500 MB (16 MB to 32 MB chunks, ~8-12 total chunks)
    size_100mb = ChunkPlanner.calculate_adaptive_chunk_size(100 * MB)
    assert size_100mb == 16 * MB
    planner_100 = ChunkPlanner(100 * MB)
    assert 6 <= len(planner_100.chunks) <= 10

    size_300mb = ChunkPlanner.calculate_adaptive_chunk_size(300 * MB)
    assert size_300mb == 30 * MB
    planner_300 = ChunkPlanner(300 * MB)
    assert len(planner_300.chunks) == 10

    # Rule 3: File > 500 MB (32 MB to 64 MB chunks)
    size_1gb = ChunkPlanner.calculate_adaptive_chunk_size(1000 * MB)
    assert 32 * MB <= size_1gb <= 64 * MB
    planner_1gb = ChunkPlanner(1000 * MB)
    assert len(planner_1gb.chunks) < 30  # Definitely not hundreds of micro-chunks


def test_worker_parse_retry_after():
    # Integer seconds
    assert ChunkWorker.parse_retry_after("15") == 15.0
    assert ChunkWorker.parse_retry_after("0") == 0.0

    # Future date
    future_date = "Wed, 21 Oct 2036 07:28:00 GMT"
    res = ChunkWorker.parse_retry_after(future_date)
    assert res is not None and res > 0

    # Invalid string
    assert ChunkWorker.parse_retry_after("not-a-number") is None
    assert ChunkWorker.parse_retry_after(None) is None


@pytest.mark.asyncio
async def test_worker_http_429_exponential_backoff_and_retry(tmp_path: Path):
    requests_received = 0
    test_data = b"HELLO_RESILIENT_CHUNK_DATA_429"

    async def handle_429_test(request: web.Request) -> web.Response:
        nonlocal requests_received
        requests_received += 1
        if requests_received == 1:
            # First request: respond with 429 and Retry-After: 0.1
            return web.Response(
                status=429,
                text="Rate limited",
                headers={"Retry-After": "1"},
            )
        # Second request: accept and return 206 Partial Content
        return web.Response(
            status=206,
            body=test_data,
            headers={
                "Content-Range": f"bytes 0-{len(test_data) - 1}/{len(test_data)}",
                "Content-Length": str(len(test_data)),
                "Accept-Ranges": "bytes",
            },
        )

    app = web.Application()
    app.router.add_get("/retry_test", handle_429_test)

    server_port = 19799
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", server_port)
    await site.start()

    try:
        dest_file = tmp_path / "retry_test.bin"
        storage = StorageEngine(filepath=dest_file, total_size=len(test_data))
        await storage.open()

        planner = ChunkPlanner(total_size=len(test_data), min_chunk_size=len(test_data), max_chunk_size=len(test_data))
        adapter = NetworkAdapter(name="loopback", ip="127.0.0.1")

        async with aiohttp.ClientSession() as session:
            worker = ChunkWorker(
                worker_id=0,
                adapter=adapter,
                session=session,
                url=f"http://127.0.0.1:{server_port}/retry_test",
                planner=planner,
                storage=storage,
                max_retries=3,
            )

            chunk = await planner.get_next_chunk()
            assert chunk is not None

            # Execute download
            success = await worker._download_chunk(chunk)
            assert success is True
            assert chunk.is_completed is True
            assert requests_received == 2  # Proves that worker retried after 429

        await storage.flush()
        await storage.close()
        assert dest_file.read_bytes() == test_data
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_probe_fails_strictly_on_network_error():
    # Attempting to probe an unreachable port must propagate ProbeError rather than returning size=0
    async with aiohttp.ClientSession() as session:
        with pytest.raises(ProbeError):
            await ResourceProbe.probe(session, "http://127.0.0.1:59999/non_existent.bin")


@pytest.mark.asyncio
async def test_host_coordinator_concurrency_and_circuit_breaker():
    coord = HostCoordinator(default_concurrency=4)
    coord.reset()

    # 1. Verify sensitive host concurrency limit (proof.ovh.net -> 2)
    assert coord.get_concurrency_limit("proof.ovh.net") == 2
    assert coord.get_concurrency_limit("test.ovh.net") == 2
    assert coord.get_concurrency_limit("generic-host.com") == 4

    sem = coord.get_semaphore("proof.ovh.net")
    assert sem._value == 2

    # 2. Verify circuit breaker tripping
    assert not coord.is_blocked("proof.ovh.net")
    coord.trip_circuit_breaker("proof.ovh.net", 0.2)
    assert coord.is_blocked("proof.ovh.net")

    # 3. Verify wait_if_blocked waits until block timestamp expires
    start = asyncio.get_running_loop().time()
    await coord.wait_if_blocked("proof.ovh.net")
    duration = asyncio.get_running_loop().time() - start
    assert duration >= 0.15
    assert not coord.is_blocked("proof.ovh.net")

    coord.reset()


@pytest.mark.asyncio
async def test_global_circuit_breaker_coordinates_workers(tmp_path: Path):
    # Server tracks concurrent requests and 429 backoff
    requests_count = 0
    test_data = b"DATA_FOR_WORKER_COORDINATION"

    async def handle_request(request: web.Request) -> web.Response:
        nonlocal requests_count
        requests_count += 1
        # First request gets 429 with 0.3s backoff
        if requests_count == 1:
            return web.Response(status=429, headers={"Retry-After": "1"})
        return web.Response(
            status=206,
            body=test_data[:10],
            headers={
                "Content-Range": f"bytes 0-9/{len(test_data)}",
                "Content-Length": "10",
                "Accept-Ranges": "bytes",
            },
        )

    app = web.Application()
    app.router.add_get("/multi_test", handle_request)
    server_port = 19805
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", server_port)
    await site.start()

    coord = HostCoordinator.get_instance()
    coord.reset()

    try:
        dest_file = tmp_path / "multi_test.bin"
        storage = StorageEngine(filepath=dest_file, total_size=len(test_data))
        await storage.open()

        planner = ChunkPlanner(total_size=len(test_data), min_chunk_size=10, max_chunk_size=10)
        adapter = NetworkAdapter(name="loopback", ip="127.0.0.1")

        async with aiohttp.ClientSession() as session:
            w1 = ChunkWorker(
                worker_id=1,
                adapter=adapter,
                session=session,
                url=f"http://127.0.0.1:{server_port}/multi_test",
                planner=planner,
                storage=storage,
                max_retries=2,
                host_coordinator=coord,
            )
            w2 = ChunkWorker(
                worker_id=2,
                adapter=adapter,
                session=session,
                url=f"http://127.0.0.1:{server_port}/multi_test",
                planner=planner,
                storage=storage,
                max_retries=2,
                host_coordinator=coord,
            )

            # Trigger chunk download on w1 which trips 429
            c1 = await planner.get_next_chunk()
            task1 = asyncio.create_task(w1._download_chunk(c1))

            # Small delay to ensure w1 hits 429
            await asyncio.sleep(0.05)

            # Verify that circuit breaker is now active on 127.0.0.1
            host = coord.extract_host(f"http://127.0.0.1:{server_port}/multi_test")
            assert coord.is_blocked(host) is True

            # When w2 checks if blocked, it must observe the circuit breaker
            assert coord.is_blocked(w2.host) is True

            # Wait for w1 to finish its retry
            await task1
            assert not coord.is_blocked(host)

        await storage.flush()
        await storage.close()
    finally:
        coord.reset()
        await runner.cleanup()


@pytest.mark.asyncio
async def test_host_coordinator_wait_if_blocked_single_sleep(caplog):
    coord = HostCoordinator(default_concurrency=4)
    coord.reset()
    host = "single-sleep-test.com"

    # Trip circuit breaker for 0.1s
    coord.trip_circuit_breaker(host, 0.1)
    assert coord.is_blocked(host) is True

    # Execute wait_if_blocked with INFO level logging
    with caplog.at_level(logging.INFO):
        await coord.wait_if_blocked(host)

    # Must be unblocked after single sleep
    assert coord.is_blocked(host) is False

    # Verify exactly 1 wait log message is emitted (no busy-waiting repetition)
    wait_logs = [r.message for r in caplog.records if f"Host '{host}' is blocked by circuit breaker" in r.message]
    assert len(wait_logs) == 1
    assert "Worker waiting..." in wait_logs[0]
    coord.reset()


@pytest.mark.asyncio
async def test_worker_logs_chunk_pickup_and_completion(caplog, tmp_path: Path):
    test_data = b"0123456789" * 100  # 1000 bytes

    async def handle_chunk(request: web.Request) -> web.Response:
        return web.Response(
            status=206,
            body=test_data,
            headers={
                "Content-Range": f"bytes 0-{len(test_data) - 1}/{len(test_data)}",
                "Content-Length": str(len(test_data)),
                "Accept-Ranges": "bytes",
            },
        )

    app = web.Application()
    app.router.add_get("/log_chunk_test", handle_chunk)
    server_port = 19812
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", server_port)
    await site.start()

    try:
        dest_file = tmp_path / "log_test.bin"
        storage = StorageEngine(filepath=dest_file, total_size=len(test_data))
        await storage.open()

        planner = ChunkPlanner(total_size=len(test_data), min_chunk_size=len(test_data), max_chunk_size=len(test_data))
        adapter = NetworkAdapter(name="Wi-Fi_Test", ip="127.0.0.1")

        with caplog.at_level(logging.INFO):
            async with aiohttp.ClientSession() as session:
                worker = ChunkWorker(
                    worker_id=42,
                    adapter=adapter,
                    session=session,
                    url=f"http://127.0.0.1:{server_port}/log_chunk_test",
                    planner=planner,
                    storage=storage,
                )
                chunk = await planner.get_next_chunk()
                assert chunk is not None
                success = await worker._download_chunk(chunk)
                assert success is True

        await storage.flush()
        await storage.close()

        # Check pickup log
        pickup_logs = [r for r in caplog.records if "preso in carico chunk" in r.message]
        assert len(pickup_logs) >= 1
        assert "Worker 42 [Wi-Fi_Test]" in pickup_logs[0].message
        assert "range 0-999" in pickup_logs[0].message
        assert "MB" in pickup_logs[0].message

        # Check completion log
        completion_logs = [r for r in caplog.records if "completato e confermato su disco chunk" in r.message]
        assert len(completion_logs) >= 1
        assert "Worker 42 [Wi-Fi_Test]" in completion_logs[0].message
        assert "MB scaricati" in completion_logs[0].message
    finally:
        await runner.cleanup()

