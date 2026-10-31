import asyncio
import pytest
from pathlib import Path

from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.engine.chunk_planner import Chunk
from core.cnl.models import CNLDecryptedPackage
from core.network.session_pool import MultiInterfaceSessionPool
from core.network.models import NetworkAdapter


def test_state_store_task_lifecycle(tmp_path: Path):
    db_file = tmp_path / "state.db"
    store = StateStore(db_file)

    task = DownloadTask(
        url="https://example.com/archive.zip",
        destination_dir=tmp_path,
        package_name="SummerPhotos",
        filename="archive.zip",
        total_size=50000,
    )
    store.save_task(task)

    pending = store.get_pending_tasks()
    assert len(pending) == 1
    assert pending[0].task_id == task.task_id
    assert pending[0].package_name == "SummerPhotos"
    assert pending[0].status == DownloadStatus.PENDING

    # Update progress
    store.update_task_progress(task.task_id, 25000, DownloadStatus.DOWNLOADING)
    pending_updated = store.get_pending_tasks()
    assert pending_updated[0].downloaded_bytes == 25000
    assert pending_updated[0].status == DownloadStatus.DOWNLOADING

    # Mark completed
    store.update_task_status(task.task_id, DownloadStatus.COMPLETED)
    assert len(store.get_pending_tasks()) == 0


def test_state_store_chunk_persistence(tmp_path: Path):
    db_file = tmp_path / "state_chunks.db"
    store = StateStore(db_file)
    task_id = "test-task-123"

    # Must save a task first for foreign key integrity
    task = DownloadTask(
        url="https://example.com/file.bin",
        destination_dir=tmp_path,
        task_id=task_id,
    )
    store.save_task(task)

    chunks = [
        Chunk(chunk_id=0, start_byte=0, end_byte=999, bytes_downloaded=500, is_completed=False),
        Chunk(chunk_id=1, start_byte=1000, end_byte=1999, bytes_downloaded=1000, is_completed=True),
    ]
    store.save_chunks(task_id, chunks)

    loaded = store.load_chunks(task_id)
    assert len(loaded) == 2
    assert loaded[0].chunk_id == 0
    assert loaded[0].bytes_downloaded == 500
    assert not loaded[0].is_completed
    assert loaded[1].chunk_id == 1
    assert loaded[1].is_completed


@pytest.mark.asyncio
async def test_queue_manager_cnl_dispatch(tmp_path: Path):
    db_file = tmp_path / "queue_test.db"
    store = StateStore(db_file)

    adapter = NetworkAdapter(name="test_eth", ip="127.0.0.1")
    pool = MultiInterfaceSessionPool(adapters=[adapter])

    qm = QueueManager(
        session_pool=pool,
        state_store=store,
        default_download_dir=tmp_path / "downloads",
        max_active_tasks=2,
    )

    pkg = CNLDecryptedPackage(
        package_name="SamplePackage",
        urls=[
            "https://example.com/part1.rar",
            "https://example.com/part2.rar",
        ],
    )

    tasks = await qm.on_cnl_package(pkg)
    assert len(tasks) == 2
    assert tasks[0].package_name == "SamplePackage"
    assert tasks[1].package_name == "SamplePackage"

    # Tasks should be saved in DB
    all_db = store.get_all_tasks()
    assert len(all_db) == 2


@pytest.mark.asyncio
async def test_filename_sanitization_basename(tmp_path: Path):
    store = StateStore(tmp_path / "sanitize.db")
    pool = MultiInterfaceSessionPool(adapters=[NetworkAdapter(name="loop", ip="127.0.0.1")])
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    # 1. Windows path in filename
    t1 = await qm.add_url(
        url="https://example.com/test",
        filename=r"C:\Users\carlo\Downloads\setup_installer.exe",
    )
    assert t1.filename == "setup_installer.exe"

    # 2. POSIX path in filename
    t2 = await qm.add_url(
        url="https://example.com/test2",
        filename="/var/downloads/linux_image.iso",
    )
    assert t2.filename == "linux_image.iso"

    # 3. Reload from DB to confirm persistence of clean filename
    t1_loaded = store.get_task(t1.task_id)
    assert t1_loaded is not None
    assert t1_loaded.filename == "setup_installer.exe"


@pytest.mark.asyncio
async def test_clear_inactive_tasks(tmp_path: Path):
    store = StateStore(tmp_path / "clear_tasks.db")
    pool = MultiInterfaceSessionPool(adapters=[NetworkAdapter(name="loop", ip="127.0.0.1")])
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    # Add a completed task
    t_comp = await qm.add_url("https://example.com/1", filename="comp.bin")
    t_comp.status = DownloadStatus.COMPLETED
    t_comp.downloaded_bytes = 1000
    store.save_task(t_comp)

    # Add a failed task
    t_fail = await qm.add_url("https://example.com/2", filename="fail.bin")
    t_fail.status = DownloadStatus.FAILED
    store.save_task(t_fail)

    # Add a 0-byte paused task (ghost task)
    t_pause_zero = await qm.add_url("https://example.com/3", filename="ghost.bin")
    t_pause_zero.status = DownloadStatus.PAUSED
    t_pause_zero.downloaded_bytes = 0
    store.save_task(t_pause_zero)

    # Add a paused task with progress (>0 bytes)
    t_pause_real = await qm.add_url("https://example.com/4", filename="real_paused.bin")
    t_pause_real.status = DownloadStatus.PAUSED
    t_pause_real.downloaded_bytes = 500
    store.save_task(t_pause_real)

    # Add an active downloading task
    t_active = await qm.add_url("https://example.com/5", filename="active.bin")
    t_active.status = DownloadStatus.DOWNLOADING
    t_active.downloaded_bytes = 100
    store.save_task(t_active)

    # Clear inactive tasks
    deleted_count = await qm.clear_inactive_tasks()
    assert deleted_count == 3  # t_comp, t_fail, t_pause_zero

    remaining_tasks = qm.get_all_tasks()
    remaining_ids = {t.task_id for t in remaining_tasks}
    assert t_active.task_id in remaining_ids
    assert t_pause_real.task_id in remaining_ids
    assert t_comp.task_id not in remaining_ids
    assert t_fail.task_id not in remaining_ids
    assert t_pause_zero.task_id not in remaining_ids
    pending = store.get_pending_tasks()
    assert len(pending) == 2
