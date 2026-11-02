import pytest
import aiohttp
from core.api.local_server import LocalHttpServer


@pytest.mark.asyncio
async def test_local_server_health_and_jdcheck():
    test_port = 19667
    server = LocalHttpServer(host="127.0.0.1", port=test_port)

    async with server:
        assert server.is_running
        async with aiohttp.ClientSession() as client:
            # 1. Test GET /health
            async with client.get(f"http://127.0.0.1:{test_port}/health") as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "ok"
                assert data["app"] == "Getly"
                assert "version" in data
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"

            # 2. Test GET /jdcheck.js
            async with client.get(f"http://127.0.0.1:{test_port}/jdcheck.js") as resp:
                assert resp.status == 200
                text = await resp.text()
                assert "jdownloader=true" in text
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"


@pytest.mark.asyncio
async def test_local_server_add_download():
    received_downloads = []

    async def on_download(data: dict):
        received_downloads.append(data)
        return {"task_id": "test-task-123", "filename": data.get("filename")}

    test_port = 19668
    server = LocalHttpServer(
        host="127.0.0.1",
        port=test_port,
        on_download_requested=on_download,
    )

    async with server:
        assert server.is_running
        async with aiohttp.ClientSession() as client:
            # 1. Test OPTIONS preflight on /add
            async with client.options(f"http://127.0.0.1:{test_port}/add") as resp:
                assert resp.status == 200
                assert resp.headers.get("Access-Control-Allow-Origin") == "*"
                assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")

            # 2. Test valid POST /add
            payload = {
                "url": "https://cdn.example.com/test_video.mp4?auth=xyz",
                "filename": "custom_name.mp4",
                "category": "Video",
                "headers": {
                    "Referer": "https://example.com/watch",
                    "User-Agent": "Mozilla/5.0 TestBrowser",
                    "Cookie": "session=abc123",
                },
            }
            async with client.post(f"http://127.0.0.1:{test_port}/add", json=payload) as resp:
                assert resp.status == 200
                res = await resp.json()
                assert res["status"] == "ok"
                assert res["task_id"] == "test-task-123"
                assert res["filename"] == "custom_name.mp4"

            assert len(received_downloads) == 1
            rec = received_downloads[0]
            assert rec["url"] == "https://cdn.example.com/test_video.mp4?auth=xyz"
            assert rec["filename"] == "custom_name.mp4"
            assert rec["category"] == "Video"
            assert rec["referer"] == "https://example.com/watch"
            assert rec["cookies"] == "session=abc123"
            assert rec["user_agent"] == "Mozilla/5.0 TestBrowser"
            assert rec["auto_start"] is True

            # Test POST /add with explicit auto_start: False and file_size
            await client.post(
                f"http://127.0.0.1:{test_port}/add",
                json={
                    "url": "https://example.com/manual.iso",
                    "file_size": 31457280,
                    "auto_start": False,
                },
            )
            assert len(received_downloads) == 2
            assert received_downloads[1]["auto_start"] is False
            assert received_downloads[1]["file_size"] == 31457280

            # 3. Test invalid POST /add (missing url)
            bad_payload = {"filename": "no_url.mp4"}
            async with client.post(f"http://127.0.0.1:{test_port}/add", json=bad_payload) as resp:
                assert resp.status == 400
                err_res = await resp.json()
                assert err_res["status"] == "error"


