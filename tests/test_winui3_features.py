import csv
import io
from pathlib import Path
import pytest
import flet as ft

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadTask, DownloadStatus
from ui.coordinator import UICoordinator
from ui.components.datagrid_view import DownloadDataGrid, export_tasks_to_csv
from ui.components.accordion_filters import AccordionFilters
from ui.components.operations_drawer import OperationsDrawer
from ui.views.dashboard_view import DashboardView


@pytest.mark.asyncio
async def test_datagrid_selection_and_csv_export(tmp_path: Path):
    ad1 = NetworkAdapter(name="Wi-Fi", friendly_name="Intel Wi-Fi 6", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "winui3_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    t1 = DownloadTask(url="https://example.com/file1.zip", destination_dir=tmp_path, filename="file1.zip", total_size=1000, downloaded_bytes=500, status=DownloadStatus.DOWNLOADING)
    t2 = DownloadTask(url="https://example.com/file2.mp4", destination_dir=tmp_path, filename="file2.mp4", total_size=2000, downloaded_bytes=2000, status=DownloadStatus.COMPLETED)
    store.save_task(t1)
    store.save_task(t2)
    qm._tasks[t1.task_id] = t1
    qm._tasks[t2.task_id] = t2

    grid = DownloadDataGrid(coordinator=coord)
    grid.set_tasks([t1, t2])

    assert len(grid.rows_column.controls) == 2
    assert len(grid.selected_task_ids) == 0

    # Toggle selection
    grid.select_all()
    assert len(grid.selected_task_ids) == 2
    grid.clear_selection()
    assert len(grid.selected_task_ids) == 0

    # Test CSV export
    csv_file = tmp_path / "export.csv"
    res = export_tasks_to_csv([t1, t2], csv_file)
    assert res == csv_file
    assert csv_file.exists()

    with open(csv_file, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) == 3  # Header + 2 rows
        assert "file1.zip" in reader[1][1]
        assert "file2.mp4" in reader[2][1]

    await pool.close()


@pytest.mark.asyncio
async def test_accordion_filters_interaction(tmp_path: Path):
    changed_records = []

    def _on_filters(statuses, categories, sources):
        changed_records.append((statuses, categories, sources))

    accordion = AccordionFilters(on_filters_changed=_on_filters)
    assert accordion is not None

    # Test selection
    accordion._on_status_changed("DOWNLOADING", True)
    assert "DOWNLOADING" in accordion.selected_statuses
    assert len(changed_records) == 1
    assert "DOWNLOADING" in changed_records[0][0]

    accordion._clear_statuses(None)
    assert len(accordion.selected_statuses) == 0
    assert len(changed_records) == 2


@pytest.mark.asyncio
async def test_operations_drawer_live_tracking(tmp_path: Path):
    ad1 = NetworkAdapter(name="Wi-Fi", friendly_name="Intel Wi-Fi 6", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "ops_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    t1 = DownloadTask(url="https://example.com/linux.iso", destination_dir=tmp_path, filename="linux.iso", total_size=50000000, downloaded_bytes=10000000, status=DownloadStatus.DOWNLOADING)
    t2 = DownloadTask(url="https://example.com/doc.pdf", destination_dir=tmp_path, filename="doc.pdf", total_size=1000000, downloaded_bytes=1000000, status=DownloadStatus.COMPLETED)
    store.save_task(t1)
    store.save_task(t2)
    qm._tasks[t1.task_id] = t1
    qm._tasks[t2.task_id] = t2

    drawer = OperationsDrawer(coordinator=coord)
    drawer.update_tasks()

    assert "1 di 2 operazioni completate" in drawer.header_text.value
    assert len(drawer.items_list.controls) == 1  # 1 active task

    await pool.close()


@pytest.mark.asyncio
async def test_dashboard_view_search_and_filter_integration(tmp_path: Path):
    ad1 = NetworkAdapter(name="Wi-Fi", friendly_name="Intel Wi-Fi 6", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "search_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    t1 = DownloadTask(url="https://example.com/ubuntu-24.04.iso", destination_dir=tmp_path, filename="ubuntu-24.04.iso", total_size=2000, downloaded_bytes=500, status=DownloadStatus.DOWNLOADING)
    t2 = DownloadTask(url="https://example.com/python-setup.exe", destination_dir=tmp_path, filename="python-setup.exe", total_size=3000, downloaded_bytes=3000, status=DownloadStatus.COMPLETED)
    store.save_task(t1)
    store.save_task(t2)
    qm._tasks[t1.task_id] = t1
    qm._tasks[t2.task_id] = t2

    dash = DashboardView(coordinator=coord)
    assert len(dash.datagrid.rows_column.controls) == 2

    # Search for "ubuntu"
    dash.set_search_query("ubuntu")
    assert len(dash.datagrid.rows_column.controls) == 1

    # Clear search
    dash.set_search_query("")
    assert len(dash.datagrid.rows_column.controls) == 2

    await pool.close()


@pytest.mark.asyncio
async def test_winui3_navigation_rail_and_settings_dialog():
    from ui.components.sidebar import CollapsibleSidebar, SIDEBAR_WIDTH_COMPACT
    from ui.components.settings_dialog import SettingsDialog

    nav_events = []
    sidebar = CollapsibleSidebar(
        on_navigate=lambda k, s, c: nav_events.append((k, s, c)),
        on_toggle_grabber=lambda: None,
        on_toggle_ask_save=lambda: None,
        cnl_port=9666,
    )

    # 1. Compact rail width = 56, toggle to 200 extended
    assert sidebar.width == 56
    assert sidebar.width == SIDEBAR_WIDTH_COMPACT
    assert sidebar.is_expanded is False

    sidebar.toggle_expanded()
    assert sidebar.is_expanded is True
    assert sidebar.width == 200
    labels = [item.label for item in sidebar.macro_items]
    assert labels == ["Download", "Site Grabber", "Net-Merge", "Estensioni"]

    sidebar.toggle_expanded()
    assert sidebar.is_expanded is False
    assert sidebar.width == 56

    # 2. Macro items
    keys = [item.key for item in sidebar.macro_items]
    assert "NAV_DOWNLOADS" in keys
    assert "NAV_GRABBER" in keys
    assert "NAV_MONITOR" in keys
    assert "NAV_EXTENSIONS" in keys

    # 3. Dynamic counts & badge rendering
    sidebar.update_counts({"ALL": 5, "ACTIVE": 2}, {}, extensions_count=3)
    assert sidebar.status_counts["ALL"] == 5
    assert sidebar.extensions_count == 3

    # 4. Settings dialog
    grabber_toggled = []
    ask_toggled = []
    dlg = SettingsDialog(
        cnl_port=9666,
        grabber_enabled=True,
        ask_save_enabled=False,
        on_toggle_grabber=lambda: grabber_toggled.append(True),
        on_toggle_ask_save=lambda: ask_toggled.append(True),
    )
    assert dlg.grabber_enabled is True
    assert dlg.ask_save_enabled is False
    dlg.grabber_switch.value = False
    dlg._handle_grabber_toggle(None)
    assert len(grabber_toggled) == 1


@pytest.mark.asyncio
async def test_datagrid_column_dimensions_and_no_wrap(tmp_path: Path):
    ad1 = NetworkAdapter(name="Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "datagrid_dim_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    grid = DownloadDataGrid(coordinator=coord)

    # 1. Verify header row proportional weights
    header_controls = grid.header_row.content.controls
    assert header_controls[0].width == 34  # Checkbox
    assert header_controls[1].expand == 4  # Nome file
    assert header_controls[2].expand == 2  # Categoria
    assert header_controls[3].expand == 1  # Dimensione
    assert header_controls[4].expand == 2  # Velocità / Stato
    assert header_controls[5].expand == 2  # Progresso
    assert header_controls[6].expand == 1  # Sorgente
    assert header_controls[7].width == 75  # Azioni

    # 2. Verify row rendering with no_wrap and error tooltip
    task_err = DownloadTask(
        url="https://video.twimg.com/test.mp4?tag=14",
        destination_dir=tmp_path,
        filename="test.mp4",
        total_size=1000,
        status=DownloadStatus.FAILED,
        error_message="HTTP 403 Forbidden CDN rejection",
    )
    grid.set_tasks([task_err])

    row = grid.rows_column.controls[0]
    row_ctrl = row
    while hasattr(row_ctrl, "content") and not isinstance(getattr(row_ctrl, "content", None), ft.Row):
        row_ctrl = row_ctrl.content
    row_controls = row_ctrl.content.controls
    assert row_controls[0].width == 34
    # Filename container
    fn_container = row_controls[1]
    assert fn_container.expand == 4
    fn_row = fn_container.content
    fn_text = fn_row.controls[1]
    assert fn_text.no_wrap is True
    assert fn_text.max_lines == 1
    assert fn_text.overflow == ft.TextOverflow.ELLIPSIS
    assert fn_text.expand is True

    # Error tooltip on status control
    status_ctrl = row_controls[4].content
    assert "403 Forbidden" in status_ctrl.tooltip

    await pool.close()


def test_twitter_headers_and_filename_sanitization():
    from core.engine.probe import ResourceProbe, get_browser_headers

    # 1. Twitter CDN Referer fallback
    twitter_url = "https://video.twimg.com/ext_tw_video/12345/pu/vid/720x1280/sample.mp4?tag=14"
    headers = get_browser_headers(twitter_url)
    assert headers["Referer"] == "https://x.com/"

    # If referer was already video.twimg.com, replace with x.com
    headers2 = get_browser_headers(twitter_url, referer="https://video.twimg.com/something")
    assert headers2["Referer"] == "https://x.com/"

    # 2. Filename extraction stripping query parameters
    fn = ResourceProbe._extract_filename_from_url(twitter_url)
    assert fn == "sample.mp4"
    assert "?" not in fn

    fn2 = ResourceProbe._extract_filename_from_url("https://example.com/archive.tar.gz?bytestart=0&byteend=1000#section")
    assert fn2 == "archive.tar.gz"


@pytest.mark.asyncio
async def test_probe_range_403_fallback_to_plain_get():
    import aiohttp
    from aiohttp import web
    from core.engine.probe import ResourceProbe

    app = web.Application()

    async def handle_probe(request: web.Request) -> web.Response:
        if request.method == "HEAD":
            return web.Response(status=403)
        if "Range" in request.headers:
            # Simulating CDN rejecting Range requests with 403
            return web.Response(status=403)
        # Plain GET without Range succeeds with full Content-Length
        return web.Response(
            status=200,
            body=b"video_bytes_content_sample",
            headers={"Content-Length": "26", "Content-Type": "video/mp4"},
        )

    app.router.add_route("*", "/video_test.mp4", handle_probe)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 19888)
    await site.start()

    try:
        async with aiohttp.ClientSession() as session:
            info = await ResourceProbe.probe(session, "http://127.0.0.1:19888/video_test.mp4")
            assert info.total_size == 26
            assert info.supports_ranges is False
            assert info.filename == "video_test.mp4"
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_downloader_auto_fallback_to_single_stream(tmp_path: Path):
    import aiohttp
    from aiohttp import web
    from core.network.models import NetworkAdapter
    from core.network.session_pool import MultiInterfaceSessionPool
    from core.engine.downloader import DownloadEngine
    from core.engine.probe import RemoteResourceInfo
    from core.engine.host_coordinator import HostCoordinator

    app = web.Application()

    # Server that claims to support ranges, but resets connection or returns 500 on Range requests,
    # while plain single-stream GET succeeds
    async def handle_download(request: web.Request) -> web.Response:
        if "Range" in request.headers:
            return web.Response(status=403, text="Range processing rejected by CDN")
        return web.Response(status=200, body=b"Hello from single stream fallback!")

    app.router.add_route("*", "/stream_fallback.txt", handle_download)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 19889)
    await site.start()

    try:
        ad = NetworkAdapter(name="Loopback", ip="127.0.0.1", is_primary=True, is_up=True)
        pool = MultiInterfaceSessionPool(adapters=[ad])
        await pool.initialize()

        info = RemoteResourceInfo(
            url="http://127.0.0.1:19889/stream_fallback.txt",
            total_size=34,
            supports_ranges=True,  # Initially set to True
            filename="stream_fallback.txt",
        )

        engine = DownloadEngine(
            url="http://127.0.0.1:19889/stream_fallback.txt",
            session_pool=pool,
            destination_dir=tmp_path,
            workers_per_interface=1,
            host_coordinator=HostCoordinator(),
        )
        engine.info = info

        success = await engine.start()
        assert success is True

        downloaded_file = tmp_path / "stream_fallback.txt"
        assert downloaded_file.exists()
        assert downloaded_file.read_bytes() == b"Hello from single stream fallback!"

        await pool.close()
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_command_bar_responsive_adaptation(tmp_path: Path):
    ad1 = NetworkAdapter(name="Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "cmd_bar_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    dash = DashboardView(coordinator=coord)

    # 1. Available width >= 1180px: labels visible, auto button width
    # 1500 - 56 (rail) - 210 (filters) = 1234 >= 1180
    dash.on_page_resize(1500, rail_width=56)
    assert dash._command_bar_compact is False
    assert all(lb.visible is True for lb in dash._secondary_labels)
    assert dash._secondary_buttons[0].width is None

    # 2. Available width < 1180px: labels hidden, compact buttons width=38, height=34
    # 1300 - 56 (rail) - 210 (filters) = 1034 < 1180
    dash.on_page_resize(1300, rail_width=56)
    assert dash._command_bar_compact is True
    assert all(lb.visible is False for lb in dash._secondary_labels)
    assert dash._secondary_buttons[0].width == 38
    assert dash._secondary_buttons[0].height == 34

    # 3. Dynamic adaptation when toggling filters:
    # Closing filters gains 210px: 1300 - 56 = 1244 >= 1180 -> restores labels
    dash._handle_toggle_filters(None)
    assert dash._filters_visible is False
    assert dash._command_bar_compact is False
    assert all(lb.visible is True for lb in dash._secondary_labels)

    # 4. Dynamic adaptation when expanding sidebar:
    # Sidebar width becomes 200px: 1300 - 200 = 1100 < 1180 -> switches back to compact mode
    dash.on_page_resize(1300, rail_width=200)
    assert dash._command_bar_compact is True
    assert all(lb.visible is False for lb in dash._secondary_labels)

    await pool.close()


@pytest.mark.asyncio
async def test_filters_inline_handle_and_overlay_backdrop(tmp_path: Path):
    ad1 = NetworkAdapter(name="Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "overlay_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    dash = DashboardView(coordinator=coord)

    # 1. Wide window (>= 900px): inline mode with handle pill
    dash.on_page_resize(1000)
    assert dash._is_narrow_screen is False
    assert dash.inline_filter_container.visible is True
    assert dash.filter_handle_pill.width == 4
    assert dash.filter_handle_pill.height == 28
    assert dash.overlay_backdrop.visible is False

    # Toggle inline visibility
    dash._handle_toggle_filters(None)
    assert dash.inline_filter_container.visible is False
    dash._handle_toggle_filters(None)
    assert dash.inline_filter_container.visible is True

    # 2. Narrow window (< 900px): inline hidden, enters overlay capability
    dash.on_page_resize(850)
    assert dash._is_narrow_screen is True
    assert dash.inline_filter_container.visible is False
    assert dash.overlay_backdrop.visible is False
    assert dash._overlay_filters_open is False

    # Open overlay
    dash._handle_toggle_filters(None)
    assert dash._overlay_filters_open is True
    assert dash.overlay_backdrop.visible is True
    assert dash.overlay_filters_panel.visible is True
    assert dash.overlay_filters_panel.content == dash.accordion_filters

    # Click backdrop to dismiss
    dash._close_overlay_filters(None)
    assert dash._overlay_filters_open is False
    assert dash.overlay_backdrop.visible is False
    assert dash.overlay_filters_panel.visible is False
    assert dash.inline_filter_slot.content == dash.accordion_filters

    await pool.close()


@pytest.mark.asyncio
async def test_hamburger_sidebar_toggle_and_responsive_breakpoint(tmp_path: Path):
    from ui.app import ResilientApp

    ad1 = NetworkAdapter(name="Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "hamburger_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    from ui.components.sidebar import CollapsibleSidebar

    app = ResilientApp(coordinator=coord, cnl_port=9666)
    app.sidebar = CollapsibleSidebar(
        on_navigate=lambda k, s, c: None,
        on_toggle_grabber=lambda: None,
        on_toggle_ask_save=lambda: None,
        cnl_port=9666,
    )
    app.dashboard_view = DashboardView(coordinator=coord)
    app._build_header()

    assert app.hamburger_btn is not None
    assert app.hamburger_btn.icon == ft.Icons.MENU_ROUNDED

    # 1. Desktop mode (width >= 720px): Rail is always visible, clicking hamburger toggles 56px <-> 200px
    assert app.sidebar.visible is True
    assert app.sidebar.is_expanded is False
    assert app.sidebar.width == 56

    app._toggle_sidebar_visibility(None)
    assert app.sidebar.visible is True
    assert app.sidebar.is_expanded is True
    assert app.sidebar.width == 200

    app._toggle_sidebar_visibility(None)
    assert app.sidebar.visible is True
    assert app.sidebar.is_expanded is False
    assert app.sidebar.width == 56

    # 2. Responsive auto-hide below 720px
    class FakeResizeEvent:
        def __init__(self, w):
            self.width = w

    app._on_window_resize(FakeResizeEvent(700))
    assert app.sidebar.visible is False

    app._on_window_resize(FakeResizeEvent(950))
    assert app.sidebar.visible is True

    # 3. Narrow window (< 720px): clicking hamburger toggles visibility on/off
    class FakePage:
        width = 650

    app.page = FakePage()
    app._on_window_resize(FakeResizeEvent(650))
    assert app.sidebar.visible is False

    app._toggle_sidebar_visibility(None)
    assert app.sidebar.visible is True

    app._toggle_sidebar_visibility(None)
    assert app.sidebar.visible is False

    await pool.close()


def test_accordion_filters_tutti_and_scroll_hidden():
    from ui.components.accordion_filters import AccordionFilters, ALL_STATUS_KEYS, ALL_CATEGORIES

    accordion = AccordionFilters()

    # 1. All checkboxes checked by default (Tutti state)
    assert all(cb.value is True for cb in accordion.status_checkboxes.values())
    assert all(cb.value is True for cb in accordion.cat_checkboxes.values())
    assert len(accordion.selected_statuses) == len(ALL_STATUS_KEYS)
    assert len(accordion.selected_categories) == len(ALL_CATEGORIES)

    # 2. ScrollMode is HIDDEN
    assert accordion.content.scroll == ft.ScrollMode.HIDDEN

    # 3. Test Nessuno deselects all
    accordion._clear_statuses(None)
    assert all(cb.value is False for cb in accordion.status_checkboxes.values())
    assert len(accordion.selected_statuses) == 0

    # 4. Test Tutti selects all
    accordion._select_all_statuses(None)
    assert all(cb.value is True for cb in accordion.status_checkboxes.values())
    assert len(accordion.selected_statuses) == len(ALL_STATUS_KEYS)


@pytest.mark.asyncio
async def test_torrent_metadata_timeout_sec_keyword_arg():
    from core.engine.torrent_engine import TorrentSessionManager, TorrentMetadata

    mgr = TorrentSessionManager.get_instance()
    # Test that calling with timeout_sec keyword argument does not raise TypeError
    meta = await mgr.resolve_metadata(
        "magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=test",
        timeout=1.0,
        timeout_sec=0.5,
    )
    assert isinstance(meta, TorrentMetadata)


@pytest.mark.asyncio
async def test_torrent_dialog_initialization_and_metadata_guard(tmp_path: Path):
    from ui.components.torrent_dialog import TorrentDialog

    ad1 = NetworkAdapter(name="Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()
    store = StateStore(tmp_path / "dlg_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    dlg = TorrentDialog(
        coordinator=coord,
        source="magnet:?xt=urn:btih:da39a3ee5e6b4b0d3255bfef95601890afd80709&dn=sample",
        initial_dir=tmp_path,
    )
    assert dlg.meta_summary_text is not None

    # Resolve metadata directly without mounting to page; shouldn't crash
    await dlg._resolve_metadata_async()
    assert dlg._is_resolving is False

    await pool.close()



