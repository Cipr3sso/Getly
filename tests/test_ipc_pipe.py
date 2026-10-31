"""
Unit and Integration Tests for Getly Windows Named Pipe IPC Server and Telemetry.
"""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import time
import uuid
import pytest

from core.engine.speed_limiter import SpeedLimiter
from core.ipc.pipe_server import NamedPipeServer, connect_pipe_client, serialize_task
from core.ipc.telemetry import TelemetryDispatcher
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadTask, DownloadStatus
from engine_service import run_test_client


@asynccontextmanager
async def ipc_server_context(tmp_path: Path):
    pipe_name = rf"\\.\pipe\test_getly_ipc_{uuid.uuid4().hex[:10]}"
    pool = MultiInterfaceSessionPool(adapters=[])
    store = StateStore(tmp_path / "ipc_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    await qm.start()

    server = NamedPipeServer(queue_manager=qm, session_pool=pool, pipe_name=pipe_name)
    await server.start()

    try:
        yield {
            "pool": pool,
            "store": store,
            "qm": qm,
            "server": server,
            "pipe_name": pipe_name,
            "dir": tmp_path,
        }
    finally:
        await server.stop()
        await qm.stop()
        await pool.close()


@pytest.mark.asyncio
async def test_pipe_server_start_stop_and_client_connect(tmp_path: Path):
    async with ipc_server_context(tmp_path) as env:
        pipe_name = env["pipe_name"]
        server: NamedPipeServer = env["server"]

        assert server.is_running is True
        assert server.connected_client_count == 0

        reader, writer = await connect_pipe_client(pipe_name=pipe_name)
        await asyncio.sleep(0.05)
        assert server.connected_client_count == 1

        # Send ping
        req = {"id": "1", "method": "ping"}
        writer.write((json.dumps(req) + "\n").encode("utf-8"))
        await writer.drain()

        resp_line = await reader.readline()
        resp = json.loads(resp_line.decode("utf-8").strip())
        assert resp["id"] == "1"
        assert resp["result"]["pong"] is True

        # Disconnect
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.05)
        assert server.connected_client_count == 0


@pytest.mark.asyncio
async def test_pipe_server_multiple_clients_and_reconnection(tmp_path: Path):
    async with ipc_server_context(tmp_path) as env:
        pipe_name = env["pipe_name"]
        server: NamedPipeServer = env["server"]

        # 1. Connect two clients simultaneously
        r1, w1 = await connect_pipe_client(pipe_name=pipe_name)
        r2, w2 = await connect_pipe_client(pipe_name=pipe_name)
        await asyncio.sleep(0.05)
        assert server.connected_client_count == 2

        # Query both
        w1.write((json.dumps({"id": "c1", "method": "ping"}) + "\n").encode())
        w2.write((json.dumps({"id": "c2", "method": "ping"}) + "\n").encode())
        await w1.drain()
        await w2.drain()

        res1 = json.loads((await r1.readline()).decode())
        res2 = json.loads((await r2.readline()).decode())
        assert res1["id"] == "c1"
        assert res2["id"] == "c2"

        # Close client 1
        w1.close()
        await w1.wait_closed()
        await asyncio.sleep(0.05)
        assert server.connected_client_count == 1

        # Reconnect client 1
        r1_new, w1_new = await connect_pipe_client(pipe_name=pipe_name)
        await asyncio.sleep(0.05)
        assert server.connected_client_count == 2

        w1_new.write((json.dumps({"id": "c1_reconnect", "method": "ping"}) + "\n").encode())
        await w1_new.drain()
        res1_new = json.loads((await r1_new.readline()).decode())
        assert res1_new["id"] == "c1_reconnect"

        w1_new.close()
        w2.close()
        await w1_new.wait_closed()
        await w2.wait_closed()


