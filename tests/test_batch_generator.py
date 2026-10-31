import pytest
from core.scheduler.batch import generate_batch_urls
from ui.components.batch_dialog import BatchDownloadDialog
from ui.coordinator import UICoordinator
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadTask, DownloadStatus


def test_batch_generator_numeric_padding():
    urls = generate_batch_urls("https://example.com/archive_[01-05].zip")
    assert urls == [
        "https://example.com/archive_01.zip",
        "https://example.com/archive_02.zip",
        "https://example.com/archive_03.zip",
        "https://example.com/archive_04.zip",
        "https://example.com/archive_05.zip",
    ]


def test_batch_generator_numeric_unpadded_and_step():
    urls = generate_batch_urls("https://example.com/data_[1-9:2].tar.gz")
    assert urls == [
        "https://example.com/data_1.tar.gz",
        "https://example.com/data_3.tar.gz",
        "https://example.com/data_5.tar.gz",
        "https://example.com/data_7.tar.gz",
        "https://example.com/data_9.tar.gz",
    ]


def test_batch_generator_alphabetic_ranges():
    # Lowercase
    urls_lower = generate_batch_urls("https://cdn.example.org/chunk_[a-d].bin")
    assert urls_lower == [
        "https://cdn.example.org/chunk_a.bin",
        "https://cdn.example.org/chunk_b.bin",
        "https://cdn.example.org/chunk_c.bin",
        "https://cdn.example.org/chunk_d.bin",
    ]

    # Uppercase with step
    urls_upper = generate_batch_urls("https://cdn.example.org/slice_[A-E:2].iso")
    assert urls_upper == [
        "https://cdn.example.org/slice_A.iso",
        "https://cdn.example.org/slice_C.iso",
        "https://cdn.example.org/slice_E.iso",
    ]


def test_batch_generator_multiline_and_plain():
    text = """
    https://example.com/simple_file.zip
    https://example.com/part_[1-3].mp4
    """
    urls = generate_batch_urls(text)
    assert len(urls) == 4
    assert urls[0] == "https://example.com/simple_file.zip"
    assert urls[1] == "https://example.com/part_1.mp4"
    assert urls[2] == "https://example.com/part_2.mp4"
    assert urls[3] == "https://example.com/part_3.mp4"


def test_batch_generator_max_limit():
    urls = generate_batch_urls("https://example.com/num_[1-100].dat", max_limit=10)
    assert len(urls) == 10
    assert urls[0] == "https://example.com/num_1.dat"
    assert urls[-1] == "https://example.com/num_10.dat"


def test_batch_dialog_preview_and_interaction(tmp_path):
    from core.network.session_pool import MultiInterfaceSessionPool
    pool = MultiInterfaceSessionPool(adapters=[])
    store = StateStore(tmp_path / "test_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coordinator = UICoordinator(queue_manager=qm, session_pool=pool)
    dialog = BatchDownloadDialog(coordinator=coordinator)

    # Initial state
    assert dialog.btn_add_all.disabled is True

    # Simulate entering a pattern
    dialog.pattern_input.value = "https://server.com/img_[001-005].jpg"
    dialog._on_pattern_change(None)

    assert dialog.btn_add_all.disabled is False
    assert "5" in dialog.btn_add_all.content
    assert len(dialog.preview_urls_column.controls) == 5
    assert "img_001.jpg" in dialog.preview_urls_column.controls[0].value
    assert "img_005.jpg" in dialog.preview_urls_column.controls[-1].value


@pytest.mark.asyncio
async def test_queue_manager_update_task_url(tmp_path):
    from core.network.session_pool import MultiInterfaceSessionPool
    pool = MultiInterfaceSessionPool(adapters=[])
    store = StateStore(tmp_path / "test_qm.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)

    task = await qm.add_url(
        url="https://expired.example.com/old_token/file.iso",
        destination_dir=tmp_path,
        filename="file.iso",
        start_paused=True,
    )
    task.status = DownloadStatus.FAILED
    task.error_message = "HTTP 403 Forbidden: Expired signature"
    store.update_task_status(task.task_id, DownloadStatus.FAILED, task.error_message)

    saved_task = store.get_task(task.task_id)
    assert saved_task.status == DownloadStatus.FAILED
    assert "expired" in saved_task.url

    # Update URL without auto-restarting
    updated = await qm.update_task_url(
        task_id=task.task_id,
        new_url="https://valid.example.com/new_fresh_token/file.iso",
        restart=False,
    )
    assert updated is True

    # Verify task state in memory
    refreshed = qm.get_task(task.task_id)
    assert refreshed.url == "https://valid.example.com/new_fresh_token/file.iso"
    assert refreshed.error_message is None
    assert refreshed.status == DownloadStatus.PAUSED

    # Verify persistence in sqlite
    db_task = store.get_task(task.task_id)
    assert db_task.url == "https://valid.example.com/new_fresh_token/file.iso"
    assert db_task.error_message is None
