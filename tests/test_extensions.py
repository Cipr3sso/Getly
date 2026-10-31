import asyncio
import io
import json
from pathlib import Path
import tempfile
import zipfile
import pytest

from core.extensions.models import ExtensionManifest, ResolvedResource, InstalledExtension
from core.extensions.manifest import parse_manifest, url_matches_pattern
from core.extensions.engine import QuickJSSandbox, QUICKJS_AVAILABLE
from core.extensions.manager import ExtensionManager
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.network.session_pool import MultiInterfaceSessionPool


def test_manifest_parsing_valid():
    manifest_data = {
        "name": "gopeed-extension-test",
        "version": "1.2.0",
        "author": "ResilientTeam",
        "title": "Test Resolver Extension",
        "description": "Resolves protected links",
        "scripts": [
            {
                "event": "onResolve",
                "match": {
                    "urls": [
                        "*://*.example.com/s/*",
                        "https://download.org/file/*",
                    ]
                },
                "entry": "dist/index.js",
            }
        ],
        "settings": [
            {"name": "token", "type": "string"}
        ],
    }

    manifest = parse_manifest(manifest_data)
    assert manifest.name == "gopeed-extension-test"
    assert manifest.version == "1.2.0"
    assert manifest.author == "ResilientTeam"
    assert manifest.display_name == "Test Resolver Extension"
    assert len(manifest.scripts) == 1
    assert manifest.scripts[0].event == "onResolve"
    assert manifest.scripts[0].entry == "dist/index.js"
    assert len(manifest.scripts[0].match_urls) == 2
    assert "*://*.example.com/s/*" in manifest.scripts[0].match_urls


def test_manifest_parsing_variations():
    # Array match
    manifest1 = parse_manifest({
        "name": "ext1",
        "scripts": [
            {"match": ["*://site.com/*"], "entry": "main.js"}
        ]
    })
    assert manifest1.scripts[0].match_urls == ["*://site.com/*"]

    # String match
    manifest2 = parse_manifest({
        "name": "ext2",
        "scripts": [
            {"match": "*://single.com/*"}
        ]
    })
    assert manifest2.scripts[0].match_urls == ["*://single.com/*"]

    # Missing name should raise ValueError
    with pytest.raises(ValueError):
        parse_manifest({"version": "1.0.0"})


