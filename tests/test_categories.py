import pytest
from pathlib import Path
import tempfile
from core.scheduler.categories import (
    CATEGORY_COMPRESSED,
    CATEGORY_PROGRAMS,
    CATEGORY_VIDEO,
    CATEGORY_AUDIO,
    CATEGORY_DOCUMENTS,
    CATEGORY_IMAGES_ISO,
    CATEGORY_OTHER,
    ALL_CATEGORIES,
    detect_category,
    get_category_subfolder,
)
from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.network.session_pool import MultiInterfaceSessionPool


def test_detect_category_by_extension():
    # Compressi
    assert detect_category("archive.zip") == CATEGORY_COMPRESSED
    assert detect_category("archive.rar") == CATEGORY_COMPRESSED
    assert detect_category("archive.7z") == CATEGORY_COMPRESSED
    assert detect_category("archive.tar.gz") == CATEGORY_COMPRESSED
    assert detect_category("archive.bz2") == CATEGORY_COMPRESSED
    assert detect_category("archive.xz") == CATEGORY_COMPRESSED

    # Programmi
    assert detect_category("setup.exe") == CATEGORY_PROGRAMS
    assert detect_category("installer.msi") == CATEGORY_PROGRAMS
    assert detect_category("app.apk") == CATEGORY_PROGRAMS
    assert detect_category("app.dmg") == CATEGORY_PROGRAMS
    assert detect_category("package.deb") == CATEGORY_PROGRAMS
    assert detect_category("program.AppImage") == CATEGORY_PROGRAMS

    # Video
    assert detect_category("movie.mp4") == CATEGORY_VIDEO
    assert detect_category("clip.mkv") == CATEGORY_VIDEO
    assert detect_category("film.avi") == CATEGORY_VIDEO
    assert detect_category("video.webm") == CATEGORY_VIDEO

    # Audio
    assert detect_category("song.mp3") == CATEGORY_AUDIO
    assert detect_category("track.flac") == CATEGORY_AUDIO
    assert detect_category("sound.wav") == CATEGORY_AUDIO
    assert detect_category("podcast.m4a") == CATEGORY_AUDIO

    # Documenti & 3D
    assert detect_category("paper.pdf") == CATEGORY_DOCUMENTS
    assert detect_category("sheet.xlsx") == CATEGORY_DOCUMENTS
    assert detect_category("doc.docx") == CATEGORY_DOCUMENTS
    assert detect_category("model.stl") == CATEGORY_DOCUMENTS
    assert detect_category("cad.step") == CATEGORY_DOCUMENTS

    # Immagini & ISO
    assert detect_category("os.iso") == CATEGORY_IMAGES_ISO
    assert detect_category("disk.img") == CATEGORY_IMAGES_ISO
    assert detect_category("photo.jpg") == CATEGORY_IMAGES_ISO
    assert detect_category("image.png") == CATEGORY_IMAGES_ISO
    assert detect_category("graphic.webp") == CATEGORY_IMAGES_ISO

    # Altro
    assert detect_category("unknown.dat") == CATEGORY_OTHER
    assert detect_category("file_without_extension") == CATEGORY_OTHER


def test_detect_category_by_mime_and_url_query():
    # MIME detection
    assert detect_category("download", mime_type="application/zip") == CATEGORY_COMPRESSED
    assert detect_category("stream", mime_type="video/mp4; charset=binary") == CATEGORY_VIDEO
    assert detect_category("audio_stream", mime_type="audio/mpeg") == CATEGORY_AUDIO
    assert detect_category("document", mime_type="application/pdf") == CATEGORY_DOCUMENTS
    assert detect_category("avatar", mime_type="image/png") == CATEGORY_IMAGES_ISO

    # URL query parameter
    url_with_param = "https://example.com/api/get?file=documento_importante.pdf&auth=123"
    assert detect_category(url_with_param) == CATEGORY_DOCUMENTS

    url_zip = "https://example.com/download.php?name=backup_2026.7z"
    assert detect_category(url_zip) == CATEGORY_COMPRESSED


def test_get_category_subfolder():
    base = Path("C:/Downloads/ResilientDownloader")
    sub = get_category_subfolder(base, CATEGORY_VIDEO)
    assert sub == base / "Video"

    sub_comp = get_category_subfolder(base, CATEGORY_COMPRESSED)
    assert sub_comp == base / "Compressi"

    sub_fallback = get_category_subfolder(base, "NonExistentCategory")
    assert sub_fallback == base / "Altro"


def test_category_persistence_in_state_store(tmp_path):
    db_file = tmp_path / "test_state.db"
    store = StateStore(db_file)

    task = DownloadTask(
        url="https://example.com/test.zip",
        destination_dir=tmp_path / "Compressi",
        filename="test.zip",
        category=CATEGORY_COMPRESSED,
    )
    store.save_task(task)

    loaded = store.get_task(task.task_id)
    assert loaded is not None
    assert loaded.category == CATEGORY_COMPRESSED
    assert loaded.filename == "test.zip"


@pytest.mark.asyncio
async def test_queue_manager_category_auto_routing(tmp_path):
    db_file = tmp_path / "test_qm.db"
    store = StateStore(db_file)
    pool = MultiInterfaceSessionPool(max_connections_per_host=2, total_limit=4)

    base_dir = tmp_path / "Downloads"
    qm = QueueManager(
        session_pool=pool,
        state_store=store,
        default_download_dir=base_dir,
    )

    # 1. Add URL without specifying destination_dir -> auto-routed to category subfolder
    task_zip = await qm.add_url("https://example.com/archive.zip")
    assert task_zip.category == CATEGORY_COMPRESSED
    assert task_zip.destination_dir == base_dir / "Compressi"
    assert task_zip.status == DownloadStatus.PENDING

    # 2. Add URL in paused mode
    task_pdf = await qm.add_url("https://example.com/manual.pdf", start_paused=True)
    assert task_pdf.category == CATEGORY_DOCUMENTS
    assert task_pdf.destination_dir == base_dir / "Documenti"
    assert task_pdf.status == DownloadStatus.PAUSED

    await pool.close()


def test_add_download_dialog_category_and_folder_sync(tmp_path):
    from ui.components.add_download_dialog import AddDownloadDialog
    from ui.coordinator import UICoordinator

    store = StateStore(tmp_path / "test_dialog_sync.db")
    pool = MultiInterfaceSessionPool(max_connections_per_host=2, total_limit=4)
    base_dir = tmp_path / "Downloads"
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=base_dir)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    # Initial dialog with prefilled zip url
    dlg = AddDownloadDialog(
        coordinator=coord,
        prefill_url="https://example.com/software.zip",
    )
    assert dlg.current_category == CATEGORY_COMPRESSED
    assert dlg.category_dropdown.value == CATEGORY_COMPRESSED
    assert dlg.dir_field.value == str(base_dir / "Compressi")
    assert dlg.filename_field.value == "software.zip"

    # User changes category to Video
    dlg.category_dropdown.value = CATEGORY_VIDEO
    class FakeEvent:
        control = dlg.category_dropdown
        page = None
    dlg._on_category_changed(FakeEvent())

    assert dlg.current_category == CATEGORY_VIDEO
    assert dlg.dir_field.value == str(base_dir / "Video")