@pytest.mark.asyncio
async def test_local_server_add_batch_on_download_requested():
    received_downloads = []

    async def on_download(data: dict):
        received_downloads.append(data)
        return {
            "task_id": f"task-{len(received_downloads)}",
            "filename": data.get("filename"),
            "status": "PENDING" if data.get("auto_start") else "WAITING",
        }

    test_port = 19669
    server = LocalHttpServer(
        host="127.0.0.1",
        port=test_port,
        on_download_requested=on_download,
    )

    async with server:
        assert server.is_running
        async with aiohttp.ClientSession() as client:
            # 1. Test empty batch
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json={"items": [], "auto_start": True},
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                assert data["count"] == 0
                assert data["tasks"] == []

            # 2. Test valid multi-item batch
            batch_payload = {
                "items": [
                    {
                        "url": "https://example.com/file1.zip",
                        "filename": "file1.zip",
                        "file_size": 1048576,
                        "category": "Archivi",
                        "headers": {
                            "Referer": "https://example.com/",
                            "Cookie": "cf_clearance=secret123",
                        },
                    },
                    {
                        "url": "https://example.com/video.mp4",
                        "filename": "clip.mp4",
                        "category": "Video",
                        "auto_start": False,
                    },
                ],
                "auto_start": True,
            }
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json=batch_payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                assert data["count"] == 2
                assert len(data["tasks"]) == 2
                assert data["tasks"][0]["task_id"] == "task-1"
                assert data["tasks"][0]["filename"] == "file1.zip"
                assert data["tasks"][0]["status"] == "PENDING"
                assert data["tasks"][1]["task_id"] == "task-2"
                assert data["tasks"][1]["filename"] == "clip.mp4"
                assert data["tasks"][1]["status"] == "WAITING"

            assert len(received_downloads) == 2
            assert received_downloads[0]["url"] == "https://example.com/file1.zip"
            assert received_downloads[0]["cookies"] == "cf_clearance=secret123"
            assert received_downloads[0]["auto_start"] is True
            assert received_downloads[1]["auto_start"] is False

            # 3. Test invalid request bodies
            async with client.post(f"http://127.0.0.1:{test_port}/add_batch", json={"items": "invalid"}) as resp:
                assert resp.status == 400
                err = await resp.json()
                assert err["status"] == "error"

            async with client.post(f"http://127.0.0.1:{test_port}/add_batch", data="not json") as resp:
                assert resp.status == 400


@pytest.mark.asyncio
async def test_local_server_add_batch_with_queue_manager(tmp_path):
    from pathlib import Path
    from core.network.models import NetworkAdapter
    from core.network.session_pool import MultiInterfaceSessionPool
    from core.scheduler.state_store import StateStore
    from core.scheduler.queue_manager import QueueManager
    from core.scheduler.task import DownloadStatus

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
    qm.auto_categorize = True
    qm.require_confirmation = False
    qm.confirmation_threshold_mb = 100

    test_port = 19670
    server = LocalHttpServer(
        host="127.0.0.1",
        port=test_port,
        queue_manager=qm,
    )

    async with server:
        assert server.is_running
        async with aiohttp.ClientSession() as client:
            batch_payload = {
                "items": [
                    {
                        "url": "https://example.com/package.zip",
                        "filename": "package.zip",
                        "file_size": 10 * 1024 * 1024,
                        "category": "Archivi",
                    },
                    {
                        "url": "https://example.com/huge_game.iso",
                        "filename": "huge_game.iso",
                        "file_size": 250 * 1024 * 1024,  # Exceeds 100 MB threshold!
                        "category": "Programmi",
                    },
                    {
                        "url": "https://example.com/unclassified.mp4",
                    },
                ],
                "auto_start": True,
            }
            async with client.post(
                f"http://127.0.0.1:{test_port}/add_batch",
                json=batch_payload,
            ) as resp:
                assert resp.status == 200
                data = await resp.json()
                assert data["success"] is True
                assert data["count"] == 3
                tasks = data["tasks"]

                # 1. package.zip: normal size, auto_start=True -> PENDING
                assert tasks[0]["status"] == "PENDING"
                task1 = qm.get_task(tasks[0]["task_id"])
                assert task1 is not None
                assert "Compressi" in str(task1.destination_dir)

                # 2. huge_game.iso: 250MB > 100MB threshold -> WAITING
                assert tasks[1]["status"] == "WAITING"
                task2 = qm.get_task(tasks[1]["task_id"])
                assert task2 is not None
                assert "Programmi" in str(task2.destination_dir)

                # 3. unclassified.mp4: auto-categorized to Video -> PENDING
                assert tasks[2]["status"] == "PENDING"
                task3 = qm.get_task(tasks[2]["task_id"])
                assert task3 is not None
                assert task3.category == "Video"
                assert "Video" in str(task3.destination_dir)

    await pool.close()
