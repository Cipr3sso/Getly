import asyncio
import pytest
from aiohttp import web
from pathlib import Path

from core.engine.site_grabber import SiteGrabber, GrabbedResource
from core.scheduler.categories import (
    CATEGORY_COMPRESSED,
    CATEGORY_PROGRAMS,
    CATEGORY_VIDEO,
    CATEGORY_AUDIO,
    CATEGORY_IMAGES_ISO,
)
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from ui.coordinator import UICoordinator
from ui.components.site_grabber_dialog import SiteGrabberDialog


@pytest.fixture
def mock_html():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Test Page with Downloads</title>
        <link rel="stylesheet" href="/assets/style.css">
        <script src="/assets/app.js"></script>
        <script src="https://www.google-analytics.com/analytics.js"></script>
    </head>
    <body>
        <h1>Download Section</h1>
        <a href="archives/archive_v1.zip">Download Zip</a>
        <a href="/installers/setup.msi">Download Windows Installer</a>
        <a href="https://cdn.example.org/software.dmg">Mac DMG</a>
        <a href="files/manual.pdf">User Manual PDF</a>
        <a href="javascript:void(0)">Click Here</a>
        <a href="mailto:support@example.com">Contact</a>
        <a href="#section-top">Anchor Link</a>

        <div class="gallery">
            <img src="/media/banner.jpg" alt="Banner">
            <img data-src="https://cdn.example.org/photo.webp" alt="Lazy photo">
        </div>

        <video src="/videos/trailer.mp4" controls></video>
        <audio src="/sounds/effect.mp3"></audio>

        <!-- Duplicate to test deduplication -->
        <a href="archives/archive_v1.zip">Download Zip Again</a>
    </body>
    </html>
    """


@pytest.mark.asyncio
async def test_site_grabber_extraction_and_classification(mock_html):
    # Setup test server serving mock HTML
    async def handler(request):
        return web.Response(text=mock_html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/downloads", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        page_url = f"http://127.0.0.1:{port}/downloads"

        grabber = SiteGrabber()
        resources = await grabber.analyze_page(page_url)

        assert len(resources) > 0

        urls = [r.url for r in resources]
        filenames = [r.filename for r in resources]
        categories = [r.category for r in resources]

        # Verify absolute URL resolution
        assert f"http://127.0.0.1:{port}/archives/archive_v1.zip" in urls
        assert f"http://127.0.0.1:{port}/installers/setup.msi" in urls
        assert "https://cdn.example.org/software.dmg" in urls
        assert f"http://127.0.0.1:{port}/files/manual.pdf" in urls
        assert f"http://127.0.0.1:{port}/media/banner.jpg" in urls
        assert "https://cdn.example.org/photo.webp" in urls
        assert f"http://127.0.0.1:{port}/videos/trailer.mp4" in urls
        assert f"http://127.0.0.1:{port}/sounds/effect.mp3" in urls

        # Verify no duplicates
        assert urls.count(f"http://127.0.0.1:{port}/archives/archive_v1.zip") == 1

        # Verify ignored assets are filtered
        for u in urls:
            assert not u.endswith(".css")
            assert not u.endswith(".js")
            assert "google-analytics" not in u
            assert not u.startswith("javascript:")
            assert not u.startswith("mailto:")

        # Verify category categorization
        res_map = {r.url: r for r in resources}
        assert res_map[f"http://127.0.0.1:{port}/archives/archive_v1.zip"].category == CATEGORY_COMPRESSED
        assert res_map[f"http://127.0.0.1:{port}/installers/setup.msi"].category == CATEGORY_PROGRAMS
        assert res_map["https://cdn.example.org/software.dmg"].category == CATEGORY_PROGRAMS
        assert res_map[f"http://127.0.0.1:{port}/videos/trailer.mp4"].category == CATEGORY_VIDEO
        assert res_map[f"http://127.0.0.1:{port}/sounds/effect.mp3"].category == CATEGORY_AUDIO
        assert res_map[f"http://127.0.0.1:{port}/media/banner.jpg"].category == CATEGORY_IMAGES_ISO
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_site_grabber_dialog_smoke(tmp_path):
    db_file = tmp_path / "test_dialog.db"
    store = StateStore(db_file)
    pool = MultiInterfaceSessionPool(max_connections_per_host=2, total_limit=4)
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(qm, pool)

    dialog = SiteGrabberDialog(coordinator=coord, initial_url="https://example.com")
    assert dialog.url_field.value == "https://example.com"
    assert dialog.title is not None
    assert dialog.content is not None

    # Test batch filter helper
    dialog.discovered_items = [
        GrabbedResource(url="https://ex.com/a.zip", filename="a.zip", category="Compressi", source_tag="a"),
        GrabbedResource(url="https://ex.com/b.pdf", filename="b.pdf", category="Documenti", source_tag="a"),
        GrabbedResource(url="https://ex.com/c.mp4", filename="c.mp4", category="Video", source_tag="video"),
    ]
    dialog._select_by_filter("ARCHIVES")
    assert dialog.selected_urls["https://ex.com/a.zip"] is True
    assert dialog.selected_urls["https://ex.com/b.pdf"] is False
    assert dialog.selected_urls["https://ex.com/c.mp4"] is False

    dialog._select_by_filter("ALL")
    assert all(dialog.selected_urls.values())

    await pool.close()
