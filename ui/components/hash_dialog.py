"""
Hash Calculation & Verification Dialog for Getly (Windows 11 Fluent Dark).
Computes MD5, SHA-1, and SHA-256 checksums in background threads with live progress
and provides real-time hash comparison verification against expected values.
"""

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Callable, Dict, Optional
import flet as ft

from core.scheduler.task import DownloadTask
from ui.coordinator import UICoordinator, format_bytes
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
    safe_update,
)


def compute_file_hashes(
    file_path: Path | str,
    on_progress: Optional[Callable[[float], None]] = None,
    chunk_size: int = 1024 * 1024,
) -> Dict[str, str]:
    """
    Computes MD5, SHA-1, and SHA-256 hashes synchronously in blocks of chunk_size (default 1MB).
    Reports progress ratio (0.0 to 1.0) via on_progress callback if provided.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    total_size = path.stat().st_size
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()

    read_bytes = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            md5.update(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
            read_bytes += len(chunk)
            if on_progress and total_size > 0:
                on_progress(min(1.0, read_bytes / total_size))

    if on_progress:
        on_progress(1.0)

    return {
        "md5": md5.hexdigest().lower(),
        "sha1": sha1.hexdigest().lower(),
        "sha256": sha256.hexdigest().lower(),
    }


class HashDialog(ft.AlertDialog):
    """
    WinUI 3 Dialog for file checksum calculation and verification.
    """

    def __init__(
        self,
        task: DownloadTask,
        coordinator: UICoordinator,
    ):
        super().__init__()
        self.task = task
        self.coordinator = coordinator
        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        self.file_path = task.target_file or (task.destination_dir / (task.filename or "file"))
        self._computed_hashes: Dict[str, str] = {}
        self._is_computing = True

        # Header
        self.title = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ft.Icons.SHIELD_OUTLINED, color=ACCENT_LIGHT, size=20),
                ft.Text("Verifica Integrità File (Checksum Hash)", size=16, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
            ],
        )

        # Progress elements
        self.progress_bar = ft.ProgressBar(
            value=None,  # indeterminate initially
            color=ACCENT_BLUE,
            bgcolor="rgba(255, 255, 255, 0.10)",
            height=4,
        )
        self.status_text = ft.Text(
            "Calcolo hash in corso...",
            size=11,
            color=TEXT_MUTED,
        )

        # Hash text fields
        self.md5_field = self._build_hash_field("MD5", "In calcolo...")
        self.sha1_field = self._build_hash_field("SHA-1", "In calcolo...")
        self.sha256_field = self._build_hash_field("SHA-256", "In calcolo...")

        # Expected hash input field
        self.compare_input = ft.TextField(
            hint_text="Incolla l'hash atteso fornito dalla sorgente...",
            hint_style=ft.TextStyle(color=TEXT_MUTED, size=11),
            text_size=11,
            dense=True,
            color=TEXT_PRIMARY,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            bgcolor=SURFACE_CARD,
            content_padding=ft.Padding(10, 8, 10, 8),
            on_change=self._on_compare_change,
        )

        # Match feedback container
        self.match_badge = ft.Container(visible=False)

        # Main content container
        fn = task.filename or self.file_path.name
        fsize = format_bytes(task.total_size or (self.file_path.stat().st_size if self.file_path.exists() else 0))

        self.content = ft.Container(
            width=580,
            padding=ft.Padding(top=6, bottom=6, left=0, right=0),
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=[
                    # File info card
                    ft.Container(
                        bgcolor=SURFACE_CARD,
                        border_radius=8,
                        border=ft.Border.all(1, BORDER_COLOR),
                        padding=ft.Padding(10, 8, 10, 8),
                        content=ft.Column(
                            tight=True,
                            spacing=3,
                            controls=[
                                ft.Row(
                                    controls=[
                                        ft.Text("File:", size=11, color=TEXT_MUTED, width=50),
                                        ft.Text(fn, size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                    ],
                                ),
                                ft.Row(
                                    controls=[
                                        ft.Text("Dimensione:", size=11, color=TEXT_MUTED, width=70),
                                        ft.Text(fsize, size=11, color=TEXT_SECONDARY),
                                    ],
                                ),
                            ],
                        ),
                    ),
                    # Calculation progress
                    self.progress_bar,
                    self.status_text,
                    # Hash results
                    self.md5_field,
                    self.sha1_field,
                    self.sha256_field,
                    ft.Divider(color=BORDER_COLOR, height=1),
                    # Compare Section
                    ft.Text("Confronta con hash atteso:", size=11, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
                    self.compare_input,
                    self.match_badge,
                ],
            ),
        )

        self.actions = [
            ft.TextButton(
                content="Chiudi",
                on_click=self._handle_close,
                style=ft.ButtonStyle(color=TEXT_SECONDARY),
            )
        ]
        self.actions_alignment = ft.MainAxisAlignment.END

    def _build_hash_field(self, label: str, initial_value: str) -> ft.Container:
        text_ctrl = ft.Text(
            initial_value,
            size=11,
            color=TEXT_PRIMARY,
            weight=ft.FontWeight.W_500,
            expand=True,
            selectable=True,
            no_wrap=True,
            overflow=ft.TextOverflow.ELLIPSIS,
        )
        copy_btn = ft.IconButton(
            icon=ft.Icons.COPY_ROUNDED,
            icon_size=14,
            icon_color=TEXT_MUTED,
            tooltip=f"Copia hash {label}",
            on_click=lambda e: self.coordinator.copy_to_clipboard(text_ctrl.value),
        )
        ctrl = ft.Container(
            bgcolor=SURFACE_CARD,
            border_radius=6,
            border=ft.Border.all(1, BORDER_COLOR),
            padding=ft.Padding(left=8, right=4, top=4, bottom=4),
            content=ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(
                        width=60,
                        content=ft.Text(label, size=11, weight=ft.FontWeight.BOLD, color=ACCENT_LIGHT),
                    ),
                    text_ctrl,
                    copy_btn,
                ],
            ),
        )
        # Store reference to text_ctrl
        ctrl._text_ctrl = text_ctrl
        return ctrl

    def show(self, page: ft.Page) -> None:
        """Shows the dialog and launches the async hash computation."""
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

        asyncio.create_task(self._compute_hashes_async())

    async def _compute_hashes_async(self) -> None:
        if not self.file_path or not self.file_path.exists():
            self.progress_bar.visible = False
            self.status_text.value = f"File non trovato su disco ({self.file_path})"
            self.status_text.color = COLOR_ERROR
            safe_update(self)
            return

        def _progress(ratio: float) -> None:
            self.progress_bar.value = ratio
            self.status_text.value = f"Calcolo hash in corso... {int(ratio * 100)}%"
            safe_update(self.progress_bar)
            safe_update(self.status_text)

        try:
            hashes = await asyncio.to_thread(compute_file_hashes, self.file_path, _progress)
            self._computed_hashes = hashes
            self._is_computing = False

            # Update fields
            self.md5_field._text_ctrl.value = hashes["md5"]
            self.sha1_field._text_ctrl.value = hashes["sha1"]
            self.sha256_field._text_ctrl.value = hashes["sha256"]

            self.progress_bar.visible = False
            self.status_text.value = "Calcolo completato con successo."
            self.status_text.color = COLOR_SUCCESS

            # Check if user already typed an expected hash
            if self.compare_input.value:
                self._validate_hash_comparison(self.compare_input.value)

            safe_update(self)
        except Exception as exc:
            self.progress_bar.visible = False
            self.status_text.value = f"Errore durante il calcolo: {exc}"
            self.status_text.color = COLOR_ERROR
            safe_update(self)

    def _on_compare_change(self, e: ft.ControlEvent) -> None:
        val = e.control.value.strip() if e.control.value else ""
        self._validate_hash_comparison(val)

    def _validate_hash_comparison(self, entered_val: str) -> None:
        val = entered_val.strip().lower()
        if not val or not self._computed_hashes:
            self.match_badge.visible = False
            safe_update(self.match_badge)
            return

        matched_algo = None
        for algo, h in self._computed_hashes.items():
            if val == h:
                matched_algo = algo.upper()
                break

        if matched_algo:
            self.match_badge.visible = True
            self.match_badge.bgcolor = "rgba(16, 185, 129, 0.15)"
            self.match_badge.border = ft.Border.all(1, "#10B981")
            self.match_badge.border_radius = 6
            self.match_badge.padding = ft.Padding(8, 4, 8, 4)
            self.match_badge.content = ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, color="#10B981", size=16),
                    ft.Text(f"✅ Hash corrispondente al checksum {matched_algo}", color="#10B981", size=11, weight=ft.FontWeight.BOLD),
                ],
            )
        else:
            self.match_badge.visible = True
            self.match_badge.bgcolor = "rgba(239, 68, 68, 0.15)"
            self.match_badge.border = ft.Border.all(1, "#EF4444")
            self.match_badge.border_radius = 6
            self.match_badge.padding = ft.Padding(8, 4, 8, 4)
            self.match_badge.content = ft.Row(
                spacing=6,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.CANCEL_ROUNDED, color="#EF4444", size=16),
                    ft.Text("❌ Hash non corrispondente (il file potrebbe essere corrotto o modificato)", color="#EF4444", size=11, weight=ft.FontWeight.BOLD),
                ],
            )

        safe_update(self.match_badge)

    def _handle_close(self, e: Optional[ft.ControlEvent] = None) -> None:
        if self.page:
            if hasattr(self.page, "close"):
                self.page.close(self)
            else:
                self.open = False
                safe_update(self.page)