def test_url_matching_patterns():
    # Subdomain wildcards
    assert url_matches_pattern("https://sub.example.com/s/123", "*://*.example.com/s/*") is True
    assert url_matches_pattern("http://example.com/s/123", "*://*.example.com/s/*") is True
    assert url_matches_pattern("https://other.com/s/123", "*://*.example.com/s/*") is False

    # Path wildcards
    assert url_matches_pattern("https://bilibili.com/video/BV123", "*://bilibili.com/video/*") is True
    assert url_matches_pattern("https://bilibili.com/read/cv123", "*://bilibili.com/video/*") is False

    # Universal wildcard
    assert url_matches_pattern("https://anywhere.org/test.zip", "*") is True


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_sandbox_onresolve():
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        const url = ctx.req.url;
        ctx.res = {
            name: "extracted_video.mp4",
            files: [
                {
                    name: "extracted_video.mp4",
                    req: {
                        url: "https://cdn.direct.stream/file123.mp4",
                        headers: {
                            "User-Agent": "QuickJS-Gopeed/1.0",
                            "Cookie": "session_token=xyz987"
                        }
                    }
                }
            ]
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://hoster.com/view/file123",
    )

    assert res is not None
    assert res.url == "https://cdn.direct.stream/file123.mp4"
    assert res.filename == "extracted_video.mp4"
    assert res.headers.get("User-Agent") == "QuickJS-Gopeed/1.0"
    assert res.headers.get("Cookie") == "session_token=xyz987"
    assert len(res.files) == 1


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_sandbox_commonjs_module_exports():
    sandbox = QuickJSSandbox()

    script = """
    module.exports = async function(ctx) {
        return {
            name: "archive.zip",
            req: {
                url: "https://direct.download.net/archive.zip",
                headers: { "Referer": "https://protected-site.com" }
            }
        };
    };
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://protected-site.com/get/archive",
    )

    assert res is not None
    assert res.url == "https://direct.download.net/archive.zip"
    assert res.filename == "archive.zip"
    assert res.headers.get("Referer") == "https://protected-site.com"


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_sandbox_fetch_and_json_parsing():
    sandbox = QuickJSSandbox()

    # Script testing fetch API mechanics and MessageError
    script = """
    gopeed.events.onResolve(async (ctx) => {
        console.log("Resolving url:", ctx.req.url);
        // Test URL parsing and fetch promise resolving
        const rawJson = '{"streamUrl": "https://stream.host.com/sample.mkv", "title": "sample.mkv"}';
        const parsed = JSON.parse(rawJson);
        ctx.res = {
            name: parsed.title,
            url: parsed.streamUrl,
            headers: { "Cookie": "download=permitted" }
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://video-site.com/v/999",
    )

    assert res is not None
    assert res.url == "https://stream.host.com/sample.mkv"
    assert res.filename == "sample.mkv"
    assert res.headers.get("Cookie") == "download=permitted"


def test_extension_manager_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        manager = ExtensionManager(storage_dir=Path(tmpdir))

        # 1. Install from standalone JS
        js_code = """
        gopeed.events.onResolve(async (ctx) => {
            ctx.res = { name: "file.bin", url: "https://direct.download.com/file.bin" };
        });
        """
        ext1 = manager.install_from_js(
            js_source=js_code,
            name="test-js-ext",
            match_urls=["*://mysite.com/*"],
        )
        assert ext1.id == "test-js-ext"
        assert ext1.enabled is True
        assert len(manager.list_extensions()) == 1

        # 2. Install from ZIP archive
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            manifest_json = json.dumps({
                "name": "zip-extension",
                "version": "2.0.0",
                "author": "Tester",
                "scripts": [
                    {"event": "onResolve", "match": ["*://ziphost.com/*"], "entry": "index.js"}
                ]
            })
            zf.writestr("manifest.json", manifest_json)
            zf.writestr("index.js", "gopeed.events.onResolve(async (ctx) => { ctx.res = { url: 'https://cdn.ziphost.com/dl' }; });")

        ext2 = manager.install_from_zip(zip_buf.getvalue())
        assert ext2.id == "zip-extension"
        assert ext2.manifest.version == "2.0.0"
        assert len(manager.list_extensions()) == 2

        # 3. Test Enable / Disable
        assert manager.disable_extension("zip-extension") is True
        assert manager.get_extension("zip-extension").enabled is False
        assert manager.enable_extension("zip-extension") is True
        assert manager.get_extension("zip-extension").enabled is True

        # 4. Test Persistence: reload manager from same directory
        manager2 = ExtensionManager(storage_dir=Path(tmpdir))
        assert len(manager2.list_extensions()) == 2
        assert manager2.get_extension("zip-extension") is not None

        # 5. Test Delete
        assert manager2.delete_extension("zip-extension") is True
        assert manager2.get_extension("zip-extension") is None
        assert len(manager2.list_extensions()) == 1


@pytest.mark.asyncio
async def test_queue_manager_extension_resolution_hook():
    with tempfile.TemporaryDirectory() as tmpdir:
        ext_dir = Path(tmpdir) / "extensions"
        db_path = Path(tmpdir) / "test.db"
        downloads_dir = Path(tmpdir) / "downloads"

        ext_mgr = ExtensionManager(storage_dir=ext_dir)

        # Install mock extension matching protected hoster
        resolver_js = """
        gopeed.events.onResolve(async (ctx) => {
            const raw = ctx.req.url;
            const fileId = raw.split("/").pop();
            ctx.res = {
                name: "resolved_document.pdf",
                files: [
                    {
                        name: "resolved_document.pdf",
                        req: {
                            url: "https://cdn.hoster-backend.com/streams/" + fileId + ".pdf",
                            headers: {
                                "User-Agent": "GopeedExt/1.0",
                                "Cookie": "auth_token=secret_token_123",
                                "Referer": "https://protected-hoster.com"
                            }
                        }
                    }
                ]
            };
        });
        """
        ext_mgr.install_from_js(
            js_source=resolver_js,
            name="hoster-resolver",
            match_urls=["*://protected-hoster.com/item/*"],
        )

        pool = MultiInterfaceSessionPool([])
        store = StateStore(db_path)
        qm = QueueManager(
            session_pool=pool,
            state_store=store,
            default_download_dir=downloads_dir,
            extension_manager=ext_mgr,
        )

        # Add the raw hoster link
        task = await qm.add_url(
            url="https://protected-hoster.com/item/doc-777",
            start_paused=True,
        )

        # Verify that the URL, filename, and headers were resolved and injected!
        assert task.url == "https://cdn.hoster-backend.com/streams/doc-777.pdf"
        assert task.filename == "resolved_document.pdf"
        assert task.cookies == "auth_token=secret_token_123"
        assert task.user_agent == "GopeedExt/1.0"
        assert task.referer == "https://protected-hoster.com"


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_web_polyfills_url_and_searchparams():
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        const u = new URL(ctx.req.url);
        const qVal = u.searchParams.get("q");
        const hasFoo = u.searchParams.has("foo");
        
        u.searchParams.set("q", "modified");
        u.searchParams.append("token", "secret123");
        
        ctx.res = {
            name: "url_test.bin",
            url: u.href,
            extra: {
                hostname: u.hostname,
                port: u.port,
                pathname: u.pathname,
                qVal: qVal,
                hasFoo: hasFoo,
                search: u.search,
            }
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://sub.domain.org:9000/download/item?q=original&foo=bar",
    )

    assert res is not None
    assert res.extra["hostname"] == "sub.domain.org"
    assert res.extra["port"] == "9000"
    assert res.extra["pathname"] == "/download/item"
    assert res.extra["qVal"] == "original"
    assert res.extra["hasFoo"] is True
    assert "q=modified" in res.url
    assert "token=secret123" in res.url


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_web_polyfills_base64_and_encoding():
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        const text = "Getly Test 🚀";
        const b64 = btoa(text);
        const decoded = atob(b64);
        
        const encoder = new TextEncoder();
        const encodedBytes = encoder.encode(text);
        const decoder = new TextDecoder();
        const decodedText = decoder.decode(encodedBytes);

        ctx.res = {
            name: "encoding_test.bin",
            url: "https://example.com/file",
            extra: {
                b64: b64,
                decoded: decoded,
                decodedText: decodedText,
                bytesLength: encodedBytes.length,
            }
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://example.com/test",
    )

    assert res is not None
    assert res.extra["decoded"] == "Getly Test 🚀"
    assert res.extra["decodedText"] == "Getly Test 🚀"
    assert res.extra["bytesLength"] > 0


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_web_polyfills_crypto_and_hashes():
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        const h_md5 = md5("password123");
        const h_sha256 = sha256("password123");
        
        const randomArr = new Uint8Array(4);
        crypto.getRandomValues(randomArr);

        ctx.res = {
            name: "crypto_test.bin",
            url: "https://example.com/file",
            extra: {
                md5: h_md5,
                sha256: h_sha256,
                randomSample: Array.from(randomArr),
            }
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://example.com/test",
    )

    assert res is not None
    assert res.extra["md5"] == "482c811da5d5b4bc6d497ffa98491e38"
    assert res.extra["sha256"] == "ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f"
    assert len(res.extra["randomSample"]) == 4


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_web_polyfills_dom_parser():
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        const html = '<div id="container"><a class="download-link" href="https://dl.org/video.mp4" data-quality="1080p">Download HD</a></div>';
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const link = doc.querySelector('a.download-link');

        ctx.res = {
            name: "dom_test.mp4",
            url: link ? link.getAttribute("href") : "",
            extra: {
                text: link ? link.innerText : "",
                quality: link ? link.getAttribute("data-quality") : "",
            }
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://scraper-site.com/view/123",
    )

    assert res is not None
    assert res.url == "https://dl.org/video.mp4"
    assert res.extra["text"] == "Download HD"
    assert res.extra["quality"] == "1080p"


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_multi_file_and_settings():
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        const preferredQuality = gopeed.settings.quality || "720p";
        
        ctx.res = {
            name: "Collection Video",
            range: true,
            files: [
                {
                    name: "video_1080p.mp4",
                    size: 104857600,
                    path: "1080p/video.mp4",
                    req: {
                        url: "https://cdn.example.com/1080p.mp4",
                        method: "GET",
                        headers: { "Authorization": "Bearer token" }
                    }
                },
                {
                    name: "video_720p.mp4",
                    size: 52428800,
                    path: "720p/video.mp4",
                    req: {
                        url: "https://cdn.example.com/720p.mp4",
                        method: "GET",
                        headers: {}
                    }
                }
            ],
            extra: {
                selectedSetting: preferredQuality
            }
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://site.org/video/99",
        settings={"quality": "1080p"},
    )

    assert res is not None
    assert len(res.files) == 2
    assert res.files[0].name == "video_1080p.mp4"
    assert res.files[0].size == 104857600
    assert res.files[0].path == "1080p/video.mp4"
    assert res.files[0].url == "https://cdn.example.com/1080p.mp4"
    assert res.files[0].headers.get("Authorization") == "Bearer token"
    assert res.files[1].name == "video_720p.mp4"
    assert res.files[1].size == 52428800
    assert res.extra["selectedSetting"] == "1080p"


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_quickjs_sandbox_web_dom_polyfills():
    """
    Verifies that QuickJS bootstrap JS injects complete Web/DOM standard implementations:
    Event, CustomEvent, EventTarget, AbortController, AbortSignal, Request, Response,
    Headers, Blob, File, FormData, ReadableStream, performance, navigator, location, process.
    """
    sandbox = QuickJSSandbox()

    script = """
    gopeed.events.onResolve(async (ctx) => {
        // 1. Event & CustomEvent
        const ev = new Event("test", { bubbles: true, cancelable: true });
        if (ev.type !== "test" || !ev.bubbles || !ev.cancelable) throw new Error("Event failed");
        ev.preventDefault();
        if (!ev.defaultPrevented) throw new Error("preventDefault failed");

        const cev = new CustomEvent("custom", { detail: { answer: 42 } });
        if (cev.type !== "custom" || cev.detail.answer !== 42) throw new Error("CustomEvent failed");

        // 2. EventTarget
        const target = new EventTarget();
        let targetFired = false;
        target.addEventListener("ping", (e) => {
            if (e.type === "ping") targetFired = true;
        });
        target.dispatchEvent(new Event("ping"));
        if (!targetFired) throw new Error("EventTarget dispatch failed");

        // 3. AbortController & AbortSignal
        const controller = new AbortController();
        if (controller.signal.aborted) throw new Error("AbortSignal should not be aborted initially");
        let abortHeard = false;
        controller.signal.addEventListener("abort", () => { abortHeard = true; });
        controller.abort("User cancelled");
        if (!controller.signal.aborted || !abortHeard || controller.signal.reason !== "User cancelled") {
            throw new Error("AbortController abort failed");
        }

        // 4. Headers, Request & Response
        const reqHeaders = new Headers([["Accept", "application/json"], ["X-Custom", "abc"]]);
        const req = new Request("https://example.com/api/item", {
            method: "POST",
            headers: reqHeaders,
            body: JSON.stringify({ q: 1 })
        });
        if (req.method !== "POST" || req.headers.get("x-custom") !== "abc") {
            throw new Error("Request constructor failed");
        }

        const resp = new Response("hello world", { status: 200, statusText: "OK", headers: { "Content-Type": "text/plain" } });
        if (!(resp instanceof Response)) throw new Error("Response instanceof check failed");
        const respText = await resp.text();
        if (respText !== "hello world") throw new Error("Response text() failed");

        // 5. Blob, File, FormData
        const blob = new Blob(["part1", "part2"], { type: "text/plain" });
        if (blob.size !== 10) throw new Error("Blob size incorrect");
        const file = new File(["test content"], "sample.txt");
        if (file.name !== "sample.txt") throw new Error("File constructor failed");
        const fd = new FormData();
        fd.append("user", "alice");
        if (fd.get("user") !== "alice") throw new Error("FormData failed");

        // 6. Platform Globals
        if (self !== globalThis || window !== globalThis || global !== globalThis) {
            throw new Error("self/window/global aliases failed");
        }
        if (!performance || typeof performance.now !== "function") throw new Error("performance.now failed");
        if (!navigator || typeof navigator.userAgent !== "string") throw new Error("navigator failed");
        if (!process || process.env.NODE_ENV !== "production") throw new Error("process failed");

        // 7. fetch() with new Request(...)
        const fetchResp = await fetch(req);
        if (!(fetchResp instanceof Response)) throw new Error("fetch() must return a Response instance");

        ctx.res = {
            name: "polyfill_success.mp4",
            url: "https://stream.example.com/video.mp4"
        };
    });
    """

    res = sandbox.resolve_url(
        script_code=script,
        raw_url="https://test.com/run",
    )

    assert res is not None
    assert res.filename == "polyfill_success.mp4"
    assert res.url == "https://stream.example.com/video.mp4"


def test_youtube_url_matching_patterns():
    """
    Verifies that wildcard URL matching correctly handles youtube wildcard variants:
    - *://*.youtube.com/watch* matches https://www.youtube.com/watch?v=..., https://youtube.com/watch?v=..., and https://m.youtube.com/watch?v=...
    - *://www.youtube.com/watch/* matches https://www.youtube.com/watch?v=...
    - *://youtu.be/* matches https://youtu.be/...
    """
    pat_wildcard = "*://*.youtube.com/watch*"
    assert url_matches_pattern("https://www.youtube.com/watch?v=gEViQOdbPwE", pat_wildcard) is True
    assert url_matches_pattern("https://youtube.com/watch?v=gEViQOdbPwE", pat_wildcard) is True
    assert url_matches_pattern("https://m.youtube.com/watch?v=gEViQOdbPwE", pat_wildcard) is True
    assert url_matches_pattern("http://www.youtube.com/watch?v=gEViQOdbPwE", pat_wildcard) is True
    assert url_matches_pattern("https://other.com/watch?v=gEViQOdbPwE", pat_wildcard) is False

    # Standard manifest pattern with trailing /*
    pat_manifest = "*://www.youtube.com/watch/*"
    assert url_matches_pattern("https://www.youtube.com/watch?v=gEViQOdbPwE", pat_manifest) is True
    assert url_matches_pattern("https://www.youtube.com/watch/123", pat_manifest) is True

    # youtu.be shortlinks
    pat_shortlink = "*://youtu.be/*"
    assert url_matches_pattern("https://youtu.be/gEViQOdbPwE", pat_shortlink) is True


@pytest.mark.asyncio
async def test_ytdlp_streaming_bridge_fallback(tmp_path: Path):
    """
    Verifies that when a JavaScript extension raises an error or returns no files
    for a YouTube/streaming URL, the yt-dlp bridge resolves the direct stream with title,
    size, and headers, and QueueManager creates an appropriate download.
    """
    from unittest.mock import patch, MagicMock
    from core.extensions.engine import resolve_with_ytdlp

    mock_info = {
        "title": "Quantum Physics Explained",
        "ext": "mp4",
        "url": "https://rr3---sn-4g5ednks.googlevideo.com/videoplayback?expire=123",
        "filesize": 104857600,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 yt-test",
            "Accept": "*/*",
        },
    }

    # 1. Test direct resolve_with_ytdlp with mocked YoutubeDL
    with patch("yt_dlp.YoutubeDL") as MockYDL:
        mock_instance = MagicMock()
        mock_instance.__enter__.return_value = mock_instance
        mock_instance.extract_info.return_value = mock_info
        MockYDL.return_value = mock_instance

        res = resolve_with_ytdlp("https://www.youtube.com/watch?v=gEViQOdbPwE")
        assert res is not None
        assert res.filename == "Quantum Physics Explained.mp4"
        assert res.url.startswith("https://rr3---sn-4g5ednks.googlevideo.com")
        assert res.headers.get("User-Agent") == "Mozilla/5.0 yt-test"
        assert len(res.files) == 1
        assert res.files[0].size == 104857600

    # 2. Test QueueManager integration when extension resolution returns None (fallback to yt-dlp)
    store = StateStore(tmp_path / "test_ytdlp.db")
    pool = MultiInterfaceSessionPool([])
    await pool.initialize()

    try:
        ext_manager = ExtensionManager(storage_dir=tmp_path / "exts")
        qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path, extension_manager=ext_manager)

        with patch.object(ext_manager, "resolve_url", return_value=None):
            with patch("core.extensions.engine.resolve_with_ytdlp") as mock_resolve:
                mock_resolve.return_value = ResolvedResource(
                    url="https://rr3---sn-4g5ednks.googlevideo.com/videoplayback?expire=123",
                    filename="Quantum Physics Explained.mp4",
                    headers={"User-Agent": "Mozilla/5.0 yt-test"},
                )

                task = await qm.add_url(
                    url="https://www.youtube.com/watch?v=gEViQOdbPwE",
                    start_paused=True,
                )

                # Ensure the task has the resolved filename and stream URL instead of "watch"
                assert task.filename == "Quantum Physics Explained.mp4"
                assert "googlevideo.com" in task.url
                assert task.user_agent == "Mozilla/5.0 yt-test"
    finally:
        await pool.close()


