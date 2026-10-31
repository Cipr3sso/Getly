import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Set
import flet as ft

from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.categories import ALL_CATEGORIES
from core.system.autostart import is_autostart_enabled, set_autostart
from ui.coordinator import UICoordinator, format_speed, format_bytes
from ui.views.dashboard_view import DashboardView
from ui.views.monitor_view import MonitorView
from ui.views.extensions_view import ExtensionsView
from ui.components.add_download_dialog import AddDownloadDialog
from ui.components.clipboard_dialog import ClipboardDownloadDialog
from ui.components.site_grabber_dialog import SiteGrabberDialog
from ui.components.sidebar import CollapsibleSidebar, SIDEBAR_WIDTH_COMPACT
from ui.components.torrent_dialog import TorrentDialog
from ui.components.operations_drawer import OperationsDrawer
from ui.tray import SystemTrayManager
from ui.clipboard_watcher import ClipboardWatcher
from ui.theme import (
    BG_WINDOW,
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    ACCENT_BLUE_WINUI,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    BORDER_LUMINESCENT,
    create_ambient_glow_layer,
    safe_update,
)
from ui.icon_generator import (
    ensure_app_icons,
    apply_windows_taskbar_icon,
    set_windows_app_user_model_id,
)

logger = logging.getLogger(__name__)


