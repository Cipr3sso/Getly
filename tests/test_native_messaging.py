import asyncio
import io
import json
from pathlib import Path
import struct
import pytest
from aiohttp import web
import aiohttp

from core.cnl.server import CNLServer
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus
from core.network.session_pool import MultiInterfaceSessionPool
from core.network.models import NetworkAdapter
from native_messaging.host import read_message, send_message, forward_download, fallback_save_to_db


def test_native_messaging_framing():
    # 1. Test sending a message
    test_payload = {"url": "https://example.com/test.iso", "action": "download", "id": 123}
    buf = io.BytesIO()
    send_message(test_payload, stream=buf)

    data = buf.getvalue()
    assert len(data) >= 4
    length = struct.unpack("@I", data[:4])[0]
    assert length == len(data) - 4

    decoded = json.loads(data[4:].decode("utf-8"))
    assert decoded == test_payload

    # 2. Test reading back the framed message
    buf.seek(0)
    read_payload = read_message(stream=buf)
    assert read_payload == test_payload

    # 3. Test EOF handling (empty stream)
    empty_buf = io.BytesIO(b"")
    assert read_message(stream=empty_buf) is None

    # 4. Test truncated length header (< 4 bytes)
    truncated_buf = io.BytesIO(b"12")
    assert read_message(stream=truncated_buf) is None

    # 5. Test truncated payload (< length bytes)
    bad_buf = io.BytesIO(struct.pack("@I", 100) + b"short")
    assert read_message(stream=bad_buf) is None


def test_native_messaging_ping_and_validation():
    # Ping action
    ping_resp = forward_download({"action": "ping"})
    assert ping_resp["status"] == "ok"
    assert "online" in ping_resp["message"]

    # Missing url
    err_resp = forward_download({})
    assert err_resp["status"] == "error"

    # Non http/https url
    err_resp2 = forward_download({"url": "ftp://example.com/file"})
    assert err_resp2["status"] == "error"


def test_native_messaging_offline_fallback_db(tmp_path: Path):
    dest_dir = tmp_path / "downloads"
    payload = {
        "url": "https://offline-example.org/large_archive.zip",
        "filename": "custom_name.zip",
        "referer": "https://offline-example.org/landing",
        "cookies": "sid=xyz123; auth=true",
        "user_agent": "Mozilla/5.0 ResilientTest",
        "destination_dir": str(dest_dir),
    }

    # Execute fallback save to DB
    result = fallback_save_to_db(payload)
    assert result["status"] == "ok"
    assert result["fallback"] is True
    assert "task_id" in result

    # Verify task in SQLite state store
    db_path = dest_dir / ".state.db"
    assert db_path.exists()

    store = StateStore(db_path)
    task = store.get_task(result["task_id"])
    assert task is not None
    assert task.url == payload["url"]
    assert task.filename == "custom_name.zip"
    assert task.referer == payload["referer"]
    assert task.cookies == payload["cookies"]
    assert task.user_agent == payload["user_agent"]
    assert task.status == DownloadStatus.PENDING


@pytest.mark.asyncio
async def test_api_add_download_endpoint_on_cnl_server():
    received_requests = []

    async def mock_download_requested(data: dict):
        received_requests.append(data)
        return DownloadTask(
            url=data["url"],
            destination_dir=Path("/tmp"),
            task_id="test-task-uuid-42",
            filename=data.get("filename"),
            cookies=data.get("cookies"),
            referer=data.get("referer"),
        )

    test_port = 19822
    server = CNLServer(
        host="127.0.0.1",
        port=test_port,
        on_download_requested=mock_download_requested,
    )
    await server.start()

    try:
        async with aiohttp.ClientSession() as session:
            # 1. CORS Preflight OPTIONS check
            async with session.options(f"http://127.0.0.1:{test_port}/api/add_download") as resp:
                assert resp.status == 200
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"
                assert "POST" in resp.headers.get("Access-Control-Allow-Methods")

            # 2. Invalid payload without url
            async with session.post(f"http://127.0.0.1:{test_port}/api/add_download", json={}) as resp:
                assert resp.status == 400
                res = await resp.json()
                assert res["status"] == "error"

            # 3. Valid payload from WebExtension direct HTTP
            payload = {
                "url": "https://cdn.example.com/movie.mkv",
                "filename": "movie.mkv",
                "cookies": "auth_token=abc987",
                "referer": "https://cdn.example.com/",
            }
            async with session.post(f"http://127.0.0.1:{test_port}/api/add_download", json=payload) as resp:
                assert resp.status == 200
                res = await resp.json()
                assert res["status"] == "ok"
                assert res["task_id"] == "test-task-uuid-42"
                assert res["filename"] == "movie.mkv"

            assert len(received_requests) == 1
            assert received_requests[0]["url"] == payload["url"]
            assert received_requests[0]["cookies"] == payload["cookies"]

            # 4. Payload with full Windows path in filename
            payload_win = {
                "url": "https://cdn.example.com/archive.zip",
                "filename": r"C:\Users\carlo\Downloads\archive.zip",
            }
            async with session.post(f"http://127.0.0.1:{test_port}/api/add_download", json=payload_win) as resp:
                assert resp.status == 200
                res = await resp.json()
                assert res["status"] == "ok"
                assert res["filename"] == "archive.zip"
                assert received_requests[1]["filename"] == "archive.zip"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_native_messaging_host_forward_to_running_daemon(tmp_path: Path):
    download_dir = tmp_path / "downloads"
    db_path = download_dir / ".state.db"
    test_port = 19823

    loopback_adapter = NetworkAdapter(name="loopback", ip="127.0.0.1")
    pool = MultiInterfaceSessionPool(adapters=[loopback_adapter])
    state_store = StateStore(db_path)
    queue_manager = QueueManager(session_pool=pool, state_store=state_store, default_download_dir=download_dir)

    server = CNLServer(
        host="127.0.0.1",
        port=test_port,
        on_download_requested=lambda data: queue_manager.add_url(
            url=data.get("url"),
            destination_dir=download_dir,
            filename=data.get("filename"),
            cookies=data.get("cookies"),
            referer=data.get("referer"),
            user_agent=data.get("user_agent"),
        ),
    )
    await server.start()

    try:
        payload = {
            "url": "https://integration.test/file.zip",
            "filename": "file.zip",
            "cookies": "session=secret123",
            "referer": "https://integration.test/",
            "user_agent": "BrowserTest/1.0",
        }

        # forward_download is synchronous, so in async tests we run it in a worker thread
        # to prevent blocking the event loop that serves CNLServer
        daemon_endpoint = f"http://127.0.0.1:{test_port}/api/add_download"
        result = await asyncio.to_thread(forward_download, payload, daemon_endpoint)

        assert result["status"] == "ok"
        assert "task_id" in result
        task_id = result["task_id"]

        # Check queue manager and database
        task = queue_manager.get_task(task_id)
        assert task is not None
        assert task.url == payload["url"]
        assert task.cookies == payload["cookies"]
        assert task.referer == payload["referer"]
        assert task.user_agent == payload["user_agent"]
    finally:
        await server.stop()
