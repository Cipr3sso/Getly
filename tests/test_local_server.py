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

            # 3. Test invalid POST /add (missing url)
            bad_payload = {"filename": "no_url.mp4"}
            async with client.post(f"http://127.0.0.1:{test_port}/add", json=bad_payload) as resp:
                assert resp.status == 400
                err_res = await resp.json()
                assert err_res["status"] == "error"
