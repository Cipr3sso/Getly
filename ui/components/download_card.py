import asyncio
import os
from pathlib import Path
from typing import Callable, Optional
import flet as ft

from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.categories import CATEGORY_COLORS
from ui.coordinator import UICoordinator, format_bytes, format_speed
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


def get_file_icon(filename: Optional[str]) -> str:
    """Returns an appropriate Fluent/Material icon based on file extension."""
    if not filename:
        return ft.Icons.INSERT_DRIVE_FILE_ROUNDED
    ext = Path(filename).suffix.lower()
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2", ".xz"):
        return ft.Icons.FOLDER_ZIP_ROUNDED
    elif ext in (".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm"):
        return ft.Icons.VIDEO_FILE_ROUNDED
    elif ext in (".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a"):
        return ft.Icons.AUDIO_FILE_ROUNDED
    elif ext in (".exe", ".msi", ".bin", ".appimage", ".apk"):
        return ft.Icons.TERMINAL_ROUNDED
    elif ext in (".pdf", ".doc", ".docx", ".txt", ".odt", ".epub"):
        return ft.Icons.DESCRIPTION_ROUNDED
    elif ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"):
        return ft.Icons.IMAGE_ROUNDED
    return ft.Icons.INSERT_DRIVE_FILE_ROUNDED


STATUS_CONFIG = {
    DownloadStatus.DOWNLOADING: {
        "text": "In Download",
        "color": "#60CDFF",
        "bg": "rgba(0, 120, 212, 0.15)",
        "border": "rgba(0, 120, 212, 0.40)",
    },
    DownloadStatus.COMPLETED: {
        "text": "Completato",
        "color": "#10B981",
        "bg": "rgba(16, 185, 129, 0.15)",
        "border": "rgba(16, 185, 129, 0.40)",
    },
    DownloadStatus.PAUSED: {
        "text": "In Pausa",
        "color": "#F59E0B",
        "bg": "rgba(245, 158, 11, 0.15)",
        "border": "rgba(245, 158, 11, 0.40)",
    },
    DownloadStatus.FAILED: {
        "text": "Errore",
        "color": "#EF4444",
        "bg": "rgba(239, 68, 68, 0.15)",
        "border": "rgba(239, 68, 68, 0.40)",
    },
    DownloadStatus.CANCELLED: {
        "text": "Annullato",
        "color": "#757575",
        "bg": "rgba(255, 255, 255, 0.05)",
        "border": "rgba(255, 255, 255, 0.12)",
    },
    DownloadStatus.PENDING: {
        "text": "In Coda",
        "color": "#60CDFF",
        "bg": "rgba(0, 120, 212, 0.15)",
        "border": "rgba(0, 120, 212, 0.40)",
    },
}


