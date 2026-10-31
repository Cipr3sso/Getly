"""
Batch Download Dialog for Getly (Windows 11 Fluent Dark).
Allows users to enter URL patterns with numeric [01-15] or alphabetic [a-z] intervals
or multiline URLs and queues all generated tasks with category auto-routing.
"""

import asyncio
from pathlib import Path
from typing import Callable, List, Optional
import flet as ft

from core.scheduler.batch import generate_batch_urls
from ui.coordinator import UICoordinator
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
    safe_update,
)


class BatchDownloadDialog(ft.AlertDialog):
    """
    WinUI 3 Dialog for batch downloading sequential patterns and multiline URLs.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_added: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_added = on_added
        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        self._generated_urls: List[str] = []

        # Title
        self.title = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.DYNAMIC_FEED_ROUNDED, color=ACCENT_LIGHT, size=20),
                ft.Text("Aggiungi Download in Batch", size=16, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
            ],
        )

        # Pattern input field
        self.pattern_input = ft.TextField(
            label="Pattern URL o lista di link",
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=11),
            hint_text="Es: https://example.com/files/archive_[01-15].zip o data_[a-z].tar.gz",
            hint_style=ft.TextStyle(color=TEXT_MUTED, size=11),
            text_size=11,
            multiline=True,
            min_lines=3,
            max_lines=5,
            color=TEXT_PRIMARY,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            bgcolor=SURFACE_CARD,
            content_padding=ft.Padding(10, 8, 10, 8),
            on_change=self._on_pattern_change,
        )

        # Preview list / summary
        self.preview_summary_text = ft.Text(
            "Inserisci un pattern per generare i link.",
            size=11,
            color=TEXT_MUTED,
        )
        self.preview_urls_column = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)

        self.preview_container = ft.Container(
            height=140,
            bgcolor=SURFACE_CARD,
            border_radius=6,
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding(8, 6, 8, 6),
            content=self.preview_urls_column,
        )

        # Options
        self.start_paused_cb = ft.Checkbox(
            label="Avvia in stato di Pausa",
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=11),
            value=False,
            active_color=ACCENT_BLUE,
        )

        # Destination directory
        default_dir = str(self.coordinator.queue_manager.default_download_dir)
        self.dest_field = ft.TextField(
            label="Cartella di destinazione (predefinita)",
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=11),
            value=default_dir,
            text_size=11,
            dense=True,
            color=TEXT_PRIMARY,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            bgcolor=SURFACE_CARD,
            content_padding=ft.Padding(8, 6, 8, 6),
            expand=True,
        )

        self.btn_add_all = ft.FilledButton(
            content="Aggiungi tutti alla coda (0)",
            icon=ft.Icons.PLAYLIST_ADD_ROUNDED,
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            disabled=True,
            on_click=self._handle_add_all,
        )

        self.content = ft.Container(
            width=620,
            padding=ft.Padding(top=6, bottom=6, left=0, right=0),
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    self.pattern_input,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Anteprima URL generati:", size=11, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
                            self.preview_summary_text,
                        ],
                    ),
                    self.preview_container,
                    ft.Row(
                        spacing=6,
                        controls=[
                            self.dest_field,
                        ],
                    ),
                    self.start_paused_cb,
                ],
            ),
        )

        self.actions = [
            ft.TextButton(
                content="Annulla",
                on_click=self._handle_close,
                style=ft.ButtonStyle(color=TEXT_SECONDARY),
            ),
            self.btn_add_all,
        ]
        self.actions_alignment = ft.MainAxisAlignment.END

    def _on_pattern_change(self, e: Optional[ft.ControlEvent] = None) -> None:
        raw_val = (e.control.value if e and hasattr(e, "control") and e.control else self.pattern_input.value) or ""
        text = raw_val.strip()
        if not text:
            self._generated_urls = []
            self.preview_summary_text.value = "Inserisci un pattern per generare i link."
            self.preview_summary_text.color = TEXT_MUTED
            self.preview_urls_column.controls.clear()
            self.btn_add_all.disabled = True
            self.btn_add_all.content = "Aggiungi tutti alla coda (0)"
            safe_update(self)
            return

        urls = generate_batch_urls(text)
        self._generated_urls = urls
        count = len(urls)

        if count == 0:
            self.preview_summary_text.value = "Nessun URL valido generato."
            self.preview_summary_text.color = TEXT_MUTED
            self.preview_urls_column.controls.clear()
            self.btn_add_all.disabled = True
            self.btn_add_all.content = "Aggiungi tutti alla coda (0)"
        else:
            self.preview_summary_text.value = f"📦 {count} link generati con successo"
            self.preview_summary_text.color = COLOR_SUCCESS
            self.btn_add_all.disabled = False
            self.btn_add_all.content = f"Aggiungi tutti alla coda ({count})"

            # Render preview (first 10 and last 3 if large)
            self.preview_urls_column.controls.clear()
            if count <= 15:
                for idx, u in enumerate(urls, 1):
                    self.preview_urls_column.controls.append(
                        ft.Text(f"{idx}. {u}", size=10, color=TEXT_SECONDARY, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
                    )
            else:
                for idx in range(1, 11):
                    self.preview_urls_column.controls.append(
                        ft.Text(f"{idx}. {urls[idx-1]}", size=10, color=TEXT_SECONDARY, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
                    )
                self.preview_urls_column.controls.append(
                    ft.Text(f"... altri {count - 13} link intermedi ...", size=10, color=TEXT_MUTED, weight=ft.FontWeight.BOLD)
                )
                for idx in range(count - 2, count + 1):
                    self.preview_urls_column.controls.append(
                        ft.Text(f"{idx}. {urls[idx-1]}", size=10, color=TEXT_SECONDARY, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
                    )

        safe_update(self)

    def _handle_add_all(self, e: ft.ControlEvent) -> None:
        if not self._generated_urls:
            return

        urls = list(self._generated_urls)
        start_paused = bool(self.start_paused_cb.value)
        dest_dir = Path(self.dest_field.value.strip()) if self.dest_field.value else None

        async def _queue_all():
            for u in urls:
                await self.coordinator.add_download(
                    url=u,
                    destination_dir=dest_dir,
                    start_paused=start_paused,
                )
            if self.on_added:
                self.on_added()

        if self.page:
            self.page.run_task(_queue_all)

        self._handle_close()

    def show(self, page: ft.Page) -> None:
        if not page:
            return
        if hasattr(page, "open"):
            page.open(self)
        elif hasattr(page, "show_dialog"):
            page.show_dialog(self)
        else:
            page.dialog = self
            self.open = True
            safe_update(page)

    def _handle_close(self, e: Optional[ft.ControlEvent] = None) -> None:
        if self.page:
            if hasattr(self.page, "close"):
                self.page.close(self)
            else:
                self.open = False
                safe_update(self.page)
