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
async def test_site_grabber_crawl_depth():
    page_1_html = """
    <html><body>
        <h1>Page 1</h1>
        <a href="/file1.zip">File 1</a>
        <a href="/subpage1.html">Subpage 1</a>
    </body></html>
    """

    page_2_html = """
    <html><body>
        <h1>Subpage 1</h1>
        <a href="/file2.rar">File 2</a>
        <a href="/subpage2.html">Subpage 2</a>
    </body></html>
    """

    page_3_html = """
    <html><body>
        <h1>Subpage 2</h1>
        <a href="/file3.7z">File 3</a>
    </body></html>
    """

    async def h1(req): return web.Response(text=page_1_html, content_type="text/html")
    async def h2(req): return web.Response(text=page_2_html, content_type="text/html")
    async def h3(req): return web.Response(text=page_3_html, content_type="text/html")

    app = web.Application()
    app.router.add_get("/", h1)
    app.router.add_get("/subpage1.html", h2)
    app.router.add_get("/subpage2.html", h3)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/"

    try:
        grabber = SiteGrabber()

        # Depth 1: should only discover file1.zip
        res_d1 = await grabber.crawl(base_url, depth=1)
        urls_d1 = [r.url for r in res_d1]
        assert f"http://127.0.0.1:{port}/file1.zip" in urls_d1
        assert f"http://127.0.0.1:{port}/file2.rar" not in urls_d1

        # Depth 2: should discover file1.zip and file2.rar
        res_d2 = await grabber.crawl(base_url, depth=2)
        urls_d2 = [r.url for r in res_d2]
        assert f"http://127.0.0.1:{port}/file1.zip" in urls_d2
        assert f"http://127.0.0.1:{port}/file2.rar" in urls_d2
        assert f"http://127.0.0.1:{port}/file3.7z" not in urls_d2

        # Depth 3: should discover all 3 files
        res_d3 = await grabber.crawl(base_url, depth=3)
        urls_d3 = [r.url for r in res_d3]
        assert f"http://127.0.0.1:{port}/file1.zip" in urls_d3
        assert f"http://127.0.0.1:{port}/file2.rar" in urls_d3
        assert f"http://127.0.0.1:{port}/file3.7z" in urls_d3
    finally:
        await runner.cleanup()


def test_site_grabber_fragment_and_hoster_extraction():
    # 1. URL with fragment containing multi-part archive filename
    url1 = "https://fuckingfast.co/dqy8n6yocv2t#CoD_MW4_--_fitgirl-repacks.site_--_.part01.rar"
    fn1, ext1, cat1 = SiteGrabber._extract_file_info(url1)
    assert fn1 == "CoD_MW4_--_fitgirl-repacks.site_--_.part01.rar"
    assert ext1 == ".rar"
    assert cat1 == CATEGORY_COMPRESSED

    # 2. Plain navigation link without extension should return None
    url2 = "https://bit.ly/fgpaste"
    fn2, ext2, cat2 = SiteGrabber._extract_file_info(url2)
    assert fn2 is None

    # 3. Paste link without file extension should return None
    url3 = "https://paste.fitgirl-repacks.site/?537b1bf966486939"
    fn3, ext3, cat3 = SiteGrabber._extract_file_info(url3)
    assert fn3 is None

    # 4. Known hoster with resource path
    url4 = "https://datanodes.to/dhv6pa37tsmz/archive.rar"
    fn4, ext4, cat4 = SiteGrabber._extract_file_info(url4)
    assert fn4 == "archive.rar"
    assert cat4 == CATEGORY_COMPRESSED



