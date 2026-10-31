import asyncio
from typing import Callable, Optional
import flet as ft

from core.scheduler.task import DownloadTask, DownloadStatus
from ui.coordinator import UICoordinator, format_bytes, format_speed, format_eta
from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    build_gradient_progress_bar,
    safe_update,
)


class OperationsDrawer(ft.Container):
    """
    Windows 11 Fluent Dark Bottom Operations Drawer:
    - Collapsible panel with header '▲/▼ X di Y operazioni completate'
    - Terminal-style live status for each active download
    - Live speed, ETA, progress bar, Annulla and option actions
    - Performs surgical self-updates (self.update()) without touching page or sidebar
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_action: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_action = on_action
        self.is_expanded = False

        self.bgcolor = BG_PANEL
        self.border = ft.Border(top=ft.BorderSide(1, BORDER_COLOR))
        self.padding = ft.Padding(left=16, right=16, top=6, bottom=6)
        self.animate = ft.Animation(150, ft.AnimationCurve.EASE_OUT)

        # Header controls
        self.chevron_icon = ft.Icon(
            ft.Icons.KEYBOARD_ARROW_UP_ROUNDED,
            size=18,
            color=ACCENT_LIGHT,
        )
        self.header_text = ft.Text(
            "0 di 0 operazioni completate",
            size=11,
            weight=ft.FontWeight.W_500,
            color=TEXT_PRIMARY,
        )
        self.speed_summary_text = ft.Text(
            "",
            size=11,
            weight=ft.FontWeight.BOLD,
            color="#10B981",
        )
        self.mini_progress = ft.ProgressBar(
            value=0.0,
            width=100,
            height=3,
            color=ACCENT_BLUE,
            bgcolor="rgba(255, 255, 255, 0.10)",
        )

        self.header_row = ft.Container(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.chevron_icon,
                            self.header_text,
                            self.mini_progress,
                        ],
                    ),
                    self.speed_summary_text,
                ],
            ),
            on_click=self._toggle_expanded,
            ink=True,
            padding=ft.Padding(0, 2, 0, 2),
        )

        # Drawer items list
        self.items_list = ft.ListView(
            spacing=6,
            height=120,
            padding=ft.Padding(top=6, bottom=4),
        )

        self.body_container = ft.Container(
            content=self.items_list,
            visible=False,
        )

        self.content = ft.Column(
            tight=True,
            spacing=4,
            controls=[
                self.header_row,
                self.body_container,
            ],
        )

    def _toggle_expanded(self, e: Optional[ft.ControlEvent] = None) -> None:
        self.is_expanded = not self.is_expanded
        self.chevron_icon.icon = (
            ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            if self.is_expanded
            else ft.Icons.KEYBOARD_ARROW_UP_ROUNDED
        )
        self.body_container.visible = self.is_expanded
        safe_update(self)

    def update_tasks(self) -> None:
        """Refreshes live operations drawer state and active tasks surgically."""
        all_tasks = self.coordinator.get_tasks("ALL")
        active_tasks = [t for t in all_tasks if t.status == DownloadStatus.DOWNLOADING]
        completed_tasks = [t for t in all_tasks if t.status == DownloadStatus.COMPLETED]
        total_count = len(all_tasks)
        completed_count = len(completed_tasks)

        self.header_text.value = (
            f"▼ {completed_count} di {total_count} operazioni completate"
            if self.is_expanded
            else f"▲ {completed_count} di {total_count} operazioni completate"
        )
        if total_count > 0:
            self.mini_progress.value = completed_count / total_count
        else:
            self.mini_progress.value = 0.0

        total_speed = self.coordinator.total_aggregate_speed
        if total_speed > 0:
            self.speed_summary_text.value = f"Totale: {format_speed(total_speed)}"
        else:
            self.speed_summary_text.value = ""

        # Update body items
        self.items_list.controls.clear()
        if not active_tasks:
            self.items_list.controls.append(
                ft.Container(
                    alignment=ft.Alignment(0, 0),
                    padding=15,
                    content=ft.Text(
                        "Nessun download attualmente in corso.",
                        size=11,
                        color=TEXT_MUTED,
                    ),
                )
            )
        else:
            for task in active_tasks:
                self.items_list.controls.append(self._build_task_row(task))

    def update_live_metrics(self) -> None:
        """Surgical update called by tick coordinator."""
        self.update_tasks()
        safe_update(self)

    def _build_task_row(self, task: DownloadTask) -> ft.Container:
        ratio = task.progress_ratio
        pct_text = f"{int(ratio * 100)}%"
        speed = self.coordinator.task_speeds.get(task.task_id, task.download_speed_bps)
        speed_str = format_speed(speed)
        eta_str = self.coordinator.task_etas.get(task.task_id, "--:--")

        fn = task.filename or "Download"

        cancel_btn = ft.TextButton(
            content="Annulla",
            style=ft.ButtonStyle(
                color="#EF4444",
                padding=ft.Padding(6, 2, 6, 2),
            ),
            on_click=lambda e, tid=task.task_id: self._handle_cancel_task(tid),
        )

        more_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_HORIZ_ROUNDED,
            icon_size=14,
            icon_color=TEXT_MUTED,
            tooltip="Opzioni",
            items=[
                ft.PopupMenuItem(
                    content="Metti in Pausa",
                    icon=ft.Icons.PAUSE_ROUNDED,
                    on_click=lambda e, tid=task.task_id: self._handle_pause_task(tid),
                ),
                ft.PopupMenuItem(
                    content="Apri Cartella",
                    icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                    on_click=lambda e, path=task.destination_dir: self.coordinator.open_folder(path),
                ),
            ],
        )

        row = ft.Container(
            bgcolor=SURFACE_CARD,
            border_radius=6,
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding(8, 5, 8, 5),
            content=ft.Column(
                tight=True,
                spacing=3,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=6,
                                controls=[
                                    ft.Icon(ft.Icons.TERMINAL_ROUNDED, size=14, color=ACCENT_LIGHT),
                                    ft.Text(fn, size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, width=280),
                                    ft.Text(f"{speed_str} • ETA {eta_str}", size=10, color="#10B981"),
                                ],
                            ),
                            ft.Row(
                                spacing=4,
                                controls=[
                                    ft.Text(pct_text, size=10, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
                                    cancel_btn,
                                    more_btn,
                                ],
                            ),
                        ],
                    ),
                    build_gradient_progress_bar(ratio, "DOWNLOADING"),
                ],
            ),
        )
        return row

    def _handle_cancel_task(self, task_id: str) -> None:
        async def _cancel():
            await self.coordinator.cancel_download(task_id)
            self.update_tasks()
            safe_update(self)
            if self.on_action:
                self.on_action()

        if self.page:
            self.page.run_task(_cancel)

    def _handle_pause_task(self, task_id: str) -> None:
        async def _pause():
            await self.coordinator.pause_download(task_id)
            self.update_tasks()
            safe_update(self)
            if self.on_action:
                self.on_action()

        if self.page:
            self.page.run_task(_pause)