class ResilientApp:
    """
    Main Flet Application controller for Getly:
    - Fixed Windows 11 Fluent Dark UI (Zero Light Mode / Purple remnants).
    - Compact Navigation Rail (width=56).
    - Clean Top Bar with Brand, Dynamic Network Badge, Quick Search, and Window Controls.
    - Zero global page.update() calls on ticks: 0ms mouse click response.
    - System Tray and clipboard integration.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        cnl_port: int = 9666,
        on_shutdown: Optional[Callable[[], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        start_minimized: bool = False,
    ):
        self.coordinator = coordinator
        self.cnl_port = cnl_port
        self.on_shutdown = on_shutdown
        self.loop = loop
        self.start_minimized = start_minimized

        self.page: Optional[ft.Page] = None
        self.tray_manager = SystemTrayManager(
            coordinator=coordinator,
            on_exit=self.shutdown,
            loop=loop,
        )
        self.clipboard_watcher: Optional[ClipboardWatcher] = None

        self.current_tab_index = 0  # 0: Dashboard, 1: Monitor, 2: Extensions
        self.sidebar: Optional[CollapsibleSidebar] = None
        self.dashboard_view: Optional[DashboardView] = None
        self.monitor_view: Optional[MonitorView] = None
        self.extensions_view: Optional[ExtensionsView] = None
        self.body_container: Optional[ft.Container] = None
        self.header_title_text: Optional[ft.Text] = None
        self.net_badge_container: Optional[ft.Container] = None
        self.net_badge_dot: Optional[ft.Container] = None
        self.net_badge_text: Optional[ft.Text] = None
        self.operations_drawer: Optional[OperationsDrawer] = None
        self.search_field: Optional[ft.TextField] = None

        self.grabber_enabled = True
        self.autostart_enabled = is_autostart_enabled()
        self._notified_completed_tasks: Set[str] = set()

    async def main(self, page: ft.Page) -> None:
        """Flet main entrypoint."""
        self.page = page

        # Ensure universal brand icons exist
        icons = ensure_app_icons()
        ico_path = os.path.abspath(str(icons["ico"]))
        png_path = os.path.abspath(str(icons["png"]))

        # 1. Window & Page Configuration (Fixed Windows 11 Fluent Dark)
        page.title = "Getly"
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(
            scrollbar_theme=ft.ScrollbarTheme(
                thickness=0,
                thumb_visibility=False,
                track_visibility=False,
                radius=0,
            )
        )
        page.bgcolor = BG_WINDOW
        page.padding = 0

        # Frameless Custom Titlebar & Window Icon
        page.window.icon = ico_path
        page.window.title_bar_hidden = True
        page.window.title_bar_buttons_hidden = True

        page.window.width = 1100
        page.window.height = 740
        page.window.min_width = 820
        page.window.min_height = 540

        if self.start_minimized:
            page.window.visible = False

        # 2. Setup System Tray & Lifecycle
        self.tray_manager.setup(page)

        def _focus_window():
            def _bring_forward():
                page.window.minimized = False
                page.window.visible = True
                safe_update(page)
                if hasattr(page, "run_task") and hasattr(page.window, "to_front"):
                    page.run_task(page.window.to_front)
            target_loop = self.loop or getattr(page, "loop", None)
            if target_loop and target_loop.is_running():
                target_loop.call_soon_threadsafe(_bring_forward)
            else:
                _bring_forward()

        self.coordinator.focus_handler = _focus_window

        # Wire queue manager on_task_added to provide immediate GUI & Tray feedback
        def _on_task_added(task: DownloadTask):
            display_name = task.filename or (task.url.split("/")[-1].split("?")[0] if "/" in task.url else task.url) or "Nuovo Download"
            self.tray_manager.notify(
                title="Download Catturato!",
                message=f"In coda: {display_name}",
            )
            def _refresh():
                if self.dashboard_view:
                    self.dashboard_view.refresh_tasks()
                if self.sidebar:
                    self._update_sidebar_counts()
                safe_update(self.body_container)
            target_loop = self.loop or getattr(page, "loop", None)
            if target_loop and target_loop.is_running():
                target_loop.call_soon_threadsafe(_refresh)
            else:
                _refresh()

        self.coordinator.queue_manager.on_task_added = _on_task_added

        # Wire confirmation prompt handler for incoming external downloads
        def _prompt_download(data: dict):
            try:
                page.window.minimized = False
                page.window.visible = True
                if hasattr(page, "run_task") and hasattr(page.window, "to_front"):
                    page.run_task(page.window.to_front)
            except Exception:
                pass

            target_url = data.get("url", "")
            target_fn = data.get("filename", "")
            is_torrent = (
                target_url.startswith("magnet:")
                or target_url.lower().endswith(".torrent")
                or target_fn.lower().endswith(".torrent")
            )

            if is_torrent:
                dlg = TorrentDialog(
                    coordinator=self.coordinator,
                    source=target_url,
                    on_added=lambda: self.dashboard_view.refresh_tasks() if self.dashboard_view else None,
                    initial_dir=Path(data.get("destination_dir")) if data.get("destination_dir") else None,
                )
            else:
                dlg = AddDownloadDialog(
                    coordinator=self.coordinator,
                    prefill_url=target_url,
                    prefill_filename=target_fn,
                    prefill_dir=Path(data.get("destination_dir")) if data.get("destination_dir") else None,
                    cookies=data.get("cookies"),
                    referer=data.get("referer"),
                    user_agent=data.get("user_agent"),
                    on_added=lambda: self.dashboard_view.refresh_tasks() if self.dashboard_view else None,
                )
            page.show_dialog(dlg)

        def _threadsafe_prompt(data: dict):
            target_loop = self.loop or getattr(page, "loop", None)
            if target_loop and target_loop.is_running():
                target_loop.call_soon_threadsafe(lambda: _prompt_download(data))
            else:
                _prompt_download(data)

        self.coordinator.prompt_download_handler = _threadsafe_prompt

        # 3. Setup Clipboard Watcher
        def on_link_detected(url: str):
            clean_url = url.strip()
            is_torrent = clean_url.startswith("magnet:") or clean_url.lower().endswith(".torrent")

            if self.coordinator.ask_before_download:
                def _prompt():
                    try:
                        page.window.minimized = False
                        page.window.visible = True
                        if hasattr(page, "run_task") and hasattr(page.window, "to_front"):
                            page.run_task(page.window.to_front)
                    except Exception:
                        pass
                    if is_torrent:
                        dlg = TorrentDialog(
                            coordinator=self.coordinator,
                            source=clean_url,
                            on_added=lambda: self.dashboard_view.refresh_tasks() if self.dashboard_view else None,
                        )
                    else:
                        dlg = AddDownloadDialog(
                            coordinator=self.coordinator,
                            prefill_url=clean_url,
                            on_added=lambda: self.dashboard_view.refresh_tasks() if self.dashboard_view else None,
                        )
                    page.show_dialog(dlg)

                target_loop = self.loop or getattr(page, "loop", None)
                if target_loop and target_loop.is_running():
                    target_loop.call_soon_threadsafe(_prompt)
                else:
                    _prompt()
            else:
                target_loop = self.loop or getattr(page, "loop", None)
                if target_loop and target_loop.is_running():
                    target_loop.create_task(self.coordinator.add_download(url=clean_url, task_type="TORRENT" if is_torrent else "HTTP"))

        self.clipboard_watcher = ClipboardWatcher(
            on_link_detected=on_link_detected,
            poll_interval_sec=1.0,
            enabled=True,
        )
        self.clipboard_watcher.start()

        # 4. Initialize Views & Sidebar
        self.dashboard_view = DashboardView(
            coordinator=self.coordinator,
            on_add_requested=self._open_add_dialog,
        )
        self.monitor_view = MonitorView(coordinator=self.coordinator)
        self.extensions_view = ExtensionsView(
            coordinator=self.coordinator,
            on_extension_updated=self._update_sidebar_counts,
        )

        self.body_container = ft.Container(
            expand=True,
            content=self.dashboard_view,
        )

        self.sidebar = CollapsibleSidebar(
            on_navigate=self._handle_sidebar_navigate,
            on_toggle_grabber=self._toggle_grabber,
            on_toggle_ask_save=self._toggle_ask_save,
            on_toggle_autostart=self._toggle_autostart,
            on_toggle_vpn_shield=self._toggle_vpn_shield,
            on_toggle_theme=self._toggle_theme,
            cnl_port=self.cnl_port,
            grabber_enabled=self.grabber_enabled,
            ask_save_enabled=self.coordinator.ask_before_download,
            autostart_enabled=self.autostart_enabled,
            vpn_shield_enabled=getattr(self.coordinator.session_pool, "vpn_shield_enabled", True),
            initially_expanded=False,
        )

        # 5. Bottom Operations Drawer
        self.operations_drawer = OperationsDrawer(
            coordinator=self.coordinator,
            on_action=self._on_operations_drawer_action,
        )

        # 6. Top Application Bar
        header = self._build_header(png_path=png_path)

        main_layout = ft.Row(
            expand=True,
            spacing=0,
            controls=[
                self.sidebar,
                self.body_container,
            ],
        )

        ambient_glow_controls = create_ambient_glow_layer()
        foreground_column = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                header,
                ft.Divider(color=BORDER_COLOR, height=1),
                main_layout,
                self.operations_drawer,
            ],
        )

        page.add(
            ft.Stack(
                expand=True,
                controls=[
                    *ambient_glow_controls,
                    foreground_column,
                ],
            )
        )

        # Apply Win32 taskbar & window icon for immediate taskbar update on Windows
        def _apply_win32_icon():
            apply_windows_taskbar_icon(ico_path, page.title)

        target_loop = self.loop or getattr(page, "loop", None)
        if target_loop and target_loop.is_running():
            target_loop.call_later(0.15, _apply_win32_icon)
        else:
            _apply_win32_icon()

        # Responsive window resize listener
        page.on_resize = self._on_window_resize
        if getattr(page, "width", None):
            self._on_window_resize()

        # Initial counts populate
        self._update_sidebar_counts()

        # 7. Subscribe to Coordinator Metrics Ticker
        self.coordinator.add_listener(self._on_metrics_tick)
        await self.coordinator.start()

    def _get_network_badge_config(self) -> dict:
        """Determines the live network badge configuration based on VPN and interface counts."""
        pool = self.coordinator.session_pool
        active = pool.active_adapters if hasattr(pool, "active_adapters") else []
        all_ads = pool.all_adapters if hasattr(pool, "all_adapters") else active

        # Check for active VPN interface
        vpn_adapter = next((a for a in active if getattr(a, "is_vpn", False)), None)
        if not vpn_adapter:
            vpn_adapter = next((a for a in all_ads if getattr(a, "is_vpn", False) and a.is_up), None)

        if vpn_adapter:
            name = vpn_adapter.friendly_name or vpn_adapter.name
            return {
                "text": f"🛡️ VPN Shield: {name}",
                "color": "#34D399",
                "dot_color": "#10B981",
                "bg": "rgba(16, 185, 129, 0.12)",
                "border": "rgba(16, 185, 129, 0.35)",
                "tooltip": f"VPN Kill-Switch e protezione privacy attivi su {name} ({vpn_adapter.ip})",
            }
        elif len(active) >= 2:
            return {
                "text": f"⚡ Net-Merge: {len(active)} Schede",
                "color": "#34D399",
                "dot_color": "#10B981",
                "bg": "rgba(16, 185, 129, 0.12)",
                "border": "rgba(16, 185, 129, 0.35)",
                "tooltip": f"Aggregazione Multi-Interfaccia attiva su {len(active)} schede di rete",
            }
        elif len(active) == 1:
            name = active[0].friendly_name or active[0].name
            return {
                "text": f"🌐 Direct: {name}",
                "color": "#38BDF8",
                "dot_color": "#38BDF8",
                "bg": "rgba(56, 189, 248, 0.12)",
                "border": "rgba(56, 189, 248, 0.35)",
                "tooltip": f"Connessione diretta singola su {name} ({active[0].ip})",
            }
        else:
            return {
                "text": "🌐 Direct: Default",
                "color": "#948FA8",
                "dot_color": "#948FA8",
                "bg": "rgba(148, 143, 168, 0.12)",
                "border": "rgba(148, 143, 168, 0.35)",
                "tooltip": "Nessuna interfaccia dedicata; routing gestito dal sistema operativo",
            }

    def _update_network_badge(self) -> None:
        """Updates the top bar network badge in-place if network status changed."""
        if not self.net_badge_container or not self.net_badge_text or not self.net_badge_dot:
            return
        cfg = self._get_network_badge_config()
        if self.net_badge_text.value != cfg["text"]:
            self.net_badge_text.value = cfg["text"]
            self.net_badge_text.color = cfg["color"]
            self.net_badge_dot.bgcolor = cfg["dot_color"]
            self.net_badge_container.bgcolor = cfg["bg"]
            self.net_badge_container.border = ft.Border.all(1, cfg["border"])
            self.net_badge_container.tooltip = cfg["tooltip"]
            safe_update(self.net_badge_container)

    def _build_header(self, png_path: Optional[str] = None) -> ft.Container:
        self.header_title_text = ft.Text("Tutti i Download", size=13, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, visible=False)

        # Build dynamic network badge
        cfg = self._get_network_badge_config()
        self.net_badge_dot = ft.Container(width=6, height=6, border_radius=3, bgcolor=cfg["dot_color"])
        self.net_badge_text = ft.Text(cfg["text"], size=10, color=cfg["color"], weight=ft.FontWeight.BOLD)
        self.net_badge_container = ft.Container(
            bgcolor=cfg["bg"],
            padding=ft.Padding(left=8, right=10, top=4, bottom=4),
            border_radius=16,
            border=ft.Border.all(1, cfg["border"]),
            content=ft.Row(
                spacing=5,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self.net_badge_dot,
                    self.net_badge_text,
                ],
            ),
            tooltip=cfg["tooltip"],
        )

        # 1. Left Zone: Hamburger Button (outside WindowDragArea) + Icon/Title (inside WindowDragArea)
        self.hamburger_btn = ft.IconButton(
            icon=ft.Icons.MENU_ROUNDED,
            icon_size=18,
            icon_color=TEXT_PRIMARY,
            tooltip="Espandi / Contrai barra laterale (WinUI 3)",
            on_click=self._toggle_sidebar_visibility,
            visible=True,
        )

        app_icon_ctrl = None
        if png_path and os.path.exists(png_path):
            app_icon_ctrl = ft.Image(
                src=png_path,
                width=20,
                height=20,
                fit=ft.BoxFit.CONTAIN,
                error_content=ft.Icon(ft.Icons.ROCKET_LAUNCH_ROUNDED, color=ACCENT_BLUE, size=18),
            )
        else:
            app_icon_ctrl = ft.Icon(ft.Icons.ROCKET_LAUNCH_ROUNDED, color=ACCENT_BLUE, size=18)

        left_zone = ft.Row(
            spacing=8,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                app_icon_ctrl,
                ft.Text("Getly", size=14, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                self.net_badge_container,
                self.header_title_text,
            ],
        )

        left_drag = ft.WindowDragArea(
            content=ft.Container(
                content=left_zone,
                padding=ft.Padding(left=4, right=10, top=4, bottom=4),
            )
        )

        left_side = ft.Row(
            spacing=2,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.hamburger_btn,
                left_drag,
            ],
        )

        # 2. Center Zone: WinUI 3 Search Box ("Filtra download...")
        self.search_field = ft.TextField(
            hint_text="Filtra download...",
            hint_style=ft.TextStyle(size=12, color=TEXT_SECONDARY),
            text_style=ft.TextStyle(size=12, color=TEXT_PRIMARY),
            border=ft.NoInputBorder(),
            dense=True,
            content_padding=ft.Padding(left=4, right=8, top=2, bottom=4),
            expand=True,
            on_change=self._on_search_query_changed,
        )
        search_box = ft.Container(
            width=320,
            height=32,
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(left=8, right=6, top=0, bottom=0),
            content=ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.SEARCH_ROUNDED, size=15, color=TEXT_SECONDARY),
                    self.search_field,
                ],
            ),
        )

        center_drag = ft.WindowDragArea(
            expand=True,
            content=ft.Container(
                alignment=ft.Alignment(0, 0),
                content=search_box,
            ),
        )

        # 3. Right Zone: Window Controls outside WindowDragArea
        window_controls = ft.Row(
            spacing=0,
            tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.IconButton(
                    icon=ft.Icons.REMOVE_ROUNDED,
                    icon_size=14,
                    icon_color=TEXT_SECONDARY,
                    tooltip="Minimizza",
                    on_click=self._minimize_window,
                ),
                ft.IconButton(
                    icon=ft.Icons.CROP_SQUARE_ROUNDED,
                    icon_size=12,
                    icon_color=TEXT_SECONDARY,
                    tooltip="Ingrandisci / Ripristina",
                    on_click=self._maximize_window,
                ),
                ft.IconButton(
                    icon=ft.Icons.CLOSE_ROUNDED,
                    icon_size=14,
                    icon_color=TEXT_SECONDARY,
                    tooltip="Chiudi (Nascondi nella Tray)",
                    on_click=self._close_window,
                ),
            ],
        )

        header_container = ft.Container(
            bgcolor=BG_PANEL,
            padding=ft.Padding(left=4, right=4, top=2, bottom=2),
            border=ft.Border(bottom=ft.BorderSide(1, BORDER_COLOR)),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    left_side,
                    center_drag,
                    window_controls,
                ],
            ),
        )

        return header_container

    def _handle_sidebar_navigate(self, key: str, status: Optional[str], category: Optional[str]) -> None:
        """Invoked when an item in the CollapsibleSidebar is clicked."""
        if key in ("TOOL_MONITOR", "NAV_MONITOR"):
            self.current_tab_index = 1
            if self.body_container and self.monitor_view:
                self.body_container.content = self.monitor_view
                self.monitor_view.refresh_adapters()
            if self.header_title_text:
                self.header_title_text.value = "Net-Merge Monitor"
        elif key in ("TOOL_GRABBER", "NAV_GRABBER"):
            self._open_site_grabber_dialog()
            return
        elif key in ("TOOL_EXTENSIONS", "NAV_EXTENSIONS"):
            self.current_tab_index = 2
            if self.body_container and self.extensions_view:
                self.body_container.content = self.extensions_view
                self.extensions_view.refresh_extensions()
            if self.header_title_text:
                self.header_title_text.value = "Estensioni Gopeed"
        else:
            self.current_tab_index = 0
            if self.body_container and self.dashboard_view:
                self.body_container.content = self.dashboard_view
                self.dashboard_view.set_filters(filter_status=status or "ALL", filter_category=category or "ALL")

            if category:
                title = f"Categoria: {category}"
            elif status == "ACTIVE":
                title = "Download In Corso"
            elif status == "COMPLETED":
                title = "Download Completati"
            elif status == "PAUSED":
                title = "Download In Pausa"
            elif status == "FAILED":
                title = "Errori e Annullati"
            else:
                title = "Tutti i Download"

            if self.header_title_text:
                self.header_title_text.value = title

        safe_update(self.body_container)

    def _update_sidebar_counts(self) -> None:
        """Calculates dynamic task and category counts and updates sidebar."""
        all_tasks = self.coordinator.get_tasks("ALL")
        status_counts = {
            "ALL": len(all_tasks),
            "ACTIVE": sum(1 for t in all_tasks if t.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PENDING)),
            "COMPLETED": sum(1 for t in all_tasks if t.status == DownloadStatus.COMPLETED),
            "PAUSED": sum(1 for t in all_tasks if t.status == DownloadStatus.PAUSED),
            "FAILED": sum(1 for t in all_tasks if t.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED)),
        }
        category_counts = {cat: 0 for cat in ALL_CATEGORIES}
        for t in all_tasks:
            c = t.category or "Altro"
            category_counts[c] = category_counts.get(c, 0) + 1

        if self.sidebar:
            self.sidebar.update_counts(
                status_counts,
                category_counts,
                extensions_count=self.coordinator.active_extensions_count,
            )

    def _toggle_grabber(self, e: Optional[ft.ControlEvent] = None) -> None:
        self.grabber_enabled = not self.grabber_enabled
        if self.clipboard_watcher:
            self.clipboard_watcher.enabled = self.grabber_enabled
        if self.sidebar:
            self.sidebar.update_footer_states(
                grabber_enabled=self.grabber_enabled,
                ask_save_enabled=self.coordinator.ask_before_download,
                autostart_enabled=self.autostart_enabled,
                vpn_shield_enabled=getattr(self.coordinator.session_pool, "vpn_shield_enabled", True),
            )

    def _toggle_ask_save(self, e: Optional[ft.ControlEvent] = None) -> None:
        self.coordinator.ask_before_download = not self.coordinator.ask_before_download
        if self.sidebar:
            self.sidebar.update_footer_states(
                grabber_enabled=self.grabber_enabled,
                ask_save_enabled=self.coordinator.ask_before_download,
                autostart_enabled=self.autostart_enabled,
                vpn_shield_enabled=getattr(self.coordinator.session_pool, "vpn_shield_enabled", True),
            )

    def _toggle_autostart(self, e: Optional[ft.ControlEvent] = None) -> None:
        new_state = not self.autostart_enabled
        success = set_autostart(new_state)
        if success:
            self.autostart_enabled = new_state
        else:
            self.autostart_enabled = is_autostart_enabled()
        if self.sidebar:
            self.sidebar.update_footer_states(
                grabber_enabled=self.grabber_enabled,
                ask_save_enabled=self.coordinator.ask_before_download,
                autostart_enabled=self.autostart_enabled,
                vpn_shield_enabled=getattr(self.coordinator.session_pool, "vpn_shield_enabled", True),
            )

    def _toggle_vpn_shield(self, e: Optional[ft.ControlEvent] = None) -> None:
        pool = self.coordinator.session_pool
        if hasattr(pool, "vpn_shield_enabled"):
            pool.vpn_shield_enabled = not pool.vpn_shield_enabled
            if self.sidebar:
                self.sidebar.update_footer_states(
                    grabber_enabled=self.grabber_enabled,
                    ask_save_enabled=self.coordinator.ask_before_download,
                    autostart_enabled=self.autostart_enabled,
                    vpn_shield_enabled=pool.vpn_shield_enabled,
                )
            self._update_network_badge()

    def _open_site_grabber_dialog(self) -> None:
        if not self.page:
            return
        dialog = SiteGrabberDialog(
            coordinator=self.coordinator,
            on_added=lambda: self.dashboard_view.refresh_tasks() if self.dashboard_view else None,
        )
        self.page.show_dialog(dialog)

    def _minimize_window(self, e: ft.ControlEvent) -> None:
        if self.page:
            self.page.window.minimized = True
            safe_update(self.page)

    def _maximize_window(self, e: ft.ControlEvent) -> None:
        if self.page:
            self.page.window.maximized = not self.page.window.maximized
            safe_update(self.page)

    def _close_window(self, e: ft.ControlEvent) -> None:
        if self.page:
            self.page.window.visible = False
            safe_update(self.page)
            self.tray_manager.notify(
                title="Getly",
                message="L'applicazione continua a scaricare in background nella System Tray.",
            )

    def _switch_tab(self, index: int) -> None:
        """Backwards-compatible tab switcher method."""
        self.current_tab_index = index
        if index == 0:
            if self.dashboard_view:
                self.dashboard_view.refresh_tasks()
            if self.body_container:
                self.body_container.content = self.dashboard_view
            if self.header_title_text:
                self.header_title_text.value = "Tutti i Download"
            if self.sidebar:
                self.sidebar.set_active_key("STATUS_ALL")
        else:
            if self.monitor_view:
                self.monitor_view.refresh_adapters()
            if self.body_container:
                self.body_container.content = self.monitor_view
            if self.header_title_text:
                self.header_title_text.value = "Net-Merge Monitor"
            if self.sidebar:
                self.sidebar.set_active_key("TOOL_MONITOR")

        safe_update(self.body_container)

    def _on_search_query_changed(self, e: ft.ControlEvent) -> None:
        val = e.control.value or ""
        if self.dashboard_view:
            self.dashboard_view.set_search_query(val)

    def _handle_back_clicked(self, e: ft.ControlEvent) -> None:
        if self.current_tab_index != 0:
            self._switch_tab(0)
        elif self.dashboard_view:
            if self.search_field:
                self.search_field.value = ""
                safe_update(self.search_field)
            self.dashboard_view.set_search_query("")

    def _toggle_sidebar_visibility(self, e: Optional[ft.ControlEvent] = None) -> None:
        """Toggles Navigation Rail between Compact (56px) and Extended (200px), or hides/shows when < 720px."""
        if not self.sidebar:
            return

        page_width = None
        if hasattr(self, "page") and self.page:
            page_width = getattr(self.page, "width", None)

        if page_width is not None and page_width < 720:
            # Narrow screen (< 720px): toggle visible True <-> False
            self.sidebar.visible = not self.sidebar.visible
            safe_update(self.sidebar)
        else:
            # Desktop standard (>= 720px): always visible, toggle compact (56px) <-> extended (200px)
            self.sidebar.visible = True
            self.sidebar.toggle_expanded()

        # Immediately update command bar responsiveness with the new rail width
        rail_width = float(self.sidebar.width) if (self.sidebar and self.sidebar.visible) else 0.0
        if self.dashboard_view:
            self.dashboard_view.on_page_resize(page_width, rail_width=rail_width)

    def _on_window_resize(self, e: Optional[ft.ControlEvent] = None) -> None:
        """Responsive breakpoints: < 720px rail hide, available_width command bar & filters overlay."""
        width = None
        if e is not None:
            width = getattr(e, "width", None)
        if width is None and hasattr(self, "page") and self.page:
            width = getattr(self.page, "width", None)
        if width is None or width <= 0:
            return

        # 1. Navigation rail visibility (< 720px)
        if self.sidebar:
            if width < 720 and self.sidebar.visible:
                self.sidebar.visible = False
                safe_update(self.sidebar)
            elif width >= 720 and not self.sidebar.visible:
                self.sidebar.visible = True
                safe_update(self.sidebar)

        # 2. Forward width and real rail_width to DashboardView
        rail_width = float(self.sidebar.width) if (self.sidebar and self.sidebar.visible) else 0.0
        if self.dashboard_view:
            self.dashboard_view.on_page_resize(width, rail_width=rail_width)

    def _handle_toggle_sidebar(self, e: ft.ControlEvent) -> None:
        self._toggle_sidebar_visibility(e)

    def _toggle_theme(self) -> None:
        """Enforces fixed Windows 11 Fluent Dark theme."""
        if self.page:
            self.page.theme_mode = ft.ThemeMode.DARK
            self.page.bgcolor = BG_WINDOW
            safe_update(self.page)

    def _on_operations_drawer_action(self) -> None:
        if self.dashboard_view:
            self.dashboard_view.refresh_tasks()
        self._update_sidebar_counts()
        safe_update(self.body_container)

    def _handle_pause_all(self, e: ft.ControlEvent) -> None:
        async def _pause():
            await self.coordinator.pause_all()
            if self.dashboard_view:
                self.dashboard_view.refresh_tasks()
            self._update_sidebar_counts()

        if e.page:
            e.page.run_task(_pause)

    def _handle_clear_inactive(self, e: ft.ControlEvent) -> None:
        async def _clear():
            await self.coordinator.clear_inactive_tasks()
            if self.dashboard_view:
                self.dashboard_view.refresh_tasks()
            self._update_sidebar_counts()

        if e.page:
            e.page.run_task(_clear)

    def _open_add_dialog(self) -> None:
        if not self.page:
            return
        dialog = AddDownloadDialog(
            coordinator=self.coordinator,
            on_added=lambda: self.dashboard_view.refresh_tasks() if self.dashboard_view else None,
        )
        self.page.show_dialog(dialog)

    def _on_metrics_tick(self) -> None:
        """
        Invoked on each metrics tick by UICoordinator.
        Surgical updates ONLY:
        - Updates operations drawer (self.operations_drawer.update_live_metrics())
        - Updates active rows/cards in dashboard view (self.dashboard_view.update_live_metrics())
        - NEVER calls page.update(), sidebar.update(), or header.update()!
        """
        # 1. Check for newly completed tasks to trigger Toast Notifications
        tasks = self.coordinator.get_tasks("ALL")
        status_changed = False
        for t in tasks:
            if t.status == DownloadStatus.COMPLETED and t.task_id not in self._notified_completed_tasks:
                self._notified_completed_tasks.add(t.task_id)
                self.tray_manager.notify(
                    title="Download Completato!",
                    message=f"{t.filename or 'File'} salvato in {t.destination_dir} ({format_bytes(t.total_size)})",
                )
                status_changed = True

        # If a task finished, update task list and sidebar counts once
        if status_changed:
            if self.dashboard_view:
                self.dashboard_view.refresh_tasks()
            self._update_sidebar_counts()

        # 2. Update dynamic network / VPN status badge in header if changed
        self._update_network_badge()

        # 3. Update operations drawer surgically
        if self.operations_drawer:
            self.operations_drawer.update_live_metrics()

        # 4. Update visible view surgically without touching page
        if self.body_container and self.body_container.content == self.dashboard_view and self.dashboard_view:
            self.dashboard_view.update_live_metrics()
        elif self.body_container and self.body_container.content == self.monitor_view and self.monitor_view:
            self.monitor_view.update_live_metrics()

    async def shutdown(self) -> None:
        """Performs graceful shutdown of background services."""
        if self.clipboard_watcher:
            self.clipboard_watcher.stop()
        await self.coordinator.stop()
        self.tray_manager.stop()
        if self.on_shutdown:
            try:
                res = self.on_shutdown()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.debug("Error in on_shutdown callback: %s", exc)


async def run_gui(
    queue_manager: QueueManager,
    session_pool: MultiInterfaceSessionPool,
    cnl_port: int = 9666,
    on_shutdown: Optional[Callable[[], Any]] = None,
    coordinator: Optional[UICoordinator] = None,
    start_minimized: bool = False,
) -> None:
    """Entrypoint function to run Flet Desktop GUI."""
    set_windows_app_user_model_id("Getly")
    loop = asyncio.get_running_loop()
    coord = coordinator or UICoordinator(queue_manager, session_pool)
    app = ResilientApp(
        coordinator=coord,
        cnl_port=cnl_port,
        on_shutdown=on_shutdown,
        loop=loop,
        start_minimized=start_minimized,
    )

    async def flet_main(page: ft.Page):
        await app.main(page)

    try:
        await ft.run_async(flet_main, view=ft.AppView.FLET_APP)
    finally:
        await app.shutdown()


GetlyApp = ResilientApp
