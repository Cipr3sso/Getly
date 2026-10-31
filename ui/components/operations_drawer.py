import asyncio
import logging
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
    COLOR_SUCCESS,
    COLOR_ERROR,
    build_gradient_progress_bar,
    safe_update,
)

logger = logging.getLogger(__name__)


class OperationsDrawer(ft.Container):
    """
    Windows 11 Fluent Dark Bottom Operations Drawer & Evolved Status Bar:
    - Left: Icon(DOWNLOAD_DONE) + 'X di Y completati' + mini horizontal progress bar
    - Center: Live aggregate throughput '⬇️ DL: X MB/s   ⬆️ UL: Y KB/s'
    - Right: Speed Limiter popup '⚡ Illimitata ▾' + '🌙 Spegni PC al termine' auto-shutdown
    - Collapsible terminal-style drawer with live per-task details
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
        self.padding = ft.Padding(left=12, right=12, top=4, bottom=4)
        self.animate = ft.Animation(150, ft.AnimationCurve.EASE_OUT)

        # Auto-shutdown tracking
        self._had_active_downloads = False
        self._shutdown_triggered = False
        self._shutdown_cancelled = False
        self._shutdown_countdown_dialog: Optional[ft.AlertDialog] = None
        self._shutdown_countdown_task: Optional[asyncio.Task] = None

        # --- 1. LEFT ZONE CONTROLS ---
        self.done_icon = ft.Icon(
            ft.Icons.DOWNLOAD_DONE_ROUNDED,
            size=16,
            color=ACCENT_LIGHT,
        )
        self.chevron_icon = ft.Icon(
            ft.Icons.KEYBOARD_ARROW_UP_ROUNDED,
            size=16,
            color=TEXT_MUTED,
        )
        self.header_text = ft.Text(
            "0 di 0 completati",
            size=11,
            weight=ft.FontWeight.W_500,
            color=TEXT_PRIMARY,
        )
        self.mini_progress = ft.ProgressBar(
            value=0.0,
            width=85,
            height=3,
            color=ACCENT_BLUE,
            bgcolor="rgba(255, 255, 255, 0.10)",
        )

        left_zone = ft.Container(
            content=ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self.chevron_icon,
                    self.done_icon,
                    self.header_text,
                    self.mini_progress,
                ],
            ),
            on_click=self._toggle_expanded,
            ink=True,
            padding=ft.Padding(4, 2, 4, 2),
            border_radius=4,
            tooltip="Espandi/Riduci dettagli operazioni",
        )

        # --- 2. CENTER ZONE CONTROLS ---
        self.speed_summary_text = ft.Text(
            "⬇️ DL: 0 B/s   ⬆️ UL: 0 B/s",
            size=11,
            weight=ft.FontWeight.BOLD,
            color="#10B981",
        )

        # --- 3. RIGHT ZONE CONTROLS ---
        current_limit = self.coordinator.get_download_limit()
        initial_label = "⚡ Illimitata" if current_limit == 0 else f"⚡ {format_speed(current_limit)}"
        self.limiter_label = ft.Text(
            initial_label,
            size=10,
            weight=ft.FontWeight.W_500,
            color=TEXT_SECONDARY,
        )
        self.limiter_menu = ft.PopupMenuButton(
            content=ft.Container(
                bgcolor=SURFACE_CARD,
                border_radius=4,
                border=ft.Border.all(1, BORDER_COLOR),
                padding=ft.Padding(6, 2, 6, 2),
                content=ft.Row(
                    spacing=4,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        self.limiter_label,
                        ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, size=15, color=TEXT_MUTED),
                    ],
                ),
            ),
            tooltip="Limitatore di banda download",
            items=[
                ft.PopupMenuItem(content="⚡ Illimitata", on_click=lambda e: self._set_speed_limit(0, "⚡ Illimitata")),
                ft.PopupMenuItem(content="1 MB/s", on_click=lambda e: self._set_speed_limit(1048576, "⚡ 1 MB/s")),
                ft.PopupMenuItem(content="2 MB/s", on_click=lambda e: self._set_speed_limit(2097152, "⚡ 2 MB/s")),
                ft.PopupMenuItem(content="5 MB/s", on_click=lambda e: self._set_speed_limit(5242880, "⚡ 5 MB/s")),
                ft.PopupMenuItem(content="10 MB/s", on_click=lambda e: self._set_speed_limit(10485760, "⚡ 10 MB/s")),
                ft.PopupMenuItem(content="Personalizzato...", on_click=self._prompt_custom_speed_limit),
            ],
        )

        self.auto_shutdown_cb = ft.Checkbox(
            label="🌙 Spegni PC al termine",
            label_style=ft.TextStyle(size=10, color=TEXT_SECONDARY),
            value=False,
            active_color=ACCENT_BLUE,
            on_change=self._on_auto_shutdown_toggle,
        )

        right_zone = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.limiter_menu,
                self.auto_shutdown_cb,
            ],
        )

        self.header_row = ft.Container(
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    left_zone,
                    self.speed_summary_text,
                    right_zone,
                ],
            ),
            padding=ft.Padding(0, 1, 0, 1),
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
            spacing=2,
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

    def _set_speed_limit(self, limit_bps: int, label: str) -> None:
        self.coordinator.set_download_limit(limit_bps)
        self.limiter_label.value = label
        safe_update(self.limiter_label)

    def _prompt_custom_speed_limit(self, e: ft.ControlEvent) -> None:
        val_input = ft.TextField(
            label="Velocità massima (MB/s)",
            value="3.0",
            text_size=11,
            dense=True,
            color=TEXT_PRIMARY,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            bgcolor=SURFACE_CARD,
        )
        dialog = ft.AlertDialog(
            bgcolor=BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=10),
            title=ft.Row([
                ft.Icon(ft.Icons.SPEED_ROUNDED, color=ACCENT_LIGHT, size=20),
                ft.Text("Limite Velocità Personalizzato", size=15, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
            ], spacing=8),
            content=ft.Container(
                width=340,
                content=ft.Column([
                    ft.Text("Inserisci il throughput massimo combinato per tutti i download:", size=11, color=TEXT_SECONDARY),
                    val_input,
                ], tight=True, spacing=10),
            ),
            actions=[
                ft.TextButton("Annulla", on_click=lambda e: self._close_dialog(dialog)),
                ft.FilledButton(
                    "Applica",
                    style=ft.ButtonStyle(bgcolor=ACCENT_BLUE, color="#FFFFFF"),
                    on_click=lambda e: self._apply_custom_limit(val_input.value, dialog),
                ),
            ],
        )
        self._show_dialog(dialog)

    def _apply_custom_limit(self, val_str: str, dialog: ft.AlertDialog) -> None:
        try:
            mb = float(val_str.strip().replace(",", "."))
            bps = int(mb * 1024 * 1024)
            self._set_speed_limit(bps, f"⚡ {mb:.1f} MB/s")
        except Exception:
            pass
        self._close_dialog(dialog)

    def _on_auto_shutdown_toggle(self, e: ft.ControlEvent) -> None:
        if not self.auto_shutdown_cb.value:
            self._cancel_auto_shutdown()

    def update_tasks(self) -> None:
        """Refreshes live operations drawer state, speeds, and auto-shutdown check."""
        all_tasks = self.coordinator.get_tasks("ALL")
        active_tasks = [t for t in all_tasks if t.status == DownloadStatus.DOWNLOADING]
        completed_tasks = [t for t in all_tasks if t.status == DownloadStatus.COMPLETED]
        total_count = len(all_tasks)
        completed_count = len(completed_tasks)

        self.header_text.value = f"{completed_count} di {total_count} operazioni completate"
        if total_count > 0:
            self.mini_progress.value = completed_count / total_count
        else:
            self.mini_progress.value = 0.0

        total_dl = self.coordinator.total_aggregate_speed
        total_ul = sum(getattr(t, "upload_speed_bps", 0.0) for t in active_tasks)
        self.speed_summary_text.value = f"⬇️ DL: {format_speed(total_dl)}   ⬆️ UL: {format_speed(total_ul)}"

        # Track active downloads for auto-shutdown trigger
        if len(active_tasks) > 0:
            self._had_active_downloads = True

        # Check for auto-shutdown completion condition
        if (
            self.auto_shutdown_cb.value
            and self._had_active_downloads
            and len(active_tasks) == 0
            and completed_count > 0
            and not self._shutdown_triggered
        ):
            self._had_active_downloads = False
            self._trigger_auto_shutdown()

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

    def _trigger_auto_shutdown(self) -> None:
        """Triggers 60-second countdown alert dialog for PC auto-shutdown."""
        self._shutdown_triggered = True
        self._shutdown_cancelled = False

        countdown_text = ft.Text("Tutti i download sono completati. Spegnimento PC tra 60 secondi...", size=12, color=TEXT_PRIMARY)
        countdown_bar = ft.ProgressBar(value=1.0, color="#EF4444", bgcolor="rgba(255, 255, 255, 0.10)", height=6)

        dialog = ft.AlertDialog(
            bgcolor=BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=10),
            title=ft.Row([
                ft.Icon(ft.Icons.POWER_SETTINGS_NEW_ROUNDED, color="#EF4444", size=22),
                ft.Text("Spegnimento Automatico Computer", size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ], spacing=8),
            content=ft.Container(
                width=420,
                content=ft.Column([
                    countdown_text,
                    countdown_bar,
                ], tight=True, spacing=10),
            ),
            actions=[
                ft.FilledButton(
                    content="Annulla Spegnimento",
                    style=ft.ButtonStyle(bgcolor="#EF4444", color="#FFFFFF"),
                    on_click=lambda e: self._cancel_auto_shutdown(),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._shutdown_countdown_dialog = dialog
        self._show_dialog(dialog)

        async def _countdown():
            for remaining in range(60, 0, -1):
                if self._shutdown_cancelled:
                    break
                countdown_text.value = f"Tutti i download sono completati. Spegnimento PC tra {remaining} secondi..."
                countdown_bar.value = remaining / 60.0
                safe_update(countdown_text)
                safe_update(countdown_bar)
                await asyncio.sleep(1.0)

            if not self._shutdown_cancelled:
                from core.system.power import shutdown_computer
                logger.info("Auto-shutdown timer expired: executing shutdown_computer()")
                shutdown_computer()

        if self.page:
            self.page.run_task(_countdown)

    def _cancel_auto_shutdown(self) -> None:
        self._shutdown_cancelled = True
        self._shutdown_triggered = False
        self.auto_shutdown_cb.value = False
        safe_update(self.auto_shutdown_cb)
        from core.system.power import cancel_shutdown
        cancel_shutdown()
        if self._shutdown_countdown_dialog:
            self._close_dialog(self._shutdown_countdown_dialog)
            self._shutdown_countdown_dialog = None

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

    def _show_dialog(self, dialog: ft.AlertDialog) -> None:
        if not self.page:
            return
        if hasattr(self.page, "open"):
            self.page.open(dialog)
        elif hasattr(self.page, "show_dialog"):
            self.page.show_dialog(dialog)
        else:
            self.page.dialog = dialog
            dialog.open = True
            safe_update(self.page)

    def _close_dialog(self, dialog: ft.AlertDialog) -> None:
        if self.page:
            if hasattr(self.page, "close"):
                self.page.close(dialog)
            else:
                dialog.open = False
                safe_update(self.page)
