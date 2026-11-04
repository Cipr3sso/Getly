import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.engine.media_extractor import (
    MediaExtractor,
    is_media_stream_url,
    is_m3u8_url,
    sanitize_filename,
)
from core.engine.stream_downloader import StreamDownloadEngine
from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadTask, DownloadStatus


def test_is_m3u8_url():
    assert is_m3u8_url("https://example.com/stream/index.m3u8") is True
    assert is_m3u8_url("https://live.video.net/hls/master.m3u8?token=123") is True
    assert is_m3u8_url("https://cdn.site.com/get?format=m3u8") is True
    assert is_m3u8_url("https://example.com/video.mp4") is False
    assert is_m3u8_url("https://example.com/file.zip") is False
    assert is_m3u8_url("") is False
    assert is_m3u8_url(None) is False


def test_is_media_stream_url():
    # Supported video platforms
    assert is_media_stream_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ") is True
    assert is_media_stream_url("https://youtu.be/dQw4w9WgXcQ") is True
    assert is_media_stream_url("https://www.tiktok.com/@creator/video/123456789") is True
    assert is_media_stream_url("https://www.instagram.com/reel/C3_abcde/") is True
    assert is_media_stream_url("https://twitter.com/user/status/123456789") is True
    assert is_media_stream_url("https://x.com/user/status/123456789") is True
    assert is_media_stream_url("https://vimeo.com/76979871") is True
    assert is_media_stream_url("https://v.redd.it/abcdef123") is True
    assert is_media_stream_url("https://www.twitch.tv/videos/12345") is True

    # HLS .m3u8 stream manifests
    assert is_media_stream_url("https://live.streaming.com/playlist.m3u8") is True

    # Standard direct files
    assert is_media_stream_url("https://releases.ubuntu.com/22.04/ubuntu-22.04-desktop-amd64.iso") is False
    assert is_media_stream_url("https://cdn.example.org/archive.zip") is False
    assert is_media_stream_url("https://mirror.opensuse.org/distribution.tar.gz") is False
    assert is_media_stream_url("") is False
    assert is_media_stream_url(None) is False


def test_sanitize_filename():
    assert sanitize_filename("Rick Astley - Never Gonna Give You Up (Official Music Video)") == "Rick Astley - Never Gonna Give You Up (Official Music Video).mp4"
    assert sanitize_filename("Video with: illegal / * ? < > | characters") == "Video with_ illegal _ _ _ _ _ _ characters.mp4"
    assert sanitize_filename("   Spaces   Everywhere   ") == "Spaces Everywhere.mp4"
    assert sanitize_filename("", ext="mp4") == "video.mp4"
    assert sanitize_filename("custom", ext=".mkv") == "custom.mkv"


def test_format_duration():
    assert MediaExtractor._format_duration(65) == "01:05"
    assert MediaExtractor._format_duration(3665) == "01:01:05"
    assert MediaExtractor._format_duration(0) == "--:--"
    assert MediaExtractor._format_duration(None) == "--:--"


