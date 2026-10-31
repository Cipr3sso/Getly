import hashlib
from pathlib import Path
import pytest
import flet as ft

from core.scheduler.task import DownloadTask
from ui.components.hash_dialog import HashDialog, compute_file_hashes
from ui.coordinator import UICoordinator


def test_compute_file_hashes_accuracy(tmp_path):
    test_file = tmp_path / "sample.bin"
    content = b"Getly Resilient Download Engine - Checksum Test Payload 1234567890"
    test_file.write_bytes(content)

    expected_md5 = hashlib.md5(content).hexdigest().lower()
    expected_sha1 = hashlib.sha1(content).hexdigest().lower()
    expected_sha256 = hashlib.sha256(content).hexdigest().lower()

    progress_reports = []

    def on_progress(p: float):
        progress_reports.append(p)

    results = compute_file_hashes(test_file, on_progress=on_progress, chunk_size=16)

    assert results["md5"] == expected_md5
    assert results["sha1"] == expected_sha1
    assert results["sha256"] == expected_sha256
    assert len(progress_reports) > 0
    assert progress_reports[-1] == 1.0


def test_compute_file_hashes_non_existent(tmp_path):
    missing_file = tmp_path / "does_not_exist.bin"
    with pytest.raises(FileNotFoundError):
        compute_file_hashes(missing_file)


@pytest.mark.asyncio
async def test_hash_dialog_ui_and_validation(tmp_path):
    from core.network.session_pool import MultiInterfaceSessionPool
    from core.scheduler.queue_manager import QueueManager
    from core.scheduler.state_store import StateStore

    test_file = tmp_path / "test_artifact.dat"
    data = b"Testing WinUI 3 Hash Dialog with matching and mismatching inputs"
    test_file.write_bytes(data)

    task = DownloadTask(
        url="https://example.com/test_artifact.dat",
        destination_dir=tmp_path,
        filename="test_artifact.dat",
    )

    pool = MultiInterfaceSessionPool(adapters=[])
    store = StateStore(tmp_path / "test_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coordinator = UICoordinator(queue_manager=qm, session_pool=pool)
    dialog = HashDialog(task=task, coordinator=coordinator)

    assert dialog.title is not None
    assert dialog.md5_field is not None
    assert dialog.sha256_field is not None
    assert dialog.match_badge.visible is False

    # Run the computation directly
    await dialog._compute_hashes_async()

    expected_sha256 = hashlib.sha256(data).hexdigest().lower()
    assert dialog._computed_hashes["sha256"] == expected_sha256
    assert dialog.sha256_field._text_ctrl.value == expected_sha256

    # Test empty compare input -> hidden badge
    dialog._validate_hash_comparison("")
    assert dialog.match_badge.visible is False

    # Test correct SHA-256 match
    dialog._validate_hash_comparison(expected_sha256.upper())  # case insensitive
    assert dialog.match_badge.visible is True
    assert "✅" in dialog.match_badge.content.controls[1].value

    # Test incorrect hash -> mismatch feedback
    dialog._validate_hash_comparison("1234567890abcdefdeadbeef")
    assert dialog.match_badge.visible is True
    assert "❌" in dialog.match_badge.content.controls[1].value
