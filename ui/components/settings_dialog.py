import flet as ft
from typing import Callable, Optional

from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
)


class SettingsDialog(ft.AlertDialog):
    """
    WinUI 3 Fluent Dark Settings Dialog for Getly:
    - CNL2 Server status (:9666)
    - Link Grabber toggle (Clipboard interceptor)
    - Ask Save Directory toggle (IDM-style confirm prompt)
    - Autostart with Windows toggle (--minimized)
    - VPN Kill-Switch toggle (Strict privacy protection)
    """

    def __init__(
        self,
        cnl_port: int = 9666,
        grabber_enabled: bool = True,
        ask_save_enabled: bool = True,
        autostart_enabled: bool = False,
        vpn_shield_enabled: bool = True,
        on_toggle_grabber: Optional[Callable[[], None]] = None,
        on_toggle_ask_save: Optional[Callable[[], None]] = None,
        on_toggle_autostart: Optional[Callable[[], None]] = None,
        on_toggle_vpn_shield: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.cnl_port = cnl_port
        self.grabber_enabled = grabber_enabled
        self.ask_save_enabled = ask_save_enabled
        self.autostart_enabled = autostart_enabled
        self.vpn_shield_enabled = vpn_shield_enabled
        self.on_toggle_grabber = on_toggle_grabber
        self.on_toggle_ask_save = on_toggle_ask_save
        self.on_toggle_autostart = on_toggle_autostart
        self.on_toggle_vpn_shield = on_toggle_vpn_shield

        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        self.title = ft.Row(
            spacing=8,
            controls=[
                ft.Icon(ft.Icons.SETTINGS_ROUNDED, size=20, color=ACCENT_BLUE),
                ft.Text("Impostazioni Getly", size=16, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ],
        )

        # 1. CNL2 Status card
        cnl_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        controls=[
                            ft.Icon(ft.Icons.LOCK_OUTLINE_ROUNDED, size=18, color="#10B981"),
                            ft.Column(
                                spacing=1,
                                controls=[
                                    ft.Text("Server Click'n'Load 2", size=12, weight=ft.FontWeight.W_500, color=TEXT_PRIMARY),
                                    ft.Text(f"In ascolto su porta locale 127.0.0.1:{self.cnl_port}", size=10, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    ft.Container(
                        bgcolor="rgba(16, 185, 129, 0.15)",
                        border=ft.Border.all(1, "rgba(16, 185, 129, 0.35)"),
                        border_radius=12,
                        padding=ft.Padding(8, 2, 8, 2),
                        content=ft.Text("Attivo", size=10, color="#10B981", weight=ft.FontWeight.BOLD),
                    ),
                ],
            ),
        )

        # 2. Link Grabber switch
        self.grabber_switch = ft.Switch(
            value=self.grabber_enabled,
            active_color=ACCENT_BLUE,
            on_change=self._handle_grabber_toggle,
        )
        grabber_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        expand=True,
                        controls=[
                            ft.Icon(ft.Icons.CONTENT_PASTE_SEARCH_ROUNDED, size=18, color="#60CDFF"),
                            ft.Column(
                                spacing=1,
                                expand=True,
                                controls=[
                                    ft.Text("Cattura Link dagli Appunti", size=12, weight=ft.FontWeight.W_500, color=TEXT_PRIMARY),
                                    ft.Text("Rileva automaticamente URL e link magnetici copiati", size=10, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    self.grabber_switch,
                ],
            ),
        )

        # 3. Ask save directory switch
        self.ask_save_switch = ft.Switch(
            value=self.ask_save_enabled,
            active_color=ACCENT_BLUE,
            on_change=self._handle_ask_save_toggle,
        )
        ask_save_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        expand=True,
                        controls=[
                            ft.Icon(ft.Icons.FOLDER_SPECIAL_ROUNDED, size=18, color="#F59E0B"),
                            ft.Column(
                                spacing=1,
                                expand=True,
                                controls=[
                                    ft.Text("Chiedi sempre dove salvare", size=12, weight=ft.FontWeight.W_500, color=TEXT_PRIMARY),
                                    ft.Text("Mostra finestra di conferma prima di avviare il download", size=10, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    self.ask_save_switch,
                ],
            ),
        )

        # 4. Autostart with PC switch
        self.autostart_switch = ft.Switch(
            value=self.autostart_enabled,
            active_color=ACCENT_BLUE,
            on_change=self._handle_autostart_toggle,
        )
        autostart_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        expand=True,
                        controls=[
                            ft.Icon(ft.Icons.POWER_SETTINGS_NEW_ROUNDED, size=18, color="#38BDF8"),
                            ft.Column(
                                spacing=1,
                                expand=True,
                                controls=[
                                    ft.Text("Avvio automatico con Windows", size=12, weight=ft.FontWeight.W_500, color=TEXT_PRIMARY),
                                    ft.Text("Avvia Getly all'accesso utente nella System Tray (--minimized)", size=10, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    self.autostart_switch,
                ],
            ),
        )

        # 5. VPN Kill-Switch
        self.vpn_shield_switch = ft.Switch(
            value=self.vpn_shield_enabled,
            active_color=ACCENT_BLUE,
            on_change=self._handle_vpn_shield_toggle,
        )
        vpn_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        expand=True,
                        controls=[
                            ft.Icon(ft.Icons.SHIELD_ROUNDED, size=18, color="#10B981"),
                            ft.Column(
                                spacing=1,
                                expand=True,
                                controls=[
                                    ft.Text("VPN Kill-Switch & Privacy Shield", size=12, weight=ft.FontWeight.W_500, color=TEXT_PRIMARY),
                                    ft.Text("Blocca rigorosamente il traffico se l'interfaccia VPN cade", size=10, color=TEXT_MUTED),
                                ],
                            ),
                        ],
                    ),
                    self.vpn_shield_switch,
                ],
            ),
        )

        self.content = ft.Container(
            width=430,
            content=ft.Column(
                tight=True,
                spacing=8,
                controls=[
                    cnl_card,
                    grabber_card,
                    ask_save_card,
                    autostart_card,
                    vpn_card,
                ],
            ),
        )

        self.actions = [
            ft.TextButton(
                "Chiudi",
                on_click=self._close,
                style=ft.ButtonStyle(color=TEXT_PRIMARY),
            ),
        ]
        self.actions_alignment = ft.MainAxisAlignment.END

    def _handle_grabber_toggle(self, e: Optional[ft.ControlEvent] = None) -> None:
        if e and hasattr(e, "control") and e.control:
            self.grabber_enabled = bool(e.control.value)
        elif hasattr(self, "grabber_switch") and self.grabber_switch:
            self.grabber_enabled = bool(self.grabber_switch.value)
        if self.on_toggle_grabber:
            self.on_toggle_grabber()

    def _handle_ask_save_toggle(self, e: Optional[ft.ControlEvent] = None) -> None:
        if e and hasattr(e, "control") and e.control:
            self.ask_save_enabled = bool(e.control.value)
        elif hasattr(self, "ask_save_switch") and self.ask_save_switch:
            self.ask_save_enabled = bool(self.ask_save_switch.value)
        if self.on_toggle_ask_save:
            self.on_toggle_ask_save()

    def _handle_autostart_toggle(self, e: Optional[ft.ControlEvent] = None) -> None:
        if e and hasattr(e, "control") and e.control:
            self.autostart_enabled = bool(e.control.value)
        elif hasattr(self, "autostart_switch") and self.autostart_switch:
            self.autostart_enabled = bool(self.autostart_switch.value)
        if self.on_toggle_autostart:
            self.on_toggle_autostart()

    def _handle_vpn_shield_toggle(self, e: Optional[ft.ControlEvent] = None) -> None:
        if e and hasattr(e, "control") and e.control:
            self.vpn_shield_enabled = bool(e.control.value)
        elif hasattr(self, "vpn_shield_switch") and self.vpn_shield_switch:
            self.vpn_shield_enabled = bool(self.vpn_shield_switch.value)
        if self.on_toggle_vpn_shield:
            self.on_toggle_vpn_shield()

    def _close(self, e: Optional[ft.ControlEvent] = None) -> None:
        if self.page:
            self.open = False
            self.page.update()
