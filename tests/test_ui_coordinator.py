import asyncio
import time
from pathlib import Path
import pytest

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus
from ui.coordinator import UICoordinator, format_bytes, format_speed, format_eta
from ui.clipboard_watcher import ClipboardWatcher


def test_formatting_utilities():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024) == "1.0 KB"
    assert format_bytes(1048576) == "1.0 MB"
    assert format_bytes(1073741824) == "1.0 GB"

    assert format_speed(1048576) == "1.0 MB/s"

    assert format_eta(None) == "--:--"
    assert format_eta(0) == "00:00"
    assert format_eta(65) == "01:05"
    assert format_eta(3665) == "01:01:05"


@pytest.mark.asyncio
async def test_coordinator_adapter_toggle(tmp_path: Path):
    ad1 = NetworkAdapter(name="eth0", ip="192.168.1.10", is_up=True)
    ad2 = NetworkAdapter(name="wlan0", ip="192.168.1.20", is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1, ad2])
    await pool.initialize()

    store = StateStore(tmp_path / "state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    assert len(coord.get_adapters()) == 2
    assert coord.is_adapter_enabled("eth0")
    assert coord.is_adapter_enabled("wlan0")

    # Disable eth0
    success = coord.toggle_adapter("eth0", False)
    assert success
    assert not coord.is_adapter_enabled("eth0")
    assert len(pool.active_adapters) == 1
    assert pool.active_adapters[0].name == "wlan0"

    # Pool prevents disabling all adapters
    fail = coord.toggle_adapter("wlan0", False)
    assert not fail
    assert coord.is_adapter_enabled("wlan0")

    # Re-enable eth0
    coord.toggle_adapter("eth0", True)
    assert coord.is_adapter_enabled("eth0")
    assert len(pool.active_adapters) == 2

    await pool.close()


@pytest.mark.asyncio
async def test_coordinator_pause_and_resume_lifecycle(tmp_path: Path):
    ad1 = NetworkAdapter(name="eth0", ip="192.168.1.10", is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()

    store = StateStore(tmp_path / "state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    task = await coord.add_download("https://example.com/test.zip")
    assert task.status == DownloadStatus.PENDING

    # Pause task
    paused = await coord.pause_task(task.task_id)
    assert paused
    retrieved = qm.get_task(task.task_id)
    assert retrieved.status == DownloadStatus.PAUSED

    # Resume task
    resumed = await coord.resume_task(task.task_id)
    assert resumed
    retrieved_resumed = qm.get_task(task.task_id)
    assert retrieved_resumed.status == DownloadStatus.PENDING

    # Cancel task
    cancelled = await coord.cancel_task(task.task_id)
    assert cancelled
    assert qm.get_task(task.task_id).status == DownloadStatus.CANCELLED

    # Delete task
    deleted = await coord.delete_task(task.task_id)
    assert deleted
    assert qm.get_task(task.task_id) is None

    await pool.close()


def test_clipboard_watcher_url_filtering():
    detected_links = []
    watcher = ClipboardWatcher(on_link_detected=lambda url: detected_links.append(url))

    # Valid download URLs
    assert watcher._is_downloadable_url("https://example.com/files/archive.zip")
    assert watcher._is_downloadable_url("https://example.com/movie.mp4")
    assert watcher._is_downloadable_url("https://speed.hetzner.de/100MB.bin")
    assert watcher._is_downloadable_url("https://proof.ovh.net/files/100Mb.dat")
    assert watcher._is_downloadable_url("https://rapidgator.net/file/abc123/package.rar.html")
    assert watcher._is_downloadable_url("https://mediafire.com/file/xyz789/data.7z/file")

    # Invalid / plain text / search engines / social media
    assert not watcher._is_downloadable_url("Just some random copied text")
    assert not watcher._is_downloadable_url("ftp://example.com/test.zip")
    assert not watcher._is_downloadable_url("https://google.com/search?q=download+archive.zip")
    assert not watcher._is_downloadable_url("https://www.bing.com/videos/search?q=movie.mp4")
    assert not watcher._is_downloadable_url("https://duckduckgo.com/?q=ubuntu.iso")
    assert not watcher._is_downloadable_url("https://www.youtube.com/watch?v=sample_video.mp4")
    assert not watcher._is_downloadable_url("https://reddit.com/r/technology/comments/file.bin")
    assert not watcher._is_downloadable_url("https://github.com/profile")
