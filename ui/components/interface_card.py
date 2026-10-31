from typing import Callable, Optional
import flet as ft

from core.network.models import NetworkAdapter
from ui.coordinator import UICoordinator, format_speed
from ui.theme import (
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    GLASS_CARD_SHADOW,
    build_gradient_progress_bar,
    safe_update,
)


class InterfaceCard(ft.Container):
    """
    Card widget for the Net-Merge Live Monitor displaying a network adapter,
    its real-time throughput, percentage contribution to bandwidth aggregation,
    and a toggle switch to enable/disable the adapter from the active socket pool.
    """

    def __init__(
        self,
        adapter: NetworkAdapter,
        coordinator: UICoordinator,
        on_toggle: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.adapter = adapter
        self.coordinator = coordinator
        self.on_toggle = on_toggle

        self.bgcolor = SURFACE_CARD
        self.border_radius = 20
        self.padding = ft.Padding(left=20, right=20, top=16, bottom=16)
        self.border = ft.Border.all(1, BORDER_COLOR)
        self.shadow = GLASS_CARD_SHADOW
        self.margin = ft.Margin(bottom=10)

        self.content = self._build_card()

    def _build_card(self) -> ft.Column:
        ad = self.adapter
        name_lower = (ad.name + (ad.friendly_name or "")).lower()
        is_wifi = "wi-fi" in name_lower or "wlan" in name_lower or "wireless" in name_lower
        icon_name = ft.Icons.WIFI_ROUNDED if is_wifi else ft.Icons.SETTINGS_ETHERNET_ROUNDED

        is_enabled = self.coordinator.is_adapter_enabled(ad.name)
        spd = self.coordinator.interface_speeds.get(ad.name, 0.0)
        pct = self.coordinator.interface_percentages.get(ad.name, 0.0)

        badge_text = "PRIMARIA" if ad.is_primary else "SECONDARIA"
        badge_color = "#10B981" if ad.is_primary else ACCENT_LIGHT
        badge_bg = "rgba(16, 185, 129, 0.15)" if ad.is_primary else "rgba(0, 120, 212, 0.15)"
        badge_border = "rgba(16, 185, 129, 0.35)" if ad.is_primary else "rgba(0, 120, 212, 0.35)"

        title_text = ad.friendly_name or ad.name
        details_str = f"IPv4: {ad.ip}  •  IfIndex: {ad.if_index}  •  Gateway: {ad.gateway or 'Nessuno'}"

        switch_control = ft.Switch(
            value=is_enabled,
            active_color=ACCENT_BLUE,
            tooltip="Includi/Escludi dal pool di download",
            on_change=self._handle_switch_changed,
        )

        icon_box = ft.Container(
            width=40,
            height=40,
            border_radius=20,
            bgcolor="rgba(0, 120, 212, 0.15)" if is_enabled else "rgba(255, 255, 255, 0.05)",
            border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)" if is_enabled else BORDER_COLOR),
            alignment=ft.Alignment(0, 0),
            content=ft.Icon(icon_name, color=ACCENT_LIGHT if is_enabled else TEXT_MUTED, size=20),
        )

        header_row = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=12,
                    controls=[
                        icon_box,
                        ft.Column(
                            spacing=3,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    controls=[
                                        ft.Text(
                                            title_text,
                                            weight=ft.FontWeight.BOLD,
                                            size=14,
                                            color=TEXT_PRIMARY if is_enabled else TEXT_MUTED,
                                        ),
                                        ft.Container(
                                            bgcolor=badge_bg,
                                            padding=ft.Padding(left=8, right=8, top=2, bottom=2),
                                            border_radius=6,
                                            border=ft.Border.all(1, badge_border),
                                            content=ft.Text(
                                                badge_text,
                                                color=badge_color,
                                                size=10,
                                                weight=ft.FontWeight.BOLD,
                                            ),
                                        ),
                                    ],
                                ),
                                ft.Text(details_str, size=11, color=TEXT_SECONDARY),
                            ],
                        ),
                    ],
                ),
                switch_control,
            ],
        )

        # 2. Throughput Gauge Bar
        progress_val = min(1.0, max(0.0, pct / 100.0)) if is_enabled else 0.0
        gauge_bar = build_gradient_progress_bar(progress_val, "DOWNLOADING" if is_enabled else "PAUSED")

        # 3. Metrics Row
        if not is_enabled:
            speed_label = "Interfaccia disabilitata manualmente dal pool"
            pct_label = "-- %"
            label_color = TEXT_MUTED
        else:
            speed_label = f"Throughput: {format_speed(spd)}"
            pct_label = f"Contributo aggregato: {pct:.1f}% della banda"
            label_color = TEXT_SECONDARY

        metrics_row = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Text(speed_label, size=12, color=label_color, weight=ft.FontWeight.W_500),
                ft.Text(pct_label, size=12, color=label_color),
            ],
        )

        return ft.Column(
            spacing=12,
            controls=[
                header_row,
                gauge_bar,
                metrics_row,
            ],
        )

    def update_data(self) -> None:
        """Refreshes the card content."""
        self.content = self._build_card()

    def update_live_metrics(self) -> None:
        """Surgically refreshes live throughput metrics without page reload."""
        self.update_data()
        safe_update(self)

    def _handle_switch_changed(self, e: ft.ControlEvent) -> None:
        new_val = bool(e.control.value)
        success = self.coordinator.toggle_adapter(self.adapter.name, new_val)
        if not success:
            e.control.value = not new_val
            if e.page:
                e.page.update()
        if self.on_toggle:
            self.on_toggle()
