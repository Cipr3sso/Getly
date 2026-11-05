import asyncio
import os
from pathlib import Path
import tempfile
from unittest.mock import MagicMock
import pytest

import libtorrent as lt

from core.network.models import NetworkAdapter
from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.state_store import StateStore
from core.scheduler.categories import detect_category, CATEGORY_TORRENTS, ALL_CATEGORIES
from core.engine.torrent_engine import (
    TorrentSessionManager,
    TorrentDownloadEngine,
    TorrentMetadata,
    TorrentFileInfo,
    LIBTORRENT_AVAILABLE,
)
from core.scheduler.queue_manager import QueueManager
from core.network.session_pool import MultiInterfaceSessionPool


def test_libtorrent_availability():
    assert LIBTORRENT_AVAILABLE is True
    assert hasattr(lt, "session")
    assert hasattr(lt, "parse_magnet_uri")


def test_magnet_link_parsing():
    magnet = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=Ubuntu-22.04.iso"
    params = lt.parse_magnet_uri(magnet)
    assert params.name == "Ubuntu-22.04.iso"
    assert str(params.info_hashes.v1) == "da39a3ee5e6b4b0d3255bfef95601890afd80709"


def test_category_detection_for_torrent_and_magnet():
    assert detect_category("magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567") == CATEGORY_TORRENTS
    assert detect_category("https://example.com/archlinux.torrent") == CATEGORY_TORRENTS
    assert detect_category("myfile.torrent") == CATEGORY_TORRENTS
    assert CATEGORY_TORRENTS in ALL_CATEGORIES


def test_state_store_torrent_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_tasks.db"
        store = StateStore(db_path)

        task = DownloadTask(
            url="magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=test.iso",
            destination_dir=Path(tmpdir) / "Torrents",
            filename="test.iso",
            task_type="TORRENT",
            category=CATEGORY_TORRENTS,
            seeds=25,
            peers=50,
            upload_speed_bps=1048576.0,
            pieces_total=1000,
            pieces_completed=450,
            info_hash="da39a3ee5e6b4b0d3255bfef95601890afd80709",
            selected_files=[0, 2, 4],
        )

        store.save_task(task)

        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.task_type == "TORRENT"
        assert loaded.category == CATEGORY_TORRENTS
        assert loaded.seeds == 25
        assert loaded.peers == 50
        assert loaded.upload_speed_bps == 1048576.0
        assert loaded.pieces_total == 1000
        assert loaded.pieces_completed == 450
        assert loaded.info_hash == "da39a3ee5e6b4b0d3255bfef95601890afd80709"
        assert loaded.selected_files == [0, 2, 4]


def test_torrent_session_manager_singleton():
    mgr1 = TorrentSessionManager.get_instance()
    mgr2 = TorrentSessionManager.get_instance()
    assert mgr1 is mgr2
    assert mgr1.session is not None
    assert mgr1.session.get_settings().get("enable_dht", False) is True


@pytest.mark.asyncio
async def test_queue_manager_torrent_routing():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_queue.db"
        store = StateStore(db_path)
        pool = MultiInterfaceSessionPool([])

        qm = QueueManager(
            session_pool=pool,
            state_store=store,
            default_download_dir=Path(tmpdir),
        )

        magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=ArchLinux.iso"
        task = await qm.add_url(
            url=magnet_url,
            start_paused=True,
        )

        assert task.task_type == "TORRENT"
        assert task.category == CATEGORY_TORRENTS
        assert task.destination_dir.name == CATEGORY_TORRENTS
        assert task.status == DownloadStatus.PAUSED


@pytest.mark.asyncio
async def test_torrent_metadata_resolution_local_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        torrent_file = Path(tmpdir) / "test.torrent"
        fs = lt.file_storage()
        fs.add_file("bundle/video.mp4", 1048576)
        fs.add_file("bundle/subtitle.srt", 2048)
        ct = lt.create_torrent(fs)
        for i in range(ct.num_pieces()):
            ct.set_hash(i, b"12345678901234567890")
        torrent_file.write_bytes(lt.bencode(ct.generate()))

        mgr = TorrentSessionManager.get_instance()
        meta = await mgr.resolve_metadata(str(torrent_file))

        assert meta.has_metadata is True
        assert len(meta.files) == 2
        paths = [f.path for f in meta.files]
        assert any("video.mp4" in p for p in paths)
        assert any("subtitle.srt" in p for p in paths)
        assert meta.total_size >= 1048576 + 2048


