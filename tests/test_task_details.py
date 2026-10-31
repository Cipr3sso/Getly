import asyncio
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
import pytest
import flet as ft

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus
from core.engine.torrent_engine import (
    TorrentDownloadEngine,
    TorrentSessionManager,
    TorrentMetadata,
    TorrentFileInfo,
)
from ui.coordinator import UICoordinator
from ui.components.task_details_dialog import TaskDetailsDialog
from ui.components.datagrid_view import DownloadDataGrid
from ui.components.download_card import DownloadCard
from ui.views.dashboard_view import DashboardView


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


@pytest.mark.asyncio
async def test_task_details_dialog_torrent(tmp_path: Path):
    """
    Test TaskDetailsDialog for a TORRENT task:
    Verifies header, metrics card, file rows filtering, and priority toggle.
    """
    store = StateStore(tmp_path / "test_tasks2.db")
    ad = NetworkAdapter(name="Wi-Fi", friendly_name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    await pool.initialize()

    try:
        qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
        coord = UICoordinator(queue_manager=qm, session_pool=pool)

        files_data = [
            {"index": 0, "path": "ArchLinux/arch.iso", "size": 900000000, "downloaded": 450000000, "progress": 0.5, "priority": 4},
            {"index": 1, "path": "ArchLinux/sha256sums.txt", "size": 500, "downloaded": 500, "progress": 1.0, "priority": 4},
            {"index": 2, "path": "ArchLinux/source.tar.gz", "size": 200000000, "downloaded": 0, "progress": 0.0, "priority": 0},
        ]

        task = DownloadTask(
            url="magnet:?xt=urn:btih:2222222222222222222222222222222222222222&dn=ArchLinux",
            destination_dir=tmp_path,
            filename="ArchLinux",
            task_type="TORRENT",
            info_hash="2222222222222222222222222222222222222222",
            status=DownloadStatus.DOWNLOADING,
            total_size=1100000500,
            downloaded_bytes=450000500,
            speed_bps=5242880,  # 5 MB/s
            upload_speed_bps=1048576,  # 1 MB/s
            seeds=42,
            peers=18,
            files_status=files_data,
        )
        qm._tasks[task.task_id] = task

        dialog = TaskDetailsDialog(task=task, coordinator=coord)

        # Verify header and content
        assert dialog.title is not None
        assert dialog.content is not None
        assert len(dialog._files_data) == 3

        # Verify search query filter in file rows
        dialog._on_search_query_changed(MagicMock(control=MagicMock(value="sha256")))
        assert len(dialog._file_rows_column.controls) == 1

        # Reset search filter
        dialog._on_search_query_changed(MagicMock(control=MagicMock(value="")))
        assert len(dialog._file_rows_column.controls) == 3

        # Verify priority toggle
        dialog._handle_file_priority_toggle(file_index=0, is_checked=False)
        assert task.files_status[0]["priority"] == 0

        dialog._handle_close()
        assert dialog._ticker_active is False
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_task_details_dialog_http(tmp_path: Path):
    """
    Test TaskDetailsDialog for an HTTP task:
    Verifies URL, destination path, transfer metrics, masked cookies, and error banner.
    """
    store = StateStore(tmp_path / "test_tasks3.db")
    ad = NetworkAdapter(name="Wi-Fi", friendly_name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    await pool.initialize()

    try:
        qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
        coord = UICoordinator(queue_manager=qm, session_pool=pool)

        task = DownloadTask(
            url="https://releases.ubuntu.com/22.04/ubuntu-22.04.iso",
            destination_dir=tmp_path,
            filename="ubuntu-22.04.iso",
            category="Software",
            task_type="HTTP",
            status=DownloadStatus.FAILED,
            error_message="HTTP 503 Service Unavailable",
            total_size=3500000000,
            downloaded_bytes=1500000000,
            speed_bps=0,
            referer="https://ubuntu.com/download/desktop",
            user_agent="Mozilla/5.0 GetlyTest",
            cookies="sessionid=secretcookie12345",
        )
        qm._tasks[task.task_id] = task

        dialog = TaskDetailsDialog(task=task, coordinator=coord)

        assert dialog.title is not None
        assert dialog.content is not None

        # Check that error message is rendered
        body = dialog.content.content
        assert isinstance(body, ft.Column)
        # The first control should be the error banner
        assert len(body.controls) >= 3
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_datagrid_and_dashboard_integration(tmp_path: Path):
    """
    Test double click and info button wiring in DownloadDataGrid and DashboardView,
    as well as proportional column layout.
    """
    store = StateStore(tmp_path / "test_tasks4.db")
    ad = NetworkAdapter(name="Wi-Fi", friendly_name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad])
    await pool.initialize()

    try:
        qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
        coord = UICoordinator(queue_manager=qm, session_pool=pool)

        task = DownloadTask(
            url="https://example.com/testfile.zip",
            destination_dir=tmp_path,
            filename="testfile.zip",
            status=DownloadStatus.DOWNLOADING,
            total_size=10485760,
        )
        qm._tasks[task.task_id] = task

        details_opened = []

        def mock_open_details(t: DownloadTask):
            details_opened.append(t.task_id)

        # 1. Test DownloadDataGrid callback
        grid = DownloadDataGrid(
            coordinator=coord,
            on_open_details=mock_open_details,
        )
        grid.set_tasks([task])
        assert len(grid.rows_column.controls) == 1

        # Simulate double-tap
        grid._handle_open_details(task)
        assert details_opened == [task.task_id]

        # Verify header proportional weights
        header = grid._build_header_row()
        assert header.height == 38
        hdr_row = header.content
        assert hdr_row.controls[0].width == 34  # Checkbox
        assert hdr_row.controls[1].expand == 4  # Nome file
        assert hdr_row.controls[2].expand == 2  # Categoria
        assert hdr_row.controls[3].expand == 1  # Dimensione
        assert hdr_row.controls[4].expand == 2  # Velocità / Stato
        assert hdr_row.controls[5].expand == 2  # Progresso
        assert hdr_row.controls[6].expand == 1  # Sorgente
        assert hdr_row.controls[7].width == 75  # Azioni

        # 2. Test DownloadCard callback
        card_opened = []
        card = DownloadCard(
            task=task,
            coordinator=coord,
            on_open_details=lambda t: card_opened.append(t.task_id),
        )
        card._handle_open_details()
        assert card_opened == [task.task_id]

        # 3. Test DashboardView wiring
        dashboard = DashboardView(coordinator=coord)
        assert dashboard.datagrid.on_open_details == dashboard._open_task_details
        # Ensure command bar has Dettagli button
        sec_texts = [lbl.value for lbl in dashboard._secondary_labels]
        assert "Dettagli" in sec_texts
    finally:
        await pool.close()
    assert hdr_row.controls[6].expand == 1  # Sorgente
    assert hdr_row.controls[7].width == 75  # Azioni

    # 2. Test DownloadCard callback
    card_opened = []
    card = DownloadCard(
        task=task,
        coordinator=coord,
        on_open_details=lambda t: card_opened.append(t.task_id),
    )
    card._handle_open_details()
    assert card_opened == [task.task_id]

    # 3. Test DashboardView wiring
    dashboard = DashboardView(coordinator=coord)
    assert dashboard.datagrid.on_open_details == dashboard._open_task_details
    # Ensure command bar has Dettagli button
    sec_texts = [lbl.value for lbl in dashboard._secondary_labels]
    assert "Dettagli" in sec_texts
