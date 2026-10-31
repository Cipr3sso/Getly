"""
Torrent and Magnet File Selection Dialog for Getly.
Windows 11 Fluent Dark modal allowing metadata inspection, selective file download,
and custom destination selection.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional
import flet as ft

logger = logging.getLogger(__name__)

from core.engine.torrent_engine import (
    TorrentSessionManager,
    TorrentMetadata,
    TorrentFileInfo,
    LIBTORRENT_AVAILABLE,
)
from core.scheduler.categories import CATEGORY_TORRENTS, get_category_subfolder
from ui.coordinator import UICoordinator, format_bytes
from ui.components.add_download_dialog import _pick_directory_sync
from ui.components.download_card import get_file_icon
from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
)


class TorrentDialog(ft.AlertDialog):
    """
    Windows 11 Fluent Dark Torrent & Magnet Selection Modal Dialog.
    Resolves metadata, displays file tree with checkboxes, destination folder picker,
    and enqueues selective downloads.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        source: str,
        on_added: Optional[Callable[[], None]] = None,
        initial_dir: Optional[Path] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.source = source.strip()
        self.on_added = on_added

        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        # Base destination defaults to Torrents subfolder
        default_base = self.coordinator.queue_manager.default_download_dir
        self.destination_dir = initial_dir or get_category_subfolder(default_base, CATEGORY_TORRENTS)

        self.metadata: Optional[TorrentMetadata] = None
        self.file_checkboxes: Dict[int, ft.Checkbox] = {}
        self._is_resolving = True

        # Header Title
        self.title = ft.Row(
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Container(
                    width=32,
                    height=32,
                    border_radius=16,
                    bgcolor="rgba(0, 120, 212, 0.15)",
                    border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                    alignment=ft.Alignment(0, 0),
                    content=ft.Icon(ft.Icons.HUB_ROUNDED, color=ACCENT_BLUE, size=18),
                ),
                ft.Column(
                    spacing=1,
                    tight=True,
                    controls=[
                        ft.Text("Download BitTorrent / Magnet", weight=ft.FontWeight.BOLD, size=15, color=TEXT_PRIMARY),
                        ft.Text("Scegli i file da scaricare e la destinazione", size=11, color=TEXT_SECONDARY),
                    ],
                ),
            ],
        )

        # Status & Metadata summary controls
        self.meta_summary_text = ft.Text(
            "Connessione allo swarm DHT/Trackers in corso...",
            size=12,
            color=TEXT_SECONDARY,
        )
        self.hash_text = ft.Text(
            "",
            size=10,
            color=TEXT_MUTED,
            selectable=True,
        )

        # Path controls
        self.path_text = ft.Text(
            str(self.destination_dir),
            size=11,
            color=TEXT_PRIMARY,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
            expand=True,
        )
        self.browse_btn = ft.OutlinedButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            content="Sfoglia...",
            style=ft.ButtonStyle(
                color=TEXT_PRIMARY,
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, BORDER_COLOR),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=self._handle_browse,
        )

        # File list container
        self.file_list_column = ft.Column(
            spacing=4,
            scroll=ft.ScrollMode.ADAPTIVE,
            expand=True,
        )

        self.loading_spinner = ft.Column(
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
            expand=True,
            controls=[
                ft.ProgressRing(color=ACCENT_BLUE, width=32, height=32),
                ft.Text("Risoluzione metadati dallo swarm DHT / Trackers...", size=12, color=TEXT_SECONDARY),
                ft.Text("Potrebbe richiedere alcuni secondi", size=10, color=TEXT_MUTED),
            ],
        )

        # Selection toggle buttons
        self.select_all_btn = ft.TextButton(
            "Seleziona Tutti",
            style=ft.ButtonStyle(color=ACCENT_LIGHT),
            on_click=lambda e: self._toggle_all(True),
        )
        self.deselect_all_btn = ft.TextButton(
            "Deseleziona Tutti",
            style=ft.ButtonStyle(color=TEXT_MUTED),
            on_click=lambda e: self._toggle_all(False),
        )

        self.selected_size_text = ft.Text("0 B selezionati", size=11, color=TEXT_SECONDARY)

        # Priority Dropdown
        self.priority_dropdown = ft.Dropdown(
            label="Priorità",
            value="4",
            options=[
                ft.dropdown.Option("7", "Alta"),
                ft.dropdown.Option("4", "Normale"),
                ft.dropdown.Option("1", "Bassa"),
            ],
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            border_radius=6,
            bgcolor=SURFACE_CARD,
            color=TEXT_PRIMARY,
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=11),
            width=130,
            dense=True,
        )

        # Action Buttons
        self.cancel_btn = ft.TextButton(
            "Annulla",
            style=ft.ButtonStyle(color=TEXT_SECONDARY),
            on_click=self._handle_cancel,
        )
        self.add_paused_btn = ft.OutlinedButton(
            icon=ft.Icons.PAUSE_CIRCLE_OUTLINE_ROUNDED,
            content="Aggiungi in Pausa",
            style=ft.ButtonStyle(
                color="#F59E0B",
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, "rgba(245, 158, 11, 0.40)"),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=lambda e: self._handle_submit(start_paused=True),
            disabled=True,
        )
        self.download_now_btn = ft.FilledButton(
            icon=ft.Icons.ROCKET_LAUNCH_ROUNDED,
            content="Scarica Ora",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=lambda e: self._handle_submit(start_paused=False),
            disabled=True,
        )

        # Main Layout
        self.content = ft.Container(
            width=650,
            height=460,
            padding=ft.Padding(12, 8, 12, 8),
            content=ft.Column(
                spacing=10,
                expand=True,
                controls=[
                    # 1. Metadata Info Card
                    ft.Container(
                        bgcolor=SURFACE_CARD,
                        border=ft.Border.all(1, BORDER_COLOR),
                        border_radius=8,
                        padding=10,
                        content=ft.Column(
                            spacing=3,
                            controls=[
                                self.meta_summary_text,
                                self.hash_text,
                            ],
                        ),
                    ),
                    # 2. Destination Folder Bar
                    ft.Container(
                        bgcolor=SURFACE_CARD,
                        border=ft.Border.all(1, BORDER_COLOR),
                        border_radius=8,
                        padding=ft.Padding(10, 6, 10, 6),
                        content=ft.Row(
                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Row(
                                    spacing=8,
                                    expand=True,
                                    controls=[
                                        ft.Icon(ft.Icons.FOLDER_SPECIAL_ROUNDED, size=18, color=ACCENT_BLUE),
                                        self.path_text,
                                    ],
                                ),
                                self.browse_btn,
                            ],
                        ),
                    ),
                    # 3. Toolbar: Select all / None & Priority
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=4,
                                controls=[
                                    self.select_all_btn,
                                    self.deselect_all_btn,
                                    self.selected_size_text,
                                ],
                            ),
                            self.priority_dropdown,
                        ],
                    ),
                    # 4. Central File Tree
                    ft.Container(
                        expand=True,
                        bgcolor=SURFACE_CARD,
                        border=ft.Border.all(1, BORDER_COLOR),
                        border_radius=8,
                        padding=8,
                        content=self.loading_spinner,
                    ),
                ],
            ),
        )

        self.actions = [self.cancel_btn, self.add_paused_btn, self.download_now_btn]
        self.actions_alignment = ft.MainAxisAlignment.END

        # Kickoff async metadata resolution
        self._resolution_task: Optional[asyncio.Task] = None

    def did_mount(self):
        super().did_mount()
        self._resolution_task = asyncio.create_task(self._resolve_metadata_async())

    async def _resolve_metadata_async(self):
        """Resolves torrent/magnet metadata without blocking the UI thread."""
        session_mgr = TorrentSessionManager.get_instance(self.coordinator.session_pool)
        try:
            meta = await session_mgr.resolve_metadata(self.source, timeout=25.0, timeout_sec=25.0)

            self._is_resolving = False
            if not meta or not getattr(meta, "has_metadata", False) or not meta.files:
                self.meta_summary_text.value = "⚠️ Impossibile risolvere i metadati. Verifica il link o la connessione."
                self.meta_summary_text.color = "#EF4444"
                self.loading_spinner.controls = [
                    ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color="#EF4444", size=32),
                    ft.Text("Timeout o errore durante il recupero dei metadati.", size=12, color=TEXT_SECONDARY),
                ]
                try:
                    if getattr(self, "page", None) is not None:
                        self.page.update()
                except Exception:
                    pass
                return

            self.metadata = meta
            num_files = getattr(meta, "num_files", len(getattr(meta, "files", [])))
            self.meta_summary_text.value = f"📦 {meta.name} ({format_bytes(meta.total_size)} • {num_files} file)"
            self.meta_summary_text.color = TEXT_PRIMARY
            self.hash_text.value = f"Info-Hash: {meta.info_hash}"

            # Populate File Tree
            self.file_list_column.controls.clear()
            for f in meta.files:
                cb = ft.Checkbox(
                    value=True,
                    active_color=ACCENT_BLUE,
                    on_change=lambda e, idx=f.index: self._on_file_toggled(idx, e.control.value),
                )
                self.file_checkboxes[f.index] = cb

                file_icon = get_file_icon(f.path)
                row = ft.Container(
                    padding=ft.Padding(4, 2, 4, 2),
                    border_radius=4,
                    content=ft.Row(
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            cb,
                            ft.Icon(file_icon, size=16, color=ACCENT_BLUE),
                            ft.Text(f.path, size=11, color=TEXT_PRIMARY, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(format_bytes(f.size), size=10, color=TEXT_MUTED),
                        ],
                    ),
                )
                self.file_list_column.controls.append(row)

            # Replace loading spinner with file tree
            file_tree_container = self.content.content.controls[3]
            file_tree_container.content = self.file_list_column

            # Enable action buttons
            self.add_paused_btn.disabled = False
            self.download_now_btn.disabled = False
            self._update_selected_summary()
            try:
                if getattr(self, "page", None) is not None:
                    self.page.update()
            except Exception:
                pass
        except Exception as exc:
            logger.error("Error during torrent metadata resolution for %s: %s", self.source, exc, exc_info=True)
            self._is_resolving = False
            self.meta_summary_text.value = "⚠️ Errore durante l'elaborazione dei metadati."
            self.meta_summary_text.color = "#EF4444"
            self.loading_spinner.controls = [
                ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, color="#EF4444", size=32),
                ft.Text(f"Errore: {str(exc)}", size=12, color=TEXT_SECONDARY),
            ]
            try:
                if getattr(self, "page", None) is not None:
                    self.page.update()
            except Exception:
                pass

    def _on_file_toggled(self, index: int, is_checked: bool):
        if index in self.file_checkboxes:
            self.file_checkboxes[index].value = is_checked
        self._update_selected_summary()
        try:
            if getattr(self, "page", None) is not None:
                self.page.update()
        except Exception:
            pass

    def _toggle_all(self, select: bool):
        for cb in self.file_checkboxes.values():
            cb.value = select
        self._update_selected_summary()
        try:
            if getattr(self, "page", None) is not None:
                self.page.update()
        except Exception:
            pass

    def _update_selected_summary(self):
        if not self.metadata:
            return
        selected_size = 0
        selected_count = 0
        for f in self.metadata.files:
            if self.file_checkboxes.get(f.index) and self.file_checkboxes[f.index].value:
                selected_size += f.size
                selected_count += 1
        num_files = getattr(self.metadata, "num_files", len(getattr(self.metadata, "files", [])))
        self.selected_size_text.value = f"{format_bytes(selected_size)} selezionati ({selected_count}/{num_files})"
        has_selection = selected_count > 0
        self.add_paused_btn.disabled = not has_selection
        self.download_now_btn.disabled = not has_selection

    def _handle_browse(self, e: ft.ControlEvent):
        async def _browse():
            chosen = await asyncio.to_thread(_pick_directory_sync, str(self.destination_dir))
            if chosen:
                self.destination_dir = Path(chosen)
                self.path_text.value = str(self.destination_dir)
                try:
                    if getattr(self, "page", None) is not None:
                        self.page.update()
                except Exception:
                    pass

        if self.page:
            self.page.run_task(_browse)

    def _handle_cancel(self, e: ft.ControlEvent):
        if self._resolution_task and not self._resolution_task.done():
            self._resolution_task.cancel()
        if self.page:
            self.page.pop_dialog()

    def _handle_submit(self, start_paused: bool):
        if not self.metadata:
            return

        selected_indices = [
            idx for idx, cb in self.file_checkboxes.items() if cb.value
        ]

        priority_val = int(self.priority_dropdown.value or "4")

        async def _add():
            await self.coordinator.add_download(
                url=self.source,
                destination_dir=self.destination_dir,
                filename=self.metadata.name,
                category=CATEGORY_TORRENTS,
                start_paused=start_paused,
                task_type="TORRENT",
                selected_files=selected_indices,
                info_hash=self.metadata.info_hash,
                priority=priority_val,
            )
            if self.on_added:
                self.on_added()

        if self.page:
            self.page.run_task(_add)
            self.page.pop_dialog()