def test_media_extractor_extract_info_mock():
    mock_ydl_info = {
        "title": "Sample Live Video",
        "duration": 185.5,
        "thumbnail": "https://i.ytimg.com/vi/sample/hqdefault.jpg",
        "uploader": "TestChannel",
        "ext": "mp4",
        "formats": [
            {
                "format_id": "137",
                "height": 1080,
                "ext": "mp4",
                "vcodec": "avc1.640028",
                "acodec": "none",
                "filesize": 52428800,
            },
            {
                "format_id": "136",
                "height": 720,
                "ext": "mp4",
                "vcodec": "avc1.4d401f",
                "acodec": "none",
                "filesize": 26214400,
            },
            {
                "format_id": "140",
                "height": None,
                "ext": "m4a",
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "filesize": 4194304,
            },
        ],
    }

    with patch("yt_dlp.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = mock_ydl_info

        res = MediaExtractor.extract_info("https://www.youtube.com/watch?v=sample123")
        assert res is not None
        assert res["title"] == "Sample Live Video"
        assert res["filename"] == "Sample Live Video.mp4"
        assert res["duration"] == 185.5
        assert res["duration_formatted"] == "03:05"
        assert res["thumbnail"] == "https://i.ytimg.com/vi/sample/hqdefault.jpg"
        assert res["uploader"] == "TestChannel"
        assert len(res["formats"]) == 2  # 1080p and 720p with heights
        assert res["formats"][0]["resolution"] == "1080p"
        assert res["formats"][1]["resolution"] == "720p"


@pytest.mark.asyncio
async def test_stream_downloader_progress_hooks_and_completion(tmp_path: Path):
    dest_dir = tmp_path / "Video"
    task = DownloadTask(
        url="https://www.youtube.com/watch?v=mock_video",
        destination_dir=str(dest_dir),
        filename="custom_video.mp4",
        category="Video",
    )

    progress_events = []
    def on_prog(downloaded: int, total: int, pct: float):
        progress_events.append((downloaded, total, pct))

    engine = StreamDownloadEngine(
        task=task,
        destination_dir=dest_dir,
        on_progress=on_prog,
    )
    assert engine.target_filepath.name == "custom_video.mp4"

    # Simulate completed file on disk
    mock_file = dest_dir / "custom_video.mp4"
    mock_file.write_bytes(b"x" * 1024)

    # Mock yt_dlp execution
    with patch("yt_dlp.YoutubeDL") as MockYDL:
        instance = MockYDL.return_value.__enter__.return_value
        instance.extract_info.return_value = {"title": "custom_video"}
        instance.download.return_value = 0

        success = await engine.start()
        assert success is True
        assert engine._total_downloaded == 1024
        assert engine.info.total_size == 1024
        assert engine.info.filename == "custom_video.mp4"


@pytest.mark.asyncio
async def test_stream_downloader_cancellation(tmp_path: Path):
    dest_dir = tmp_path / "Video"
    task = DownloadTask(
        url="https://www.youtube.com/watch?v=cancel_video",
        destination_dir=str(dest_dir),
        category="Video",
    )

    engine = StreamDownloadEngine(
        task=task,
        destination_dir=dest_dir,
    )

    await engine.cancel()
    assert engine._is_cancelled is True


@pytest.mark.asyncio
async def test_queue_manager_detects_stream_and_m3u8(tmp_path: Path):
    ad = NetworkAdapter(name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    store = StateStore(tmp_path / "test_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    # Mock extract_info so it doesn't do a real network call during test
    with patch.object(MediaExtractor, "extract_info", return_value={"title": "Rick Astley", "filename": "Rick Astley.mp4"}):
        task1 = await qm.add_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ", start_paused=True)
        assert task1.task_type == "STREAM"
        assert task1.category == "Video"
        assert task1.filename == "Rick Astley.mp4"

        task2 = await qm.add_url("https://example.com/live/stream.m3u8", start_paused=True)
        assert task2.task_type == "STREAM"
        assert task2.category == "Video"

    await pool.close()


@pytest.mark.asyncio
async def test_pipe_server_media_extract_info(tmp_path: Path):
    from core.ipc.pipe_server import NamedPipeServer

    ad = NetworkAdapter(name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    store = StateStore(tmp_path / "test_pipe_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    server = NamedPipeServer(queue_manager=qm, session_pool=pool)

    with patch.object(
        MediaExtractor,
        "extract_info",
        return_value={
            "title": "Rick Astley",
            "filename": "Rick Astley.mp4",
            "duration": 213,
            "duration_formatted": "03:33",
            "formats": [{"format_id": "137", "resolution": "1080p"}],
        },
    ):
        res = await server._rpc_media_extract_info({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        assert res["success"] is True
        assert res["info"]["title"] == "Rick Astley"
        assert res["info"]["filename"] == "Rick Astley.mp4"

    # Test error handling when url not provided
    with pytest.raises(ValueError):
        await server._rpc_media_extract_info({})

    await pool.close()


@pytest.mark.asyncio
async def test_queue_manager_recovers_failed_task_if_merged_file_exists(tmp_path: Path):
    ad = NetworkAdapter(name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    store = StateStore(tmp_path / "test_recover_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    # Create dummy final merged file
    final_file = tmp_path / "Video" / "Test Movie.mp4"
    final_file.parent.mkdir(parents=True, exist_ok=True)
    final_file.write_bytes(b"A" * 1024 * 1024)

    # Task was recorded as FAILED with temporary chunk name .f401.mp4
    task = DownloadTask(
        url="https://www.youtube.com/watch?v=mock",
        destination_dir=str(tmp_path / "Video"),
        filename="Test Movie.f401.mp4",
        status=DownloadStatus.FAILED,
        total_size=1024,
        error_message="WinError 2 file not found",
    )
    store.save_task(task)
    qm._tasks[task.task_id] = task

    # Run disk check
    qm.check_files_status()

    assert task.status == DownloadStatus.COMPLETED
    assert task.error_message is None
    assert task.filename == "Test Movie.mp4"
    assert task.total_size == 1024 * 1024
    assert task.downloaded_bytes == 1024 * 1024

    await pool.close()