@pytest.mark.asyncio
async def test_torrent_engine_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        torrent_path = Path(tmpdir) / "sample.torrent"
        fs = lt.file_storage()
        fs.add_file("sample.bin", 65536)
        ct = lt.create_torrent(fs)
        for i in range(ct.num_pieces()):
            ct.set_hash(i, b"12345678901234567890")
        torrent_path.write_bytes(lt.bencode(ct.generate()))

        task = DownloadTask(
            url=str(torrent_path),
            destination_dir=Path(tmpdir) / "output",
            task_type="TORRENT",
            category=CATEGORY_TORRENTS,
        )

        engine = TorrentDownloadEngine(task)
        assert engine.info.total_size == 0

        # Start task in background
        start_task = asyncio.create_task(engine.start())
        await asyncio.sleep(0.5)

        # Check handle valid and pause/resume
        await engine.pause()
        assert task.task_type == "TORRENT"
        await engine.resume()

        # Check adapter bytes
        ad_bytes = engine.get_adapter_bytes()
        assert isinstance(ad_bytes, dict)

        # Cancel engine
        await engine.cancel()
        await start_task


@pytest.mark.asyncio
async def test_torrent_priority_handling():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_prio.db"
        store = StateStore(db_path)
        pool = MultiInterfaceSessionPool([])
        qm = QueueManager(pool, store, Path(tmpdir))

        magnet_url = "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=test.iso"
        task = await qm.add_url(
            url=magnet_url,
            start_paused=True,
            priority=7,
        )

        assert task.priority == 7
        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.priority == 7


def test_pieces_completed_type_safety_and_formatting():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_pieces.db"
        store = StateStore(db_path)

        task = DownloadTask(
            url="magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=ubuntu.iso",
            destination_dir=Path(tmpdir),
            task_type="TORRENT",
            category=CATEGORY_TORRENTS,
            pieces_total=4053,
            pieces_completed=0,
        )

        # Test string formatting: must not raise TypeError
        formatted = "%d/%d pieces" % (task.pieces_completed, task.pieces_total)
        assert formatted == "0/4053 pieces"

        # Corrupt list passed to simulate libtorrent bitfield list
        task.pieces_completed = [True, False, True]  # type: ignore
        store.save_task(task)

        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert isinstance(loaded.pieces_completed, int)
        assert loaded.pieces_completed == 2
        assert "%d/%d" % (loaded.pieces_completed, loaded.pieces_total) == "2/4053"


def test_vpn_detection_and_kill_switch_binding():
    from core.network.models import NetworkAdapter
    from core.network.interface_detector import InterfaceDetector

    assert InterfaceDetector.is_vpn_adapter(name="NordLynx", description="NordVPN Tunnel") is True
    assert InterfaceDetector.is_vpn_adapter(name="wg0", description="WireGuard Virtual Adapter") is True
    assert InterfaceDetector.is_vpn_adapter(name="tun0", description="OpenVPN TAP-Windows Adapter V9") is True
    assert InterfaceDetector.is_vpn_adapter(name="Wi-Fi", description="Intel(R) Wi-Fi 6 AX201 160MHz") is False

    lan_adapter = NetworkAdapter(
        name="Ethernet",
        description="Realtek PCIe GbE Family Controller",
        ip="192.168.1.50",
        if_index=10,
        is_vpn=False,
    )
    vpn_adapter = NetworkAdapter(
        name="NordLynx",
        description="NordLynx Tunnel",
        ip="10.5.0.2",
        if_index=66,
        is_vpn=True,
    )

    pool = MultiInterfaceSessionPool([lan_adapter, vpn_adapter], vpn_shield_enabled=True)
    assert pool.has_vpn is True
    active = pool.active_adapters
    assert len(active) == 1
    assert active[0].name == "NordLynx"
    assert active[0].ip == "10.5.0.2"


def test_torrent_privacy_hardened_configuration():
    from core.network.models import NetworkAdapter

    vpn_adapter = NetworkAdapter(
        name="NordLynx",
        description="NordLynx Tunnel",
        ip="10.5.0.2",
        if_index=66,
        is_vpn=True,
    )
    pool = MultiInterfaceSessionPool([vpn_adapter], vpn_shield_enabled=True)

    mgr = TorrentSessionManager.get_instance(session_pool=pool)
    assert mgr.session is not None

    sett = mgr.session.get_settings()
    assert sett.get("anonymous_mode", True) is False
    assert sett.get("enable_dht", False) is True
    assert sett.get("enable_lsd", False) is True
    assert sett.get("enable_upnp", True) is False
    assert sett.get("enable_natpmp", True) is False
    listen_ifs = sett.get("listen_interfaces", "")
    assert "10.5.0.2" in listen_ifs
    outgoing_ifs = sett.get("outgoing_interfaces", "")
    assert "10.5.0.2" in outgoing_ifs


@pytest.mark.asyncio
async def test_torrent_metadata_num_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        torrent_file = Path(tmpdir) / "dialog_test.torrent"
        fs = lt.file_storage()
        fs.add_file("archive/video.mp4", 2048576)
        fs.add_file("archive/audio.mp3", 102400)
        fs.add_file("archive/readme.txt", 512)
        ct = lt.create_torrent(fs)
        for i in range(ct.num_pieces()):
            ct.set_hash(i, b"12345678901234567890")
        torrent_file.write_bytes(lt.bencode(ct.generate()))

        mgr = TorrentSessionManager.get_instance()
        meta = await mgr.resolve_metadata(str(torrent_file))

        # Verify TorrentMetadata.num_files property and files list
        assert meta.has_metadata is True
        assert meta.num_files == 3
        assert len(meta.files) == 3


