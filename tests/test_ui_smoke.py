import asyncio
from pathlib import Path
import pytest
import flet as ft

from core.network.models import NetworkAdapter
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus
from ui.coordinator import UICoordinator
from ui.views.dashboard_view import DashboardView
from ui.views.monitor_view import MonitorView
from ui.components.download_card import DownloadCard
from ui.components.interface_card import InterfaceCard
from ui.components.add_download_dialog import AddDownloadDialog
from ui.components.clipboard_dialog import ClipboardDownloadDialog
from ui.app import ResilientApp


@pytest.mark.asyncio
async def test_ui_components_smoke(tmp_path: Path):
    """
    Comprehensive smoke test to verify that all UI views and components can be instantiated
    and build their control trees across all states without raising AttributeError or TypeError in Flet 1.0.5.
    """
    # 1. Setup mock environment
    ad1 = NetworkAdapter(name="Wi-Fi", friendly_name="Intel Wi-Fi 6", ip="192.168.1.89", is_primary=True, is_up=True)
    ad2 = NetworkAdapter(name="Eth0", friendly_name="Realtek PCIe GbE", ip="192.168.1.105", is_primary=False, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1, ad2])
    await pool.initialize()

    store = StateStore(tmp_path / "smoke_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    # 2. Test DownloadCard across all statuses
    statuses = [
        DownloadStatus.DOWNLOADING,
        DownloadStatus.COMPLETED,
        DownloadStatus.PAUSED,
        DownloadStatus.FAILED,
        DownloadStatus.CANCELLED,
        DownloadStatus.PENDING,
    ]
    for st in statuses:
        task = DownloadTask(
            url=f"https://example.com/file_{st.value.lower()}.iso",
            destination_dir=tmp_path,
            filename=f"file_{st.value.lower()}.iso",
            total_size=104857600,
            downloaded_bytes=52428800 if st != DownloadStatus.COMPLETED else 104857600,
            status=st,
        )
        store.save_task(task)
        qm._tasks[task.task_id] = task

        card = DownloadCard(task=task, coordinator=coord)
        assert card is not None
        assert isinstance(card.content, ft.Column)
        assert card.margin is not None
        assert card.border_radius == 20

    # 3. Test InterfaceCard instantiation (Primary Wi-Fi & Secondary Ethernet)
    iface_wifi = InterfaceCard(adapter=ad1, coordinator=coord)
    assert iface_wifi is not None
    assert isinstance(iface_wifi.content, ft.Column)
    assert iface_wifi.margin is not None
    assert iface_wifi.border_radius == 20

    iface_eth = InterfaceCard(adapter=ad2, coordinator=coord)
    assert iface_eth is not None

    # 4. Test DashboardView instantiation with active tasks
    dash_view = DashboardView(coordinator=coord)
    assert dash_view is not None
    assert isinstance(dash_view.content, ft.Column)
    assert len(dash_view.card_list_view.controls) == len(statuses)

    # Test filter button switching
    dash_view._set_filter("ACTIVE")
    assert dash_view.current_filter == "ACTIVE"
    dash_view._set_filter("COMPLETED")
    assert dash_view.current_filter == "COMPLETED"
    dash_view._set_filter("PAUSED")
    assert dash_view.current_filter == "PAUSED"
    dash_view._set_filter("FAILED")
    assert dash_view.current_filter == "FAILED"
    dash_view._set_filter("ALL")
    assert dash_view.current_filter == "ALL"

    # 5. Test DashboardView with empty state (0 tasks)
    empty_store = StateStore(tmp_path / "empty.db")
    empty_qm = QueueManager(session_pool=pool, state_store=empty_store, default_download_dir=tmp_path)
    empty_coord = UICoordinator(queue_manager=empty_qm, session_pool=pool)
    empty_dash_view = DashboardView(coordinator=empty_coord)
    assert len(empty_dash_view.card_list_view.controls) == 1  # Empty state container

    # 6. Test MonitorView instantiation
    mon_view = MonitorView(coordinator=coord)
    assert mon_view is not None
    assert isinstance(mon_view.content, ft.Column)
    assert len(mon_view.cards_column.controls) == 2

    # 7. Test AddDownloadDialog instantiation
    add_dlg = AddDownloadDialog(coordinator=coord)
    assert add_dlg is not None
    assert add_dlg.url_field.value == ""
    assert add_dlg.actions is not None

    # 8. Test ClipboardDownloadDialog instantiation
    clip_dlg = ClipboardDownloadDialog(url="https://speedtest.server/file.iso", coordinator=coord)
    assert clip_dlg is not None
    assert "file.iso" in clip_dlg.url

    # 9. Test ResilientApp initialization and navigation header
    app = ResilientApp(coordinator=coord, cnl_port=9666)
    assert app is not None
    assert app.current_tab_index == 0
    header = app._build_header()
    assert header is not None

    # Test tab navigation internal logic
    app.dashboard_view = dash_view
    app.monitor_view = mon_view
    app.body_container = ft.Container(content=dash_view)
    app._switch_tab(1)
    assert app.current_tab_index == 1
    assert app.body_container.content == mon_view
    app._switch_tab(0)
    assert app.current_tab_index == 0
    assert app.body_container.content == dash_view

    await pool.close()


@pytest.mark.asyncio
async def test_system_tray_threadsafe_lifecycle():
    """
    Verifies that SystemTrayManager schedules window.destroy() and on_exit()
    thread-safely on the main asyncio loop without raising un-awaited coroutine warnings.
    """
    import threading
    from unittest.mock import MagicMock
    from ui.tray import SystemTrayManager

    loop = asyncio.get_running_loop()
    destroyed = False

    class MockWindow:
        def __init__(self):
            self.visible = True
            self.prevent_close = False
            self.on_event = None

        async def destroy(self):
            nonlocal destroyed
            destroyed = True

        async def to_front(self):
            pass

    class MockPage:
        def __init__(self):
            self.window = MockWindow()
            self.loop = loop

        def update(self):
            pass

        def run_task(self, coro):
            pass

    on_exit_called = False

    async def mock_on_exit():
        nonlocal on_exit_called
        on_exit_called = True

    mock_coord = MagicMock()
    tray = SystemTrayManager(coordinator=mock_coord, on_exit=mock_on_exit, loop=loop)
    page = MockPage()
    tray.page = page

    # Call _menu_quit from a background thread (simulating Pystray's native OS loop)
    thread = threading.Thread(target=lambda: tray._menu_quit(None, None))
    thread.start()
    thread.join()

    # Yield control to let the loop execute the scheduled threadsafe coroutine
    await asyncio.sleep(0.05)

    assert on_exit_called is True
    assert destroyed is True


@pytest.mark.asyncio
async def test_resilient_app_main_initialization(tmp_path: Path):
    """Verifies that ResilientApp.main(page) initializes all views and window icons without NameError or crash."""
    loop = asyncio.get_running_loop()
    pool = MultiInterfaceSessionPool(adapters=[NetworkAdapter(name="loop", ip="127.0.0.1")])
    await pool.initialize()
    store = StateStore(tmp_path / "app_main_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    class MockWindow:
        def __init__(self):
            self.title_bar_hidden = False
            self.title_bar_buttons_hidden = False
            self.icon = None
            self.width = 0
            self.height = 0
            self.min_width = 0
            self.min_height = 0
            self.prevent_close = False
            self.on_event = None

    class MockPage:
        def __init__(self):
            self.title = ""
            self.theme_mode = None
            self.bgcolor = None
            self.padding = 0
            self.window = MockWindow()
            self.loop = loop
            self.controls = []

        def add(self, *controls):
            self.controls.extend(controls)

        def update(self):
            pass

        def show_dialog(self, dlg):
            pass

    page = MockPage()
    app = ResilientApp(coordinator=coord, cnl_port=9666, loop=loop)
    await app.main(page)

    assert page.title == "Getly"
    assert page.window.icon is not None
    assert page.window.title_bar_hidden is True
    assert len(page.controls) > 0
    assert app.dashboard_view is not None
    assert app.monitor_view is not None

    await app.shutdown()

    # Also verify start_minimized=True sets page.window.visible = False
    page_min = MockPage()
    app_min = ResilientApp(coordinator=coord, cnl_port=9666, loop=loop, start_minimized=True)
    await app_min.main(page_min)
    assert page_min.window.visible is False
    await app_min.shutdown()

    await pool.close()


@pytest.mark.asyncio
async def test_collapsible_sidebar_navigation_and_filters(tmp_path: Path):
    """
    Verifies that CollapsibleSidebar collapses/expands with ease-out animation,
    triggers navigation callbacks for status/category/tools, and syncs filters with DashboardView.
    """
    from ui.components.sidebar import CollapsibleSidebar, SIDEBAR_WIDTH_COMPACT, SIDEBAR_WIDTH_EXTENDED

    ad1 = NetworkAdapter(name="Wi-Fi", friendly_name="Intel Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    await pool.initialize()

    store = StateStore(tmp_path / "sidebar_test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    nav_events = []
    def on_nav(key, status, category):
        nav_events.append((key, status, category))

    grabber_toggled = []
    ask_save_toggled = []
    autostart_toggled = []

    sidebar = CollapsibleSidebar(
        on_navigate=on_nav,
        on_toggle_grabber=lambda: grabber_toggled.append(True),
        on_toggle_ask_save=lambda: ask_save_toggled.append(True),
        on_toggle_autostart=lambda: autostart_toggled.append(True),
        cnl_port=9666,
        grabber_enabled=True,
        ask_save_enabled=True,
        autostart_enabled=False,
        initially_expanded=True,
    )

    # 1. Verify extended mode
    assert sidebar.is_expanded is True
    assert sidebar.width == SIDEBAR_WIDTH_EXTENDED

    # 2. Toggle to compact mode
    class FakeEvent:
        pass
    sidebar._toggle_expanded(FakeEvent())
    assert sidebar.is_expanded is False
    assert sidebar.width == SIDEBAR_WIDTH_COMPACT

    # 3. Toggle back to extended mode
    sidebar._toggle_expanded(FakeEvent())
    assert sidebar.is_expanded is True
    assert sidebar.width == SIDEBAR_WIDTH_EXTENDED

    # 4. Test item selection
    sidebar._handle_item_click("STATUS_ACTIVE")
    assert sidebar.active_key == "STATUS_ACTIVE"
    assert len(nav_events) == 1
    assert nav_events[-1] == ("STATUS_ACTIVE", "ACTIVE", None)

    sidebar._handle_item_click("CAT_Compressi")
    assert sidebar.active_key == "CAT_Compressi"
    assert nav_events[-1] == ("CAT_Compressi", None, "Compressi")

    sidebar._handle_item_click("TOOL_MONITOR")
    assert sidebar.active_key == "TOOL_MONITOR"
    assert nav_events[-1] == ("TOOL_MONITOR", None, None)

    # 5. Test count updates and footer state
    sidebar.update_counts({"ALL": 12, "ACTIVE": 3}, {"Compressi": 5})
    assert sidebar.status_counts["ALL"] == 12
    assert sidebar.status_counts["ACTIVE"] == 3
    assert sidebar.category_counts["Compressi"] == 5

    sidebar.update_footer_states(grabber_enabled=False, ask_save_enabled=False, autostart_enabled=True)
    assert sidebar.grabber_enabled is False
    assert sidebar.ask_save_enabled is False
    assert sidebar.autostart_enabled is True

    # 6. Test App integration with sidebar navigation
    app = ResilientApp(coordinator=coord, cnl_port=9666)
    app.dashboard_view = DashboardView(coordinator=coord)
    app.monitor_view = MonitorView(coordinator=coord)
    app.body_container = ft.Container(content=app.dashboard_view)
    app.sidebar = sidebar
    app._build_header()

    # Navigate to category Video
    app._handle_sidebar_navigate("CAT_Video", None, "Video")
    assert "Video" in app.header_title_text.value
    assert app.dashboard_view.current_category_filter == "Video"
    assert app.body_container.content == app.dashboard_view

    # Navigate to Net-Merge Monitor
    app._handle_sidebar_navigate("TOOL_MONITOR", None, None)
    assert "Monitor" in app.header_title_text.value
    assert app.body_container.content == app.monitor_view

    # Navigate back to All downloads
    app._handle_sidebar_navigate("STATUS_ALL", "ALL", None)
    assert app.header_title_text.value == "Tutti i Download"
    assert app.dashboard_view.current_filter == "ALL"
    assert app.dashboard_view.current_category_filter == "ALL"
    assert app.body_container.content == app.dashboard_view

    await pool.close()


@pytest.mark.asyncio
async def test_extensions_view_smoke(tmp_path: Path):
    from ui.views.extensions_view import ExtensionsView, InstallExtensionDialog
    from core.extensions.manager import ExtensionManager

    ext_dir = tmp_path / "extensions"
    ext_mgr = ExtensionManager(storage_dir=ext_dir)
    ext_mgr.install_from_js(
        js_source="gopeed.events.onResolve(async (ctx) => {});",
        name="test-plugin",
        match_urls=["*://test.com/*"],
    )

    ad1 = NetworkAdapter(name="Wi-Fi", ip="192.168.1.89", is_primary=True, is_up=True)
    pool = MultiInterfaceSessionPool(adapters=[ad1])
    store = StateStore(tmp_path / "ext_state.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path, extension_manager=ext_mgr)
    coord = UICoordinator(queue_manager=qm, session_pool=pool, extension_manager=ext_mgr)

    view = ExtensionsView(coordinator=coord)
    assert view is not None
    assert len(view.list_view.controls) == 1

    dlg = InstallExtensionDialog(coordinator=coord)
    assert dlg is not None
    assert dlg.url_field is not None

    view._toggle_extension("test-plugin", False)
    assert ext_mgr.get_extension("test-plugin").enabled is False
    view._toggle_extension("test-plugin", True)
    assert ext_mgr.get_extension("test-plugin").enabled is True

    view._delete_extension("test-plugin")
    assert len(view.list_view.controls) == 1

    await pool.close()


def test_ambient_glow_layer_soft_diffusion():
    from ui.theme import create_ambient_glow_layer

    layers = create_ambient_glow_layer()
    assert len(layers) == 1
    bg = layers[0]
    assert isinstance(bg, ft.Container)
    assert bg.expand is True
    grad = bg.gradient
    assert isinstance(grad, ft.LinearGradient)
    assert grad.colors == ["#202020", "#1C1C1C", "#181818"]


@pytest.mark.asyncio
async def test_dynamic_network_badge(tmp_path: Path):
    store = StateStore(tmp_path / "badge.db")

    # 1. VPN Adapter Active
    vpn_ad = NetworkAdapter(name="NordLynx", ip="10.5.0.2", is_primary=True, is_up=True, is_vpn=True)
    eth_ad = NetworkAdapter(name="Ethernet", ip="192.168.1.10", is_primary=False, is_up=True, is_vpn=False)
    pool_vpn = MultiInterfaceSessionPool(adapters=[vpn_ad, eth_ad], vpn_shield_enabled=True)
    qm_vpn = QueueManager(session_pool=pool_vpn, state_store=store, default_download_dir=tmp_path)
    coord_vpn = UICoordinator(queue_manager=qm_vpn, session_pool=pool_vpn)

    app_vpn = ResilientApp(coordinator=coord_vpn, cnl_port=9666)
    app_vpn._build_header()
    assert "🛡️ VPN Shield:" in app_vpn.net_badge_text.value
    assert "NordLynx" in app_vpn.net_badge_text.value
    assert app_vpn.net_badge_dot.bgcolor == "#10B981"

    # 2. Multi-Interface (>= 2) without VPN
    wifi_ad = NetworkAdapter(name="Wi-Fi", ip="192.168.1.50", is_primary=True, is_up=True, is_vpn=False)
    pool_multi = MultiInterfaceSessionPool(adapters=[wifi_ad, eth_ad], vpn_shield_enabled=False)
    qm_multi = QueueManager(session_pool=pool_multi, state_store=store, default_download_dir=tmp_path)
    coord_multi = UICoordinator(queue_manager=qm_multi, session_pool=pool_multi)

    app_multi = ResilientApp(coordinator=coord_multi, cnl_port=9666)
    app_multi._build_header()
    assert "⚡ Net-Merge:" in app_multi.net_badge_text.value
    assert "2 Schede" in app_multi.net_badge_text.value
    assert app_multi.net_badge_dot.bgcolor == "#10B981"

    # 3. Single Direct Adapter without VPN
    pool_single = MultiInterfaceSessionPool(adapters=[wifi_ad], vpn_shield_enabled=False)
    qm_single = QueueManager(session_pool=pool_single, state_store=store, default_download_dir=tmp_path)
    coord_single = UICoordinator(queue_manager=qm_single, session_pool=pool_single)

    app_single = ResilientApp(coordinator=coord_single, cnl_port=9666)
    app_single._build_header()
    assert "🌐 Direct:" in app_single.net_badge_text.value
    assert "Wi-Fi" in app_single.net_badge_text.value
    assert app_single.net_badge_dot.bgcolor == "#38BDF8"