@pytest.mark.asyncio
async def test_rpc_tasks_lifecycle(tmp_path: Path):
    async with ipc_server_context(tmp_path) as env:
        pipe_name = env["pipe_name"]
        qm: QueueManager = env["qm"]
        reader, writer = await connect_pipe_client(pipe_name=pipe_name)

        async def call_rpc(method: str, params: dict = None, req_id: str = "test-rpc"):
            payload = {"id": req_id, "method": method, "params": params or {}}
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
            line = await reader.readline()
            return json.loads(line.decode("utf-8").strip())

        # 1. tasks.list (empty initially)
        resp = await call_rpc("tasks.list")
        assert resp["error"] is None
        assert isinstance(resp["result"], list)
        assert len(resp["result"]) == 0

        # 2. task.add
        add_resp = await call_rpc(
            "task.add",
            {
                "url": "https://example.com/sample.mp4",
                "category": "Video",
                "filename": "sample.mp4",
                "start_paused": True,
            },
        )
        assert add_resp["error"] is None
        task_data = add_resp["result"]
        assert task_data["url"] == "https://example.com/sample.mp4"
        assert task_data["filename"] == "sample.mp4"
        assert task_data["category"] == "Video"
        assert task_data["status"] == "PAUSED"
        tid = task_data["task_id"]

        # 3. task.get
        get_resp = await call_rpc("task.get", {"task_id": tid})
        assert get_resp["error"] is None
        assert get_resp["result"]["task_id"] == tid

        # 4. task.resume and task.pause
        resume_resp = await call_rpc("task.resume", {"task_id": tid})
        assert resume_resp["result"]["success"] is True

        pause_resp = await call_rpc("task.pause", {"task_id": tid})
        assert pause_resp["result"]["success"] is True

        # 5. task.update_url
        update_resp = await call_rpc(
            "task.update_url",
            {"task_id": tid, "new_url": "https://example.com/fresh_sample.mp4", "restart": False},
        )
        assert update_resp["result"]["success"] is True
        assert update_resp["result"]["new_url"] == "https://example.com/fresh_sample.mp4"

        # Verify task updated in queue
        refreshed = qm.get_task(tid)
        assert refreshed.url == "https://example.com/fresh_sample.mp4"

        # 6. task.delete
        del_resp = await call_rpc("task.delete", {"task_id": tid, "delete_files": True})
        assert del_resp["result"]["success"] is True
        assert qm.get_task(tid) is None

        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_rpc_system_and_torrent_methods(tmp_path: Path):
    async with ipc_server_context(tmp_path) as env:
        pipe_name = env["pipe_name"]
        reader, writer = await connect_pipe_client(pipe_name=pipe_name)

        async def call_rpc(method: str, params: dict = None, req_id: str = "sys-rpc"):
            payload = {"id": req_id, "method": method, "params": params or {}}
            writer.write((json.dumps(payload) + "\n").encode("utf-8"))
            await writer.drain()
            line = await reader.readline()
            return json.loads(line.decode("utf-8").strip())

        # 1. system.set_speed_limit
        limit_resp = await call_rpc("system.set_speed_limit", {"limit_bps": 2_500_000})
        assert limit_resp["error"] is None
        assert limit_resp["result"]["limit_bps"] == 2_500_000
        assert SpeedLimiter.get_instance().get_limit() == 2_500_000

        # 2. system.get_stats
        stats_resp = await call_rpc("system.get_stats")
        assert stats_resp["error"] is None
        stats = stats_resp["result"]
        assert "active_adapters" in stats
        assert "vpn_shield_enabled" in stats
        assert "download_speed_bps" in stats
        assert "memory" in stats
        assert stats["memory"]["process_rss_mb"] > 0
        assert stats["speed_limit_bps"] == 2_500_000
        assert "task_counts" in stats

        # 3. torrent.get_files with unknown task returns empty list
        tf_resp = await call_rpc("torrent.get_files", {"task_id": "nonexistent"})
        assert tf_resp["error"] is None
        assert tf_resp["result"] == []

        # 4. torrent.set_priority with unknown task returns False
        prio_resp = await call_rpc("torrent.set_priority", {"task_id": "nonexistent", "file_index": 0, "priority": 1})
        assert prio_resp["error"] is None
        assert prio_resp["result"]["success"] is False

        # 5. Invalid method error handling
        err_resp = await call_rpc("unknown.method")
        assert err_resp["error"] is not None
        assert "Unknown RPC method" in err_resp["error"]

        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_telemetry_dispatcher_10hz_and_delta_updates(tmp_path: Path):
    async with ipc_server_context(tmp_path) as env:
        pipe_name = env["pipe_name"]
        server: NamedPipeServer = env["server"]
        qm: QueueManager = env["qm"]
        pool = env["pool"]

        dispatcher = TelemetryDispatcher(pipe_server=server, queue_manager=qm, session_pool=pool, hz=10.0)
        dispatcher.start()
        assert dispatcher.is_running is True

        reader, writer = await connect_pipe_client(pipe_name=pipe_name)
        await asyncio.sleep(0.05)

        # Add a task so telemetry detects changes
        task = await qm.add_url("https://example.com/stream.bin", filename="stream.bin", start_paused=True)

        # Read the first tick broadcast frame
        tick_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
        frame1 = json.loads(tick_line.decode("utf-8").strip())
        assert frame1["event"] == "tick"
        assert "stats" in frame1
        assert "interfaces" in frame1["stats"]
        # Task was newly added, so it should be in frame1["tasks"]
        assert len(frame1["tasks"]) == 1
        assert frame1["tasks"][0]["task_id"] == task.task_id

        # On next tick without any byte or status change, delta updates should send empty tasks
        tick_line2 = await asyncio.wait_for(reader.readline(), timeout=2.0)
        frame2 = json.loads(tick_line2.decode("utf-8").strip())
        assert frame2["event"] == "tick"
        assert len(frame2["tasks"]) == 0

        # Modify downloaded_bytes on task
        task.downloaded_bytes = 1048576
        tick_line3 = await asyncio.wait_for(reader.readline(), timeout=2.0)
        frame3 = json.loads(tick_line3.decode("utf-8").strip())
        assert len(frame3["tasks"]) == 1
        assert frame3["tasks"][0]["downloaded_bytes"] == 1048576

        writer.close()
        await writer.wait_closed()
        await dispatcher.stop()
        assert dispatcher.is_running is False


@pytest.mark.asyncio
async def test_engine_service_test_client_cli(tmp_path: Path):
    async with ipc_server_context(tmp_path) as env:
        pipe_name = env["pipe_name"]

        # Test running test client against active server
        code = await run_test_client(pipe_name=pipe_name)
        assert code == 0

        # Test running with custom command
        code2 = await run_test_client(pipe_name=pipe_name, custom_cmd="tasks.list")
        assert code2 == 0
