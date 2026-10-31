import asyncio
import os
from pathlib import Path
import tempfile
import pytest

import libtorrent as lt

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


def test_download_card_torrent_ui():
    from ui.components.download_card import DownloadCard
    from ui.coordinator import UICoordinator
    import flet as ft

    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        pool = MultiInterfaceSessionPool([])
        qm = QueueManager(pool, store, Path(tmpdir))
        coord = UICoordinator(qm, pool)

        torrent_task = DownloadTask(
            url="magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=test.iso",
            destination_dir=Path(tmpdir),
            filename="test.iso",
            task_type="TORRENT",
            category=CATEGORY_TORRENTS,
            seeds=12,
            peers=30,
            status=DownloadStatus.DOWNLOADING,
            total_size=1073741824,
            downloaded_bytes=536870912,
            speed_bps=5242880.0,
            upload_speed_bps=1048576.0,
            pieces_total=500,
            pieces_completed=250,
        )

        card = DownloadCard(torrent_task, coord)
        assert card is not None
        # Check that card content builds and contains expected layout
        col = card.content
        assert isinstance(col, ft.Column)
        header_row = col.controls[0]
        assert isinstance(header_row, ft.Row)

        # Requirement 2: Verify duplicate badge elimination
        # For a generic torrent task, there should be ONLY 2 badges (TORRENT and Status), NOT 3
        badge_row = header_row.controls[1]
        assert len(badge_row.controls) == 2

        # When a custom category is specified, category badge is retained
        torrent_task.category = "Video"
        card_custom = DownloadCard(torrent_task, coord)
        assert len(card_custom.content.controls[0].controls[1].controls) == 3

        # Requirement 1: Verify completion string does not display ghost seeding / upload metrics
        torrent_task.status = DownloadStatus.COMPLETED
        card.update_task_state(torrent_task)
        metrics_str = card._get_metrics_info()
        assert "Completato con successo" in metrics_str
        assert "Seeds:" not in metrics_str
        assert "▲" not in metrics_str


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


def test_torrent_dialog_initialization():
    from ui.components.torrent_dialog import TorrentDialog
    from ui.coordinator import UICoordinator

    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        pool = MultiInterfaceSessionPool([])
        qm = QueueManager(pool, store, Path(tmpdir))
        coord = UICoordinator(qm, pool)

        dlg = TorrentDialog(
            coordinator=coord,
            source="magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=test.iso",
        )
        assert dlg is not None
        assert dlg.source.startswith("magnet:")
        assert dlg.destination_dir.name == CATEGORY_TORRENTS


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


def test_download_card_in_place_update():
    from ui.components.download_card import DownloadCard
    from ui.coordinator import UICoordinator

    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test_card.db")
        pool = MultiInterfaceSessionPool([])
        qm = QueueManager(pool, store, Path(tmpdir))
        coord = UICoordinator(qm, pool)

        task = DownloadTask(
            url="https://example.com/file.zip",
            destination_dir=Path(tmpdir),
            filename="file.zip",
            status=DownloadStatus.PENDING,
            total_size=1000,
            downloaded_bytes=0,
        )

        card = DownloadCard(task, coord)
        assert card._last_status == DownloadStatus.PENDING
        orig_metrics_text = card._metrics_text
        orig_actions_row = card._actions_row

        task.status = DownloadStatus.DOWNLOADING
        task.downloaded_bytes = 500
        card.update_task_state(task)

        assert card._metrics_text is orig_metrics_text
        assert card._actions_row is orig_actions_row
        assert card._last_status == DownloadStatus.DOWNLOADING
        assert "500" in (card._metrics_text.value or "")


@pytest.mark.asyncio
async def test_torrent_metadata_num_files_and_dialog_update():
    from ui.components.torrent_dialog import TorrentDialog
    from ui.coordinator import UICoordinator

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

        # 1. Verify TorrentMetadata.num_files property
        assert meta.has_metadata is True
        assert meta.num_files == 3
        assert len(meta.files) == 3

        # 2. Test TorrentDialog with this real metadata
        store = StateStore(Path(tmpdir) / "test.db")
        pool = MultiInterfaceSessionPool([])
        qm = QueueManager(pool, store, Path(tmpdir))
        coord = UICoordinator(qm, pool)

        dlg = TorrentDialog(
            coordinator=coord,
            source=str(torrent_file),
            initial_dir=Path(tmpdir),
        )

        # Run _resolve_metadata_async directly
        await dlg._resolve_metadata_async()

        # Check that UI controls updated without error
        assert dlg._is_resolving is False
        assert dlg.metadata is not None
        assert dlg.metadata.num_files == 3
        assert "3 file" in dlg.meta_summary_text.value
        assert len(dlg.file_checkboxes) == 3
        assert dlg.add_paused_btn.disabled is False
        assert dlg.download_now_btn.disabled is False
        assert "3/3" in dlg.selected_size_text.value

        # Toggle one checkbox
        dlg._on_file_toggled(0, False)
        assert "2/3" in dlg.selected_size_text.value


def test_torrent_session_manager_dht_and_anonymous_settings():
    assert TorrentSessionManager.DHT_ROUTERS is not None
    assert len(TorrentSessionManager.DHT_ROUTERS) >= 4
    router_hosts = [r[0] for r in TorrentSessionManager.DHT_ROUTERS]
    assert "router.bittorrent.com" in router_hosts
    assert "dht.transmissionbt.com" in router_hosts
    assert "router.utorrent.com" in router_hosts
    assert "dht.libtorrent.org" in router_hosts