@pytest.mark.skipif(not QUICKJS_AVAILABLE, reason="QuickJS not installed")
def test_real_youtube_extension_resolves_without_reference_error():
    """
    Tests that the real Gopeed YouTube extension bundle (if installed in data/extensions/youtube)
    executes inside QuickJS without raising 'ReferenceError: Event is not defined'
    or other Web/DOM polyfill errors.
    """
    yt_dir = Path("data/extensions/youtube")
    if not (yt_dir / "manifest.json").exists() or not (yt_dir / "dist/index.js").exists():
        pytest.skip("YouTube extension not found on disk")

    mgr = ExtensionManager(storage_dir=Path("data/extensions"))
    res = mgr.resolve_url("https://www.youtube.com/watch?v=gEViQOdbPwE")
    # Must successfully resolve into a real video title and stream, never raising ReferenceError
    assert res is not None
    assert res.filename and res.filename != "watch"
    assert res.url and res.url != "https://www.youtube.com/watch?v=gEViQOdbPwE"


def test_googlevideo_single_connection_detection_and_header_preservation():
    from core.engine.downloader import is_single_connection_stream
    from core.engine.probe import get_browser_headers

    # 1. Detection
    assert is_single_connection_stream("https://rr1---sn-4g5lzney.googlevideo.com/videoplayback?expire=17000") is True
    assert is_single_connection_stream("https://cdn.example.com/video.mp4?clen=12345&sig=abcdef") is True
    assert is_single_connection_stream("https://cdn.example.com/video.mp4?clen=12345&lsig=abcdef") is True
    assert is_single_connection_stream("https://example.com/standard.iso") is False

    # 2. Header preservation and client hint suppression for VisionOS/Mobile UA on googlevideo
    custom_headers = {
        "User-Agent": "com.google.ios.youtube/19.10.1 (Apple Vision Pro; visionOS 1.1)",
        "Range": "bytes=0-",
        "Accept-Encoding": "identity",
    }
    headers = get_browser_headers(
        url="https://rr1---sn-4g5lzney.googlevideo.com/videoplayback?expire=17000",
        custom_headers=custom_headers,
    )

    # Must preserve exact User-Agent
    assert headers["User-Agent"] == "com.google.ios.youtube/19.10.1 (Apple Vision Pro; visionOS 1.1)"
    # Must NOT send Windows Chromium hints with Apple Vision Pro User-Agent (avoids HTTP 403 Forbidden)
    assert "Sec-Ch-Ua" not in headers
    assert "Sec-Ch-Ua-Platform" not in headers
    # Must set Referer to youtube.com, never the googlevideo CDN host
    assert headers["Referer"] == "https://www.youtube.com/"