class DownloadCard(ft.Container):
    """
    Windows 11 Fluent Dark Card widget representing a single download task.
    Displays file details, status badge, progress bar, live speed, ETA,
    and row action buttons (Pause, Resume, Open Folder, Cancel, Retry).
    """

    def __init__(
        self,
        task: DownloadTask,
        coordinator: UICoordinator,
        on_action: Optional[Callable[[], None]] = None,
        on_open_details: Optional[Callable[[DownloadTask], None]] = None,
    ):
        super().__init__()
        self.task = task
        self.coordinator = coordinator
        self.on_action = on_action
        self.on_open_details = on_open_details

        self.bgcolor = SURFACE_CARD
        self.border_radius = 20
        self.padding = ft.Padding(left=16, right=16, top=10, bottom=10)
        self.border = ft.Border.all(1, BORDER_COLOR)
        self.shadow = GLASS_CARD_SHADOW
        self.margin = ft.Margin(bottom=8)

        # Internal control references for in-place updates
        self._last_status: DownloadStatus = task.status
        self._display_name_text: Optional[ft.Text] = None
        self._short_dest_text: Optional[ft.Text] = None
        self._category_badge_text: Optional[ft.Text] = None
        self._category_badge: Optional[ft.Container] = None
        self._status_badge_text: Optional[ft.Text] = None
        self._status_badge: Optional[ft.Container] = None
        self._progress_bar_slot: Optional[ft.Container] = None
        self._metrics_text: Optional[ft.Text] = None
        self._actions_row: Optional[ft.Row] = None

        # Build Card Content
        self.content = self._build_card()

    def _get_metrics_info(self) -> str:
        task = self.task
        status = task.status
        is_torrent = task.task_type == "TORRENT" or (task.category and task.category.lower() == "torrents")
        spd = self.coordinator.task_speeds.get(task.task_id, 0.0)
        eta = self.coordinator.task_etas.get(task.task_id, "--:--")

        size_text = (
            f"{format_bytes(task.downloaded_bytes)} / {format_bytes(task.total_size)} ({task.progress_percentage:.1f}%)"
            if task.total_size > 0
            else f"{format_bytes(task.downloaded_bytes)} scaricati"
        )

        if status == DownloadStatus.COMPLETED:
            size_val = task.total_size if task.total_size > 0 else task.downloaded_bytes
            return f"{format_bytes(size_val)} scaricati   •   Completato con successo"

        if is_torrent:
            dl_speed = task.speed_bps or spd
            up_speed = task.upload_speed_bps
            speed_str = f"▼ {format_speed(dl_speed)}  ▲ {format_speed(up_speed)}"
            swarm_str = f"Seeds: {task.seeds} | Peers: {task.peers}"
            pieces_str = f"Pezzi: {task.pieces_completed}/{task.pieces_total}" if task.pieces_total > 0 else ""

            if status == DownloadStatus.DOWNLOADING:
                extra_parts = [speed_str, swarm_str]
                if pieces_str:
                    extra_parts.append(pieces_str)
                extra_parts.append(f"ETA {eta}")
                return f"{size_text}   •   " + "   •   ".join(extra_parts)
            elif status == DownloadStatus.PAUSED:
                return f"{size_text}   •   In Pausa (Torrent)"
            elif status == DownloadStatus.FAILED:
                return f"{size_text}   •   {task.error_message or 'Errore Torrent'}"
            elif status == DownloadStatus.CANCELLED:
                return f"{size_text}   •   Annullato"
            else:
                return f"{size_text}   •   In Coda (Torrent)"
        else:
            if status == DownloadStatus.DOWNLOADING:
                return f"{size_text}   •   {format_speed(spd)}   •   ETA {eta}"
            elif status == DownloadStatus.PAUSED:
                return f"{size_text}   •   In Pausa"
            elif status == DownloadStatus.FAILED:
                return f"{size_text}   •   {task.error_message or 'Errore'}"
            elif status == DownloadStatus.CANCELLED:
                return f"{size_text}   •   Annullato"
            else:
                return f"{size_text}   •   In Coda"

    def _handle_open_details(self) -> None:
        if self.on_open_details:
            self.on_open_details(self.task)

    def _build_action_buttons(self) -> list[ft.Control]:
        status = self.task.status
        action_buttons: list[ft.Control] = [
            ft.IconButton(
                icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                icon_color=TEXT_MUTED,
                icon_size=16,
                tooltip="Dettagli Download",
                on_click=lambda e: self._handle_open_details(),
            )
        ]

        if status == DownloadStatus.DOWNLOADING:
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.PAUSE_ROUNDED,
                    icon_color="#F59E0B",
                    icon_size=16,
                    tooltip="Metti in Pausa",
                    on_click=self._handle_pause,
                )
            )
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.CANCEL_ROUNDED,
                    icon_color="#EF4444",
                    icon_size=16,
                    tooltip="Annulla",
                    on_click=self._handle_cancel,
                )
            )
        elif status == DownloadStatus.PAUSED:
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    icon_color=ACCENT_BLUE,
                    icon_size=16,
                    tooltip="Riprendi Download",
                    on_click=self._handle_resume,
                )
            )
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=TEXT_MUTED,
                    icon_size=16,
                    tooltip="Elimina",
                    on_click=self._handle_delete,
                )
            )
        elif status == DownloadStatus.COMPLETED:
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                    icon_color="#10B981",
                    icon_size=16,
                    tooltip="Apri Cartella di Destinazione",
                    on_click=self._handle_open_folder,
                )
            )
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=TEXT_MUTED,
                    icon_size=16,
                    tooltip="Elimina dalla Coda",
                    on_click=self._handle_delete,
                )
            )
        elif status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.REFRESH_ROUNDED,
                    icon_color=ACCENT_BLUE,
                    icon_size=16,
                    tooltip="Riprova Download",
                    on_click=self._handle_retry,
                )
            )
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    icon_color=TEXT_MUTED,
                    icon_size=16,
                    tooltip="Elimina",
                    on_click=self._handle_delete,
                )
            )
        elif status == DownloadStatus.PENDING:
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.PAUSE_ROUNDED,
                    icon_color="#F59E0B",
                    icon_size=16,
                    tooltip="Metti in Pausa",
                    on_click=self._handle_pause,
                )
            )
            action_buttons.append(
                ft.IconButton(
                    icon=ft.Icons.CANCEL_ROUNDED,
                    icon_color="#EF4444",
                    icon_size=16,
                    tooltip="Annulla",
                    on_click=self._handle_cancel,
                )
            )
        return action_buttons

    def _build_card(self) -> ft.Column:
        task = self.task
        status = task.status
        self._last_status = status
        cfg = STATUS_CONFIG.get(status, STATUS_CONFIG[DownloadStatus.PENDING])

        is_torrent = task.task_type == "TORRENT" or (task.category and task.category.lower() == "torrents")
        icon_name = ft.Icons.HUB_ROUNDED if is_torrent else get_file_icon(task.filename or task.url)

        raw_name = task.filename or (task.url.split("/")[-1].split("?")[0] if "/" in task.url else task.url)
        if raw_name:
            display_name = os.path.basename(str(raw_name).replace("\\", "/"))
        else:
            display_name = task.url
        if not display_name or display_name.strip() == "":
            display_name = task.url

        # File icon container
        file_icon_box = ft.Container(
            width=36,
            height=36,
            border_radius=18,
            bgcolor="rgba(0, 120, 212, 0.15)",
            border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
            alignment=ft.Alignment(0, 0),
            content=ft.Icon(icon_name, color=ACCENT_LIGHT if is_torrent else ACCENT_BLUE, size=18),
        )

        dest_path = Path(task.destination_dir)
        parts = dest_path.parts
        if len(parts) >= 2:
            short_dest = f"📁 ...\\{parts[-2]}\\{parts[-1]}"
        elif len(parts) == 1:
            short_dest = f"📁 ...\\{parts[-1]}"
        else:
            short_dest = f"📁 {task.destination_dir}"

        # Category & Status Badges
        cat_name = task.category or "Altro"
        cat_cfg = CATEGORY_COLORS.get(cat_name, CATEGORY_COLORS["Altro"])
        self._category_badge_text = ft.Text(
            cat_name,
            color=cat_cfg["color"],
            size=9,
            weight=ft.FontWeight.BOLD,
        )
        self._category_badge = ft.Container(
            bgcolor=cat_cfg["bg"],
            padding=ft.Padding(left=8, right=8, top=2, bottom=2),
            border_radius=6,
            border=ft.Border.all(1, cat_cfg["border"]),
            content=self._category_badge_text,
        )

        self._status_badge_text = ft.Text(
            cfg["text"],
            color=cfg["color"],
            size=10,
            weight=ft.FontWeight.BOLD,
        )
        self._status_badge = ft.Container(
            bgcolor=cfg["bg"],
            padding=ft.Padding(left=10, right=10, top=3, bottom=3),
            border_radius=6,
            border=ft.Border.all(1, cfg["border"]),
            content=self._status_badge_text,
        )

        badge_controls: list[ft.Control] = []
        is_generic_torrent = is_torrent and (not task.category or str(task.category).strip().lower() in ("torrents", "torrent"))
        if is_torrent:
            badge_controls.append(
                ft.Container(
                    bgcolor="rgba(0, 120, 212, 0.15)",
                    padding=ft.Padding(left=8, right=8, top=2, bottom=2),
                    border_radius=6,
                    border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                    content=ft.Row(
                        spacing=3,
                        tight=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(ft.Icons.HUB_ROUNDED, size=10, color=ACCENT_LIGHT),
                            ft.Text("TORRENT", color=ACCENT_LIGHT, size=9, weight=ft.FontWeight.BOLD),
                        ],
                    ),
                )
            )
        if not is_generic_torrent:
            badge_controls.append(self._category_badge)
        badge_controls.append(self._status_badge)

        self._display_name_text = ft.Text(
            display_name,
            weight=ft.FontWeight.BOLD,
            size=13,
            color=TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            width=420,
        )
        self._short_dest_text = ft.Text(
            short_dest,
            size=10,
            color=TEXT_SECONDARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            width=420,
        )

        header_row = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        file_icon_box,
                        ft.Column(
                            spacing=1,
                            controls=[
                                self._display_name_text,
                                self._short_dest_text,
                            ],
                        ),
                    ],
                ),
                ft.Row(
                    spacing=6,
                    tight=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=badge_controls,
                ),
            ],
        )

        progress_val = (task.progress_percentage / 100.0) if task.total_size > 0 else 0.0
        if status == DownloadStatus.COMPLETED:
            progress_val = 1.0

        progress_bar = build_gradient_progress_bar(progress_val, status.value)
        self._progress_bar_slot = ft.Container(content=progress_bar)

        self._metrics_text = ft.Text(self._get_metrics_info(), size=11, color=TEXT_SECONDARY)
        self._actions_row = ft.Row(spacing=2, controls=self._build_action_buttons())

        footer_row = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self._metrics_text,
                self._actions_row,
            ],
        )

        return ft.Column(
            spacing=8,
            controls=[
                header_row,
                self._progress_bar_slot,
                footer_row,
            ],
        )

    def update_task_state(self, new_task: DownloadTask) -> None:
        """Updates the card in-place with new task data without destroying child controls."""
        prev_status = self._last_status
        self.task = new_task
        status = new_task.status

        # 1. Update Metrics Text
        new_metrics = self._get_metrics_info()
        if self._metrics_text and self._metrics_text.value != new_metrics:
            self._metrics_text.value = new_metrics

        # 2. Update Progress Bar
        progress_val = (new_task.progress_percentage / 100.0) if new_task.total_size > 0 else 0.0
        if status == DownloadStatus.COMPLETED:
            progress_val = 1.0
        if self._progress_bar_slot:
            self._progress_bar_slot.content = build_gradient_progress_bar(progress_val, status.value)

        # 3. Update Status Badge & Action Buttons if status transitioned
        if status != prev_status or status != self._last_status:
            self._last_status = status
            cfg = STATUS_CONFIG.get(status, STATUS_CONFIG[DownloadStatus.PENDING])
            if self._status_badge_text and self._status_badge:
                self._status_badge_text.value = cfg["text"]
                self._status_badge_text.color = cfg["color"]
                self._status_badge.bgcolor = cfg["bg"]
                self._status_badge.border = ft.Border.all(1, cfg["border"])
            if self._actions_row:
                self._actions_row.controls = self._build_action_buttons()

    def update_task_data(self, task: DownloadTask) -> None:
        self.update_task_state(task)

    def update_live_metrics(self) -> None:
        """Surgically updates only live metrics on this single card."""
        new_metrics = self._get_metrics_info()
        if self._metrics_text:
            self._metrics_text.value = new_metrics
        if self._progress_bar_slot:
            progress_val = (self.task.progress_percentage / 100.0) if self.task.total_size > 0 else 0.0
            self._progress_bar_slot.content = build_gradient_progress_bar(progress_val, self.task.status.value)
        safe_update(self)

    def _handle_pause(self, e: ft.ControlEvent) -> None:
        async def _run():
            await self.coordinator.pause_task(self.task.task_id)
            if self.on_action:
                self.on_action()
        if e.page:
            e.page.run_task(_run)
        else:
            asyncio.create_task(_run())

    def _handle_resume(self, e: ft.ControlEvent) -> None:
        async def _run():
            await self.coordinator.resume_task(self.task.task_id)
            if self.on_action:
                self.on_action()
        if e.page:
            e.page.run_task(_run)
        else:
            asyncio.create_task(_run())

    def _handle_retry(self, e: ft.ControlEvent) -> None:
        async def _run():
            await self.coordinator.retry_task(self.task.task_id)
            if self.on_action:
                self.on_action()
        if e.page:
            e.page.run_task(_run)
        else:
            asyncio.create_task(_run())

    def _handle_cancel(self, e: ft.ControlEvent) -> None:
        async def _run():
            await self.coordinator.cancel_task(self.task.task_id)
            if self.on_action:
                self.on_action()
        if e.page:
            e.page.run_task(_run)
        else:
            asyncio.create_task(_run())

    def _handle_delete(self, e: ft.ControlEvent) -> None:
        async def _run():
            await self.coordinator.delete_task(self.task.task_id)
            if self.on_action:
                self.on_action()
        if e.page:
            e.page.run_task(_run)
        else:
            asyncio.create_task(_run())

    def _handle_open_folder(self, e: ft.ControlEvent) -> None:
        self.coordinator.open_containing_folder(self.task)
