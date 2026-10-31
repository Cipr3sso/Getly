from typing import Optional
import flet as ft

from ui.coordinator import UICoordinator, format_speed
from ui.components.interface_card import InterfaceCard
from ui.theme import (
    BG_WINDOW,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    ACCENT_BLUE_WINUI,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    GLASS_CARD_SHADOW,
    safe_update,
)


class MonitorView(ft.Container):
    """
    Net-Merge Live Monitor view providing real-time throughput metrics
    per network interface and controls to include/exclude adapters on the fly.
    Windows 11 Fluent Dark theme.
    """

    def __init__(self, coordinator: UICoordinator):
        super().__init__()
        self.coordinator = coordinator
        self.expand = True
        self.bgcolor = BG_WINDOW
        self.padding = ft.Padding(left=20, right=20, top=16, bottom=16)

        self.cards_column = ft.Column(spacing=10)
        self.speed_text: Optional[ft.Text] = None
        self.content = self._build_layout()
        self.refresh_adapters()

    def _build_layout(self) -> ft.Column:
        total_speed_str = format_speed(self.coordinator.total_aggregate_speed)
        active_count = len(self.coordinator.session_pool.active_adapters)
        total_count = len(self.coordinator.session_pool.all_adapters)

        # 1. Summary KPI Header Card
        summary_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border_radius=12,
            padding=20,
            border=ft.Border.all(1, BORDER_COLOR),
            shadow=GLASS_CARD_SHADOW,
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=16,
                        controls=[
                            ft.Container(
                                bgcolor="rgba(0, 120, 212, 0.15)",
                                border_radius=12,
                                border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                                padding=12,
                                content=ft.Icon(ft.Icons.SPEED_ROUNDED, size=28, color=ACCENT_LIGHT),
                            ),
                            ft.Column(
                                spacing=4,
                                controls=[
                                    ft.Text(
                                        "BANDA AGGREGATA TOTALE (NET-MERGE)",
                                        size=11,
                                        color=ACCENT_LIGHT,
                                        weight=ft.FontWeight.BOLD,
                                    ),
                                    self._get_speed_text(total_speed_str),
                                    ft.Text(
                                        "Aggregazione multi-socket su Windows Winsock IP_UNICASTIF",
                                        size=11,
                                        color=TEXT_SECONDARY,
                                    ),
                                ],
                            ),
                        ],
                    ),
                    ft.Container(
                        bgcolor="rgba(255, 255, 255, 0.04)",
                        padding=ft.Padding(left=18, right=18, top=10, bottom=10),
                        border_radius=8,
                        border=ft.Border.all(1, BORDER_COLOR),
                        content=ft.Column(
                            horizontal_alignment=ft.CrossAxisAlignment.END,
                            spacing=2,
                            controls=[
                                ft.Text("SCHEDE ATTIVE", size=10, color=TEXT_SECONDARY, weight=ft.FontWeight.BOLD),
                                ft.Text(f"{active_count} di {total_count}", size=18, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
                                ft.Row(
                                    spacing=4,
                                    controls=[
                                        ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=12, color="#10B981"),
                                        ft.Text("Online", size=11, color="#10B981"),
                                    ],
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )

        return ft.Column(
            expand=True,
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            controls=[
                summary_card,
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text("SCHEDE DI RETE DISPONIBILI", size=13, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                        ft.Text("Usa gli switch per escludere o includere temporaneamente le interfacce", size=11, color=TEXT_MUTED),
                    ],
                ),
                self.cards_column,
            ],
        )

    def _get_speed_text(self, speed_str: str) -> ft.Text:
        self.speed_text = ft.Text(
            speed_str,
            size=28,
            color=TEXT_PRIMARY,
            weight=ft.FontWeight.BOLD,
        )
        return self.speed_text

    def refresh_adapters(self) -> None:
        """Rebuilds cards for all adapters and updates speeds."""
        adapters = self.coordinator.get_adapters()
        self.cards_column.controls.clear()

        for adapter in adapters:
            card = InterfaceCard(
                adapter=adapter,
                coordinator=self.coordinator,
                on_toggle=self.refresh_adapters,
            )
            self.cards_column.controls.append(card)

        if self.speed_text:
            self.speed_text.value = format_speed(self.coordinator.total_aggregate_speed)

    def update_live_metrics(self) -> None:
        """Surgically refreshes live throughput metrics without full rebuild."""
        if self.speed_text:
            self.speed_text.value = format_speed(self.coordinator.total_aggregate_speed)
            safe_update(self.speed_text)
        for card in self.cards_column.controls:
            if isinstance(card, InterfaceCard):
                card.update_live_metrics()