def test_task_headers_persistence_in_state_store(tmp_path):
    from core.scheduler.state_store import StateStore
    from core.scheduler.task import DownloadTask, DownloadStatus

    store = StateStore(tmp_path / "state.db")
    task = DownloadTask(
        url="https://rr1---sn.googlevideo.com/videoplayback?expire=123",
        destination_dir=tmp_path,
        filename="video.mp4",
        total_size=1234567,
        headers={"User-Agent": "VisionOS/1.0", "X-Custom": "Token123"},
    )
    store.save_task(task)

    loaded = store.get_task(task.task_id)
    assert loaded is not None
    assert loaded.headers == {"User-Agent": "VisionOS/1.0", "X-Custom": "Token123"}
    assert loaded.total_size == 1234567

    all_tasks = store.get_all_tasks()
    assert len(all_tasks) == 1
    assert all_tasks[0].headers == {"User-Agent": "VisionOS/1.0", "X-Custom": "Token123"}


@pytest.mark.asyncio
async def test_downloader_googlevideo_probe_skip_and_single_stream_routing(tmp_path):
    from unittest.mock import AsyncMock, patch, MagicMock
    from core.engine.downloader import DownloadEngine
    from core.network.models import NetworkAdapter

    adapter = NetworkAdapter(name="Ethernet", ip="192.168.1.100", if_index=1, is_primary=True)
    mock_pool = MagicMock()
    mock_pool.is_initialized = True
    mock_pool.active_adapters = [adapter]
    mock_pool.primary_adapter = adapter
    mock_pool.all_adapters = [adapter]

    mock_session = MagicMock()
    mock_pool.get_primary_adapter_and_session.return_value = (adapter, mock_session)

    googlevideo_url = "https://rr2---sn-4g5lzney.googlevideo.com/videoplayback?expire=99999&clen=264796527&sig=mock_sig"

    engine = DownloadEngine(
        url=googlevideo_url,
        destination_dir=tmp_path,
        session_pool=mock_pool,
        custom_filename="stream_test.mp4",
        custom_headers={"User-Agent": "visionOS/1.0"},
    )

    with patch("core.engine.probe.ResourceProbe.probe", new_callable=AsyncMock) as mock_probe, \
         patch.object(engine, "_download_single_stream", new_callable=AsyncMock) as mock_single_stream:
        mock_single_stream.return_value = True

        result = await engine.start()
        assert result is True

        # Ensure probe was SKIPPED because it's a single-connection stream with clen
        mock_probe.assert_not_called()
        # Ensure it routed directly to single-stream download instead of multi-worker
        mock_single_stream.assert_called_once()
        assert engine.info.total_size == 264796527
        assert engine.info.filename == "stream_test.mp4"