def test_torrent_session_manager_dht_and_anonymous_settings():
    assert TorrentSessionManager.DHT_ROUTERS is not None
    assert len(TorrentSessionManager.DHT_ROUTERS) >= 4
    router_hosts = [r[0] for r in TorrentSessionManager.DHT_ROUTERS]
    assert "router.bittorrent.com" in router_hosts
    assert "dht.transmissionbt.com" in router_hosts
    assert "router.utorrent.com" in router_hosts
    assert "dht.libtorrent.org" in router_hosts


@pytest.mark.asyncio
async def test_torrent_engine_files_status_and_priority(tmp_path: Path):
    """
    Test TorrentDownloadEngine file progress tracking, priority updates,
    and completion status normalization.
    """
    task = DownloadTask(
        url="magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12&dn=Ubuntu_ISO",
        destination_dir=tmp_path,
        filename="Ubuntu_ISO",
        task_type="TORRENT",
        info_hash="abcdef1234567890abcdef1234567890abcdef12",
        selected_files=[0, 1],
    )

    engine = TorrentDownloadEngine(task=task)

    # Mock libtorrent handle and torrent_info matching libtorrent file_storage API
    mock_ti = MagicMock()
    mock_ti.num_files.return_value = 2

    mock_fs = MagicMock()
    mock_fs.file_path.side_effect = lambda i: ["Ubuntu_ISO/ubuntu.iso", "Ubuntu_ISO/readme.txt"][i]
    mock_fs.file_size.side_effect = lambda i: [1000000, 2000][i]
    mock_fs.file_flags.return_value = 0
    mock_ti.files.return_value = mock_fs

    mock_status = MagicMock()
    mock_status.has_metadata = True

    def mock_file_priority(idx, prio=None):
        if prio is not None:
            return None
        return 4 if idx in (0, 1) else 0

    mock_handle = MagicMock()
    mock_handle.is_valid.return_value = True
    mock_handle.status.return_value = mock_status
    mock_handle.torrent_file.return_value = mock_ti
    mock_handle.file_progress.return_value = [500000, 2000]
    mock_handle.file_priority.side_effect = mock_file_priority

    engine.handle = mock_handle

    # 1. Update file status
    files_status = engine.update_files_status()
    assert len(files_status) == 2
    assert files_status[0]["index"] == 0
    assert files_status[0]["path"] == "Ubuntu_ISO/ubuntu.iso"
    assert files_status[0]["size"] == 1000000
    assert files_status[0]["downloaded"] == 500000
    assert files_status[0]["progress"] == 0.5
    assert files_status[0]["priority"] == 4

    assert files_status[1]["index"] == 1
    assert files_status[1]["downloaded"] == 2000
    assert files_status[1]["progress"] == 1.0

    # Task files_status should be updated
    assert task.files_status == files_status

    # 2. Priority change on the fly
    ok = engine.set_file_priority(0, 0)
    assert ok is True
    mock_handle.file_priority.assert_called_with(0, 0)
    assert task.files_status[0]["priority"] == 0


@pytest.mark.asyncio
async def test_queue_manager_torrent_files_status_and_db(tmp_path: Path):
    """
    Test that QueueManager correctly exposes get_torrent_files_status and
    set_file_priority, updating the in-memory task and persisting to SQLite.
    """
    store = StateStore(tmp_path / "test_tasks.db")
    ad = NetworkAdapter(name="Wi-Fi", friendly_name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    await pool.initialize()

    try:
        qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

        task = await qm.add_url(
            url="magnet:?xt=urn:btih:1111111111111111111111111111111111111111&dn=TestTorrent",
            destination_dir=tmp_path,
            filename="TestTorrent",
            task_type="TORRENT",
            info_hash="1111111111111111111111111111111111111111",
            start_paused=True,
        )

        # Initially files_status may be None
        assert qm.get_torrent_files_status(task.task_id) == []

        # Manually assign files_status simulating resolution or initial load
        initial_files = [
            {"index": 0, "path": "TestTorrent/fileA.bin", "size": 5000, "downloaded": 0, "progress": 0.0, "priority": 4},
            {"index": 1, "path": "TestTorrent/fileB.bin", "size": 10000, "downloaded": 0, "progress": 0.0, "priority": 1},
        ]
        task.files_status = initial_files
        store.save_task(task)

        # QueueManager should retrieve files_status
        st = qm.get_torrent_files_status(task.task_id)
        assert len(st) == 2
        assert st[0]["path"] == "TestTorrent/fileA.bin"

        # Set file priority via QueueManager
        qm.set_file_priority(task.task_id, 0, 0)
        st_updated = qm.get_torrent_files_status(task.task_id)
        assert st_updated[0]["priority"] == 0

        # Verify SQLite persistence
        loaded = store.get_task(task.task_id)
        assert loaded is not None
        assert loaded.files_status is not None
        assert loaded.files_status[0]["priority"] == 0
    finally:
        await pool.close()


