import asyncio
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
import flet as ft

from core.scheduler.categories import (
    CATEGORY_COMPRESSED,
    CATEGORY_PROGRAMS,
    CATEGORY_VIDEO,
    CATEGORY_AUDIO,
    CATEGORY_DOCUMENTS,
    CATEGORY_IMAGES_ISO,
    CATEGORY_TORRENTS,
)
from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    ACCENT_BLUE_WINUI,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    safe_update,
)
from ui.components.settings_dialog import SettingsDialog

SIDEBAR_WIDTH_COMPACT = 56
SIDEBAR_WIDTH_EXTENDED = 200


@dataclass
class SidebarItemData:
    key: str
    label: str
    icon: str
    status: Optional[str] = None
    category: Optional[str] = None
    is_tool: bool = False


class CollapsibleSidebar(ft.Container):
    """
    Windows 11 Fluent Dark Navigation Rail (width=56, bgcolor=#202020):
    - Top Macro Navigation:
        1. 📥 Download (NAV_DOWNLOADS)
        2. 🌐 Site Grabber (NAV_GRABBER)
        3. 📡 Net-Merge Monitor (NAV_MONITOR)
        4. 🧩 Estensioni (NAV_EXTENSIONS)
      Using native ft.IconButton controls for instantaneous (0ms) click response,
      with 3px Fluent blue active indicator strips and dynamic count badges.
    - Bottom Action:
        ⚙️ Impostazioni Getly (SettingsDialog with all system toggles)
    """

    def __init__(
        self,
        on_navigate: Callable[[str, Optional[str], Optional[str]], None],
        on_toggle_grabber: Callable[[], None],
        on_toggle_ask_save: Callable[[], None],
        on_toggle_autostart: Optional[Callable[[], None]] = None,
        on_toggle_vpn_shield: Optional[Callable[[], None]] = None,
        on_toggle_theme: Optional[Callable[[], None]] = None,
        cnl_port: int = 9666,
        grabber_enabled: bool = True,
        ask_save_enabled: bool = True,
        autostart_enabled: bool = False,
        vpn_shield_enabled: bool = True,
        initially_expanded: bool = False,
        is_dark_theme: bool = True,
        width: int = 56,
    ):
        super().__init__()
        self.on_navigate = on_navigate
        self.on_toggle_grabber = on_toggle_grabber
        self.on_toggle_ask_save = on_toggle_ask_save
        self.on_toggle_autostart = on_toggle_autostart
        self.on_toggle_vpn_shield = on_toggle_vpn_shield
        self.on_toggle_theme = on_toggle_theme
        self.cnl_port = cnl_port
        self.grabber_enabled = grabber_enabled
        self.ask_save_enabled = ask_save_enabled
        self.autostart_enabled = autostart_enabled
        self.vpn_shield_enabled = vpn_shield_enabled
        self.is_expanded = initially_expanded
        self.is_dark_theme = is_dark_theme

        self.active_key = "NAV_DOWNLOADS"

        # Fixed rail width and Windows 11 Fluent Dark styling
        self.width = SIDEBAR_WIDTH_EXTENDED if self.is_expanded else width
        self.bgcolor = BG_PANEL
        self.border = ft.Border(right=ft.BorderSide(1, BORDER_COLOR))
        self.padding = ft.Padding(left=4, right=4, top=8, bottom=8)

        # Dynamic task and category counts
        self.status_counts: Dict[str, int] = {
            "ALL": 0, "ACTIVE": 0, "COMPLETED": 0, "PAUSED": 0, "FAILED": 0
        }
        self.category_counts: Dict[str, int] = {
            CATEGORY_COMPRESSED: 0,
            CATEGORY_PROGRAMS: 0,
            CATEGORY_VIDEO: 0,
            CATEGORY_AUDIO: 0,
            CATEGORY_DOCUMENTS: 0,
            CATEGORY_IMAGES_ISO: 0,
            CATEGORY_TORRENTS: 0,
        }
        self.extensions_count: int = 0

        # Backward compatibility item references
        self.status_items: List[SidebarItemData] = [
            SidebarItemData("STATUS_ALL", "Tutti", ft.Icons.ALL_INBOX_ROUNDED, status="ALL"),
            SidebarItemData("STATUS_ACTIVE", "In Corso", ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED, status="ACTIVE"),
            SidebarItemData("STATUS_COMPLETED", "Completati", ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, status="COMPLETED"),
            SidebarItemData("STATUS_PAUSED", "In Pausa", ft.Icons.PAUSE_CIRCLE_OUTLINE_ROUNDED, status="PAUSED"),
            SidebarItemData("STATUS_FAILED", "Errori", ft.Icons.ERROR_OUTLINE_ROUNDED, status="FAILED"),
        ]

        self.category_items: List[SidebarItemData] = [
            SidebarItemData("CAT_Torrents", "Torrent & Magnet", ft.Icons.HUB_ROUNDED, category=CATEGORY_TORRENTS),
            SidebarItemData("CAT_Compressi", "Compressi", ft.Icons.FOLDER_ZIP_ROUNDED, category=CATEGORY_COMPRESSED),
            SidebarItemData("CAT_Programmi", "Programmi", ft.Icons.TERMINAL_ROUNDED, category=CATEGORY_PROGRAMS),
            SidebarItemData("CAT_Video", "Video", ft.Icons.VIDEO_FILE_ROUNDED, category=CATEGORY_VIDEO),
            SidebarItemData("CAT_Audio", "Audio", ft.Icons.AUDIO_FILE_ROUNDED, category=CATEGORY_AUDIO),
            SidebarItemData("CAT_Documenti", "Documenti & 3D", ft.Icons.DESCRIPTION_ROUNDED, category=CATEGORY_DOCUMENTS),
            SidebarItemData("CAT_Immagini_ISO", "Immagini & ISO", ft.Icons.IMAGE_ROUNDED, category=CATEGORY_IMAGES_ISO),
        ]

        self.tool_items: List[SidebarItemData] = [
            SidebarItemData("TOOL_MONITOR", "Net-Merge Monitor", ft.Icons.NETWORK_CHECK_ROUNDED, is_tool=True),
            SidebarItemData("TOOL_GRABBER", "Site Grabber", ft.Icons.PUBLIC_ROUNDED, is_tool=True),
            SidebarItemData("TOOL_EXTENSIONS", "Estensioni", ft.Icons.EXTENSION_ROUNDED, is_tool=True),
        ]

        # Top 4 Macro Navigation Items
        self.macro_items: List[SidebarItemData] = [
            SidebarItemData("NAV_DOWNLOADS", "Download", ft.Icons.DOWNLOAD_ROUNDED, status="ALL"),
            SidebarItemData("NAV_GRABBER", "Site Grabber", ft.Icons.PUBLIC_ROUNDED, is_tool=True),
            SidebarItemData("NAV_MONITOR", "Net-Merge", ft.Icons.NETWORK_CHECK_ROUNDED, is_tool=True),
            SidebarItemData("NAV_EXTENSIONS", "Estensioni", ft.Icons.EXTENSION_ROUNDED, is_tool=True),
        ]

        self.top_items_column = ft.Column(
            spacing=4,
            alignment=ft.MainAxisAlignment.START,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.bottom_items_column = ft.Column(
            spacing=4,
            alignment=ft.MainAxisAlignment.END,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.content = self._build_layout()
        self._render_items()

    def _build_layout(self) -> ft.Column:
        return ft.Column(
            expand=True,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.top_items_column,
                self.bottom_items_column,
            ],
        )

    def set_expanded(self, expanded: bool) -> None:
        """Sets the expansion state of the sidebar (56px compact <-> 200px extended)."""
        if self.is_expanded != expanded:
            self.is_expanded = expanded
            self.width = SIDEBAR_WIDTH_EXTENDED if self.is_expanded else SIDEBAR_WIDTH_COMPACT
            self._render_items()
            safe_update(self)

    def toggle_expanded(self, e: Optional[ft.ControlEvent] = None) -> None:
        self.set_expanded(not self.is_expanded)

    def _toggle_expanded(self, e: Optional[ft.ControlEvent] = None) -> None:
        self.toggle_expanded(e)

    def _render_items(self) -> None:
        self.top_items_column.controls.clear()
        self.bottom_items_column.controls.clear()

        is_ext = self.is_expanded
        self.top_items_column.horizontal_alignment = ft.CrossAxisAlignment.START if is_ext else ft.CrossAxisAlignment.CENTER
        self.bottom_items_column.horizontal_alignment = ft.CrossAxisAlignment.START if is_ext else ft.CrossAxisAlignment.CENTER

        # Render Top 4 Macro Items
        for item in self.macro_items:
            count = 0
            if item.key == "NAV_DOWNLOADS":
                # Badge counts active/in-progress downloads only (hidden when 0 in-progress)
                count = self.status_counts.get("ACTIVE", 0)
            elif item.key == "NAV_EXTENSIONS":
                count = self.extensions_count

            ctrl = self._build_rail_item(item, count, is_ext=is_ext)
            self.top_items_column.controls.append(ctrl)

        # Render Bottom: Settings Button
        settings_btn = self._build_settings_item(is_ext=is_ext)

        self.bottom_items_column.controls.extend([
            ft.Divider(color=BORDER_COLOR, height=1),
            settings_btn,
        ])

    def _is_item_active(self, item_key: str) -> bool:
        if self.active_key == item_key:
            return True
        if item_key == "NAV_DOWNLOADS" and (
            self.active_key in ("STATUS_ALL", "STATUS_ACTIVE", "STATUS_COMPLETED", "STATUS_PAUSED", "STATUS_FAILED")
            or self.active_key.startswith("CAT_")
        ):
            return True
        if item_key in ("NAV_MONITOR", "TOOL_MONITOR") and self.active_key in ("NAV_MONITOR", "TOOL_MONITOR"):
            return True
        if item_key in ("NAV_GRABBER", "TOOL_GRABBER") and self.active_key in ("NAV_GRABBER", "TOOL_GRABBER"):
            return True
        if item_key in ("NAV_EXTENSIONS", "TOOL_EXTENSIONS") and self.active_key in ("NAV_EXTENSIONS", "TOOL_EXTENSIONS"):
            return True
        return False

    def _build_settings_item(self, is_ext: bool) -> ft.Control:
        if not is_ext:
            return ft.Container(
                width=48,
                height=40,
                border_radius=6,
                alignment=ft.Alignment(0, 0),
                content=ft.IconButton(
                    icon=ft.Icons.SETTINGS_OUTLINED,
                    icon_size=18,
                    icon_color=TEXT_SECONDARY,
                    tooltip="Impostazioni Getly",
                    on_click=self._handle_settings_click,
                ),
            )

        return ft.Container(
            width=192,
            height=40,
            border_radius=6,
            on_click=self._handle_settings_click,
            content=ft.Row(
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(width=3, height=18, bgcolor="transparent"),
                    ft.Container(
                        width=40,
                        height=36,
                        alignment=ft.Alignment(0, 0),
                        content=ft.IconButton(
                            icon=ft.Icons.SETTINGS_OUTLINED,
                            icon_size=18,
                            icon_color=TEXT_SECONDARY,
                            tooltip="Impostazioni Getly",
                            on_click=self._handle_settings_click,
                        ),
                    ),
                    ft.Text(
                        "Impostazioni",
                        size=13,
                        color=TEXT_SECONDARY,
                        weight=ft.FontWeight.W_500,
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ],
            ),
        )

    def _build_rail_item(self, item: SidebarItemData, count: int, is_ext: bool = False) -> ft.Control:
        is_active = self._is_item_active(item.key)

        # WinUI 3 left vertical accent strip (3px wide, rounded)
        indicator = ft.Container(
            width=3,
            height=18,
            border_radius=1.5,
            bgcolor=ACCENT_BLUE if is_active else "transparent",
        )

        icon_color = ACCENT_LIGHT if is_active else TEXT_SECONDARY
        tooltip_str = item.label + (f" ({count})" if count > 0 else "")

        # Use native ft.IconButton for zero latency click response
        btn = ft.IconButton(
            icon=item.icon,
            icon_size=20,
            icon_color=icon_color,
            tooltip=tooltip_str,
            on_click=lambda e, k=item.key: self._handle_item_click(k),
        )

        stack_controls = [btn]
        badge_str = str(count) if count < 100 else "99+"

        # Circular badge on icon in compact mode
        if not is_ext and count > 0 and item.key in ("NAV_DOWNLOADS", "NAV_EXTENSIONS"):
            stack_controls.append(
                ft.Container(
                    right=0,
                    top=0,
                    bgcolor=ACCENT_BLUE_WINUI,
                    border_radius=8,
                    padding=ft.Padding(left=4, right=4, top=1, bottom=1),
                    content=ft.Text(badge_str, size=8, color="#FFFFFF", weight=ft.FontWeight.BOLD),
                )
            )

        icon_stack = ft.Stack(
            controls=stack_controls,
            width=40,
            height=36,
            alignment=ft.Alignment(0, 0),
        )

        row_controls = [
            indicator,
            icon_stack if is_ext else ft.Container(
                expand=True,
                alignment=ft.Alignment(0, 0),
                content=icon_stack,
            ),
        ]

        if is_ext:
            label_text = ft.Text(
                item.label,
                size=13,
                color=TEXT_PRIMARY if is_active else TEXT_SECONDARY,
                weight=ft.FontWeight.W_500 if is_active else ft.FontWeight.NORMAL,
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            )
            row_controls.append(label_text)
            if count > 0 and item.key in ("NAV_DOWNLOADS", "NAV_EXTENSIONS"):
                row_controls.append(
                    ft.Container(
                        margin=ft.Margin(right=8),
                        bgcolor=ACCENT_BLUE_WINUI,
                        border_radius=8,
                        padding=ft.Padding(left=5, right=5, top=1, bottom=1),
                        content=ft.Text(badge_str, size=9, color="#FFFFFF", weight=ft.FontWeight.BOLD),
                    )
                )

        item_row = ft.Row(
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.START,
            controls=row_controls,
        )

        bg_color = "rgba(255, 255, 255, 0.08)" if is_active else "transparent"

        item_container = ft.Container(
            width=192 if is_ext else 48,
            height=40,
            border_radius=6,
            bgcolor=bg_color,
            content=item_row,
            on_click=lambda e, k=item.key: self._handle_item_click(k),
        )

        return item_container

    def _handle_item_click(self, key: str) -> None:
        self.set_active_key(key)

        # Normalize key for app navigation
        if key in ("NAV_DOWNLOADS", "STATUS_ALL"):
            self.on_navigate("STATUS_ALL", "ALL", None)
            return
        elif key in ("NAV_MONITOR", "TOOL_MONITOR"):
            self.on_navigate("TOOL_MONITOR", None, None)
            return
        elif key in ("NAV_GRABBER", "TOOL_GRABBER"):
            self.on_navigate("TOOL_GRABBER", None, None)
            return
        elif key in ("NAV_EXTENSIONS", "TOOL_EXTENSIONS"):
            self.on_navigate("TOOL_EXTENSIONS", None, None)
            return

        all_items = self.macro_items + self.status_items + self.category_items + self.tool_items
        selected = next((it for it in all_items if it.key == key), None)
        if selected:
            self.on_navigate(selected.key, selected.status, selected.category)

    def set_active_key(self, key: str) -> None:
        self.active_key = key
        self._render_items()
        safe_update(self)

    def update_counts(
        self,
        status_counts: Dict[str, int],
        category_counts: Dict[str, int],
        extensions_count: Optional[int] = None,
    ) -> None:
        if extensions_count is not None:
            self.extensions_count = extensions_count
        self.status_counts.update(status_counts)
        self.category_counts.update(category_counts)
        self._render_items()
        safe_update(self)

    def update_footer_states(
        self,
        grabber_enabled: bool,
        ask_save_enabled: bool,
        autostart_enabled: Optional[bool] = None,
        vpn_shield_enabled: Optional[bool] = None,
    ) -> None:
        self.grabber_enabled = grabber_enabled
        self.ask_save_enabled = ask_save_enabled
        if autostart_enabled is not None:
            self.autostart_enabled = autostart_enabled
        if vpn_shield_enabled is not None:
            self.vpn_shield_enabled = vpn_shield_enabled

    def _handle_toggle_theme(self) -> None:
        """Theme toggle removed - Dark theme is now fixed WinUI 3 Fluent."""
        if self.on_toggle_theme:
            self.on_toggle_theme()

    def _handle_settings_click(self, e: Optional[ft.ControlEvent] = None) -> None:
        """Opens WinUI 3 Settings Dialog modal."""
        dialog = SettingsDialog(
            cnl_port=self.cnl_port,
            grabber_enabled=self.grabber_enabled,
            ask_save_enabled=self.ask_save_enabled,
            autostart_enabled=self.autostart_enabled,
            vpn_shield_enabled=self.vpn_shield_enabled,
            on_toggle_grabber=self.on_toggle_grabber,
            on_toggle_ask_save=self.on_toggle_ask_save,
            on_toggle_autostart=self.on_toggle_autostart,
            on_toggle_vpn_shield=self.on_toggle_vpn_shield,
        )
        if self.page:
            if hasattr(self.page, "show_dialog"):
                self.page.show_dialog(dialog)
            elif hasattr(self.page, "open"):
                self.page.open(dialog)
            else:
                self.page.dialog = dialog
                dialog.open = True
                safe_update(self.page)