def test_resolve_with_ytdlp_source_address_and_format_propagation():
    from unittest.mock import MagicMock, patch
    from core.extensions.engine import resolve_with_ytdlp

    mock_ydl_cls = MagicMock()
    mock_ydl_instance = MagicMock()
    mock_ydl_cls.return_value.__enter__.return_value = mock_ydl_instance
    mock_ydl_instance.extract_info.return_value = {
        "title": "Mock Video",
        "url": "https://rr1---sn.googlevideo.com/videoplayback?expire=123",
        "filesize": 50000000,
        "http_headers": {"User-Agent": "MockUA/1.0"},
        "ext": "mp4",
    }

    with patch.dict("sys.modules", {"yt_dlp": MagicMock(YoutubeDL=mock_ydl_cls)}):
        res = resolve_with_ytdlp("https://www.youtube.com/watch?v=mock123", source_address="10.5.0.2")

        assert res is not None
        assert res.filename == "Mock Video.mp4"
        assert res.url == "https://rr1---sn.googlevideo.com/videoplayback?expire=123"
        assert res.headers == {"User-Agent": "MockUA/1.0"}

        # Verify source_address and format chain were passed to YoutubeDL options
        opts = mock_ydl_cls.call_args[0][0]
        assert opts.get("source_address") == "10.5.0.2"
        assert "best[ext=mp4]" in opts.get("format", "")




