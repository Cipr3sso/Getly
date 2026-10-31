"""
Task Details Dialog for Getly (Windows 11 Fluent Dark).
Displays complete technical details, network parameters, and individual file progress
for BitTorrent downloads or deep HTTP streaming metrics.
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
import flet as ft

from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.categories import CATEGORY_COLORS
from ui.components.download_card import get_file_icon, STATUS_CONFIG
from ui.coordinator import UICoordinator, format_bytes, format_speed
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
    COLOR_SUCCESS,
    COLOR_ERROR,
    build_gradient_progress_bar,
    safe_update,
)


class TaskDetailsDialog(ft.AlertDialog):
    """
    WinUI 3 Dark Dialog for inspecting comprehensive details of a download task.
    - BitTorrent: Live individual file progress tree with on-the-fly priority selection,
      hash, seeds/peers, ratio, and strict VPN/interface binding details.
    - HTTP/HTTPS: Source URL, destination file, multi-stream chunks, ETA,
      custom headers (Referer, User-Agent, masked Cookies).
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

        # File search filter for torrents
        self._file_filter_query = ""
        self._files_data: List[Dict[str, Any]] = []
        self._ticker_active = False

        # Live UI references for periodic updates
        self._live_speed_text: Optional[ft.Text] = None
        self._live_upload_text: Optional[ft.Text] = None
        self._live_progress_text: Optional[ft.Text] = None
        self._live_downloaded_text: Optional[ft.Text] = None
        self._live_seeds_text: Optional[ft.Text] = None
        self._live_eta_text: Optional[ft.Text] = None
        self._file_rows_column = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)

        self.title = self._build_header()
        self.content = ft.Container(
            width=740,
            height=500,
            content=self._build_body(),
        )

        self.actions = [
            ft.TextButton(
                content=ft.Text("Chiudi", color=TEXT_PRIMARY),
                on_click=self._handle_close,
            )
        ]
        self.actions_alignment = ft.MainAxisAlignment.END

    def did_mount(self):
        super().did_mount()
        # Start periodic live metrics update ticker if task is downloading
        if self.task.status == DownloadStatus.DOWNLOADING:
            self._ticker_active = True
            asyncio.create_task(self._live_update_loop())

    def show(self, page: ft.Page) -> None:
        """Opens this dialog safely on the target page."""
        if not page:
            return
        if hasattr(page, "show_dialog"):
            page.show_dialog(self)
        elif hasattr(page, "open"):
            page.open(self)
        else:
            page.dialog = self
            self.open = True
            safe_update(page)

    def _handle_close(self, e: Optional[ft.ControlEvent] = None) -> None:
        self._ticker_active = False
        page = None
        try:
            page = self.page
        except (RuntimeError, Exception):
            page = None

        if page:
            try:
                if hasattr(page, "pop_dialog"):
                    page.pop_dialog()
                elif hasattr(page, "close"):
                    page.close(self)
                else:
                    self.open = False
                    safe_update(page)
            except Exception:
                pass

    def _build_header(self) -> ft.Control:
        is_torrent = (self.task.task_type == "TORRENT")
        icon_name = ft.Icons.HUB_ROUNDED if is_torrent else ft.Icons.LANGUAGE_ROUNDED
        icon_color = "#38BDF8" if is_torrent else ACCENT_LIGHT

        st_cfg = STATUS_CONFIG.get(self.task.status, STATUS_CONFIG[DownloadStatus.DOWNLOADING])
        status_badge = ft.Container(
            bgcolor=st_cfg["bg"],
            border=ft.Border.all(1, st_cfg["border"]),
            border_radius=6,
            padding=ft.Padding(8, 2, 8, 2),
            content=ft.Text(st_cfg["text"], size=11, color=st_cfg["color"], weight=ft.FontWeight.BOLD),
        )

        display_name = self.task.filename or self.task.url

        return ft.Container(
            padding=ft.Padding(0, 0, 0, 4),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Row(
                        spacing=10,
                        tight=True,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(icon_name, size=24, color=icon_color),
                            ft.Column(
                                spacing=2,
                                expand=True,
                                controls=[
                                    ft.Text(
                                        display_name,
                                        size=15,
                                        weight=ft.FontWeight.BOLD,
                                        color=TEXT_PRIMARY,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Row(
                                        spacing=8,
                                        tight=True,
                                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                        controls=[
                                            status_badge,
                                            ft.TextButton(
                                                content=ft.Row(
                                                    spacing=4,
                                                    tight=True,
                                                    controls=[
                                                        ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=14, color=ACCENT_LIGHT),
                                                        ft.Text("Apri Cartella", size=11, color=ACCENT_LIGHT),
                                                    ],
                                                ),
                                                on_click=lambda e: self.coordinator.open_folder(self.task.destination_dir),
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                        ],
                    ),
                    ft.IconButton(
                        icon=ft.Icons.CLOSE_ROUNDED,
                        icon_size=18,
                        icon_color=TEXT_SECONDARY,
                        tooltip="Chiudi",
                        on_click=self._handle_close,
                    ),
                ],
            ),
        )

    def _build_body(self) -> ft.Control:
        if self.task.task_type == "TORRENT":
            return self._build_torrent_view()
        return self._build_http_view()

    # =========================================================================
    # TORRENT VIEW & INDIVIDUAL FILE PROGRESS
    # =========================================================================

    def _build_torrent_view(self) -> ft.Control:
        vpn_ips = getattr(self.coordinator.session_pool, "active_adapters", [])
        vpn_adapter = next((a for a in vpn_ips if getattr(a, "is_vpn", False)), None)
        net_str = f"🛡️ VPN Shield: {vpn_adapter.name} ({vpn_adapter.ip})" if vpn_adapter else "🌐 Multi-Interface / Diretto"

        ratio_pct = f"{int(self.task.progress_ratio * 100)}%"
        self._live_downloaded_text = ft.Text(format_bytes(self.task.downloaded_bytes), size=12, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD)
        self._live_progress_text = ft.Text(ratio_pct, size=12, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD)
        self._live_speed_text = ft.Text(format_speed(self.task.speed_bps), size=12, color=COLOR_SUCCESS, weight=ft.FontWeight.BOLD)
        self._live_upload_text = ft.Text(format_speed(self.task.upload_speed_bps), size=12, color="#38BDF8", weight=ft.FontWeight.BOLD)
        self._live_seeds_text = ft.Text(f"{self.task.seeds} seeds • {self.task.peers} peers", size=12, color=TEXT_PRIMARY)

        # 1. Top Metrics Summary Card
        metrics_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            self._metric_cell("Dimensione Totale", format_bytes(self.task.total_size)),
                            self._metric_cell("Byte Scaricati", self._live_downloaded_text),
                            self._metric_cell("Avanzamento", self._live_progress_text),
                            self._metric_cell("Velocità Download", self._live_speed_text),
                            self._metric_cell("Velocità Upload", self._live_upload_text),
                        ],
                    ),
                    ft.Divider(color=BORDER_COLOR, height=1),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            self._metric_cell("Seeds / Peers", self._live_seeds_text),
                            self._metric_cell("Info Hash", self.task.info_hash[:16] + "..." if self.task.info_hash else "--", tooltip=self.task.info_hash),
                            self._metric_cell("Rete & Privacy", net_str),
                        ],
                    ),
                ],
            ),
        )

        # 2. File list table with filter bar
        self._refresh_torrent_files_data()

        search_field = ft.TextField(
            hint_text="Filtra file nel torrent...",
            hint_style=ft.TextStyle(size=11, color=TEXT_MUTED),
            text_style=ft.TextStyle(size=11, color=TEXT_PRIMARY),
            bgcolor=SURFACE_CARD,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            border_radius=6,
            dense=True,
            content_padding=ft.Padding(8, 4, 8, 4),
            expand=True,
            on_change=self._on_search_query_changed,
        )

        files_header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text(f"File contenuti nel Torrent ({len(self._files_data)})", size=13, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                ft.Container(width=280, content=search_field),
            ],
        )

        table_header = ft.Container(
            bgcolor=SURFACE_CARD,
            padding=ft.Padding(6, 6, 6, 6),
            border_radius=6,
            content=ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(width=50, content=ft.Text("Scarica", size=10, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY)),
                    ft.Container(expand=4, content=ft.Text("Nome File", size=10, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY)),
                    ft.Container(expand=1, content=ft.Text("Dimensione", size=10, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY)),
                    ft.Container(expand=1, content=ft.Text("Scaricati", size=10, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY)),
                    ft.Container(expand=2, content=ft.Text("Progresso %", size=10, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY)),
                    ft.Container(width=80, content=ft.Text("Priorità", size=10, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY)),
                ],
            ),
        )

        self._render_torrent_file_rows()

        return ft.Column(
            expand=True,
            spacing=8,
            controls=[
                metrics_card,
                files_header,
                table_header,
                ft.Container(
                    expand=True,
                    border=ft.Border.all(1, BORDER_COLOR),
                    border_radius=6,
                    padding=4,
                    content=self._file_rows_column,
                ),
            ],
        )

    def _refresh_torrent_files_data(self) -> None:
        """Fetches latest file progress from coordinator / queue manager."""
        files = self.coordinator.get_torrent_files_status(self.task.task_id)
        if not files and self.task.files_status:
            files = self.task.files_status
        self._files_data = files or []

    def _render_torrent_file_rows(self) -> None:
        self._file_rows_column.controls.clear()

        query = self._file_filter_query.lower().strip()
        matching_files = [
            f for f in self._files_data
            if not query or query in f.get("path", "").lower()
        ]

        if not matching_files:
            empty_msg = "Nessun file corrispondente al filtro." if query else "Nessun file rilevato o metadati in caricamento..."
            self._file_rows_column.controls.append(
                ft.Container(
                    alignment=ft.Alignment(0, 0),
                    padding=20,
                    content=ft.Text(empty_msg, size=11, color=TEXT_MUTED),
                )
            )
            return

        for f in matching_files:
            idx = f.get("index", 0)
            path = f.get("path", "")
            size = f.get("size", 0)
            downloaded = f.get("downloaded", 0)
            progress = f.get("progress", 0.0)
            prio = f.get("priority", 4)
            is_enabled = (prio > 0)

            file_icon = get_file_icon(path)

            cb = ft.Checkbox(
                value=is_enabled,
                active_color=ACCENT_BLUE,
                on_change=lambda e, i=idx: self._handle_file_priority_toggle(i, e.control.value),
            )

            prog_pct = f"{int(progress * 100)}%"
            prio_text = "Massima" if prio >= 7 else ("Normale" if prio >= 4 else ("Bassa" if prio >= 1 else "Escluso"))
            prio_color = ACCENT_LIGHT if prio >= 7 else (TEXT_PRIMARY if prio >= 4 else (TEXT_MUTED if prio >= 1 else COLOR_ERROR))

            row = ft.Container(
                bgcolor="transparent",
                border_radius=4,
                padding=ft.Padding(4, 2, 4, 2),
                content=ft.Row(
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(width=50, content=cb),
                        ft.Container(
                            expand=4,
                            content=ft.Row(
                                spacing=6,
                                tight=True,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Icon(file_icon, size=14, color=ACCENT_BLUE),
                                    ft.Text(path, size=11, color=TEXT_PRIMARY, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                                ],
                            ),
                        ),
                        ft.Container(expand=1, content=ft.Text(format_bytes(size), size=11, color=TEXT_SECONDARY)),
                        ft.Container(expand=1, content=ft.Text(format_bytes(downloaded), size=11, color=TEXT_SECONDARY)),
                        ft.Container(
                            expand=2,
                            content=ft.Row(
                                spacing=6,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Container(
                                        expand=True,
                                        content=build_gradient_progress_bar(progress, "DOWNLOADING" if progress < 1.0 else "COMPLETED"),
                                    ),
                                    ft.Text(prog_pct, size=10, color=TEXT_PRIMARY, width=32),
                                ],
                            ),
                        ),
                        ft.Container(width=80, content=ft.Text(prio_text, size=10, color=prio_color, weight=ft.FontWeight.W_500)),
                    ],
                ),
            )
            self._file_rows_column.controls.append(row)

    def _handle_file_priority_toggle(self, file_index: int, is_checked: bool) -> None:
        new_prio = 4 if is_checked else 0
        self.coordinator.set_file_priority(self.task.task_id, file_index, new_prio)
        self._refresh_torrent_files_data()
        self._render_torrent_file_rows()
        safe_update(self._file_rows_column)

    def _on_search_query_changed(self, e: ft.ControlEvent) -> None:
        self._file_filter_query = e.control.value or ""
        self._render_torrent_file_rows()
        safe_update(self._file_rows_column)

    # =========================================================================
    # HTTP / HTTPS VIEW
    # =========================================================================

    def _build_http_view(self) -> ft.Control:
        eta_val = self.coordinator.task_etas.get(self.task.task_id, "--")
        self._live_speed_text = ft.Text(format_speed(self.task.speed_bps), size=12, color=COLOR_SUCCESS, weight=ft.FontWeight.BOLD)
        self._live_downloaded_text = ft.Text(format_bytes(self.task.downloaded_bytes), size=12, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD)
        self._live_progress_text = ft.Text(f"{int(self.task.progress_ratio * 100)}%", size=12, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD)
        self._live_eta_text = ft.Text(eta_val, size=12, color=TEXT_PRIMARY)

        # 1. URL and Target File Info Card
        dest_full = str(self.task.destination_dir / (self.task.filename or ""))
        cat = self.task.category or "Altro"
        cat_color = CATEGORY_COLORS.get(cat, "#948FA8")

        info_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("URL Sorgente:", size=11, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY),
                            ft.IconButton(
                                icon=ft.Icons.COPY_ROUNDED,
                                icon_size=14,
                                icon_color=ACCENT_LIGHT,
                                tooltip="Copia URL negli appunti",
                                on_click=lambda e: self.coordinator.copy_to_clipboard(self.task.url),
                            ),
                        ],
                    ),
                    ft.Text(self.task.url, size=11, color=TEXT_PRIMARY, selectable=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Divider(color=BORDER_COLOR, height=1),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("Percorso su disco:", size=11, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY),
                            ft.TextButton(
                                content=ft.Row(
                                    spacing=4,
                                    tight=True,
                                    controls=[
                                        ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=14, color=ACCENT_LIGHT),
                                        ft.Text("Mostra file", size=11, color=ACCENT_LIGHT),
                                    ],
                                ),
                                on_click=lambda e: self.coordinator.open_folder(self.task.destination_dir),
                            ),
                        ],
                    ),
                    ft.Text(dest_full, size=11, color=TEXT_PRIMARY, selectable=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                ],
            ),
        )

        # 2. Transfer Metrics Card
        multi_stream_str = "Supportato (Accept-Ranges attivo)" if self.task.total_size > 0 else "Singolo Stream Fallback"
        metrics_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            self._metric_cell("Dimensione Totale", format_bytes(self.task.total_size)),
                            self._metric_cell("Byte Scaricati", self._live_downloaded_text),
                            self._metric_cell("Avanzamento", self._live_progress_text),
                            self._metric_cell("Velocità Corrente", self._live_speed_text),
                            self._metric_cell("Tempo Rimanente (ETA)", self._live_eta_text),
                        ],
                    ),
                    ft.Divider(color=BORDER_COLOR, height=1),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            self._metric_cell("Multi-Stream Chunks", multi_stream_str),
                            self._metric_cell("Architettura Socket", "Winsock IP_UNICASTIF Bind (IPv4)"),
                            self._metric_cell("Categoria", cat, color=cat_color),
                        ],
                    ),
                ],
            ),
        )

        # 3. Custom Headers & Security Card
        headers_card = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(12, 10, 12, 10),
            content=ft.Column(
                spacing=6,
                controls=[
                    ft.Text("Parametri Connessione & Header HTTP:", size=11, weight=ft.FontWeight.BOLD, color=TEXT_SECONDARY),
                    self._header_row("Referer:", self.task.referer or "Nessuno"),
                    self._header_row("User-Agent:", self.task.user_agent or "Getly/1.0 (Windows NT 10.0; Win64; x64)"),
                    self._header_row(
                        "Cookie Sessione:",
                        f"{len(self.task.cookies)} caratteri (mascherati per sicurezza)" if self.task.cookies else "Nessuno",
                    ),
                ],
            ),
        )

        # Error banner if failed
        error_banner = None
        if self.task.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED) and self.task.error_message:
            error_banner = ft.Container(
                bgcolor="rgba(239, 68, 68, 0.15)",
                border=ft.Border.all(1, "rgba(239, 68, 68, 0.4)"),
                border_radius=6,
                padding=ft.Padding(10, 8, 10, 8),
                content=ft.Row(
                    spacing=8,
                    controls=[
                        ft.Icon(ft.Icons.ERROR_OUTLINE_ROUNDED, size=18, color=COLOR_ERROR),
                        ft.Text(f"Errore: {self.task.error_message}", size=11, color=COLOR_ERROR, expand=True),
                    ],
                ),
            )

        body_controls = [info_card, metrics_card, headers_card]
        if error_banner:
            body_controls.insert(0, error_banner)

        return ft.Column(
            expand=True,
            spacing=8,
            scroll=ft.ScrollMode.AUTO,
            controls=body_controls,
        )

    # =========================================================================
    # HELPERS & PERIODIC UPDATE LOOP
    # =========================================================================

    def _metric_cell(self, label: str, val: Any, color: Optional[str] = None, tooltip: Optional[str] = None) -> ft.Control:
        content_ctrl = val if isinstance(val, ft.Control) else ft.Text(str(val), size=12, color=color or TEXT_PRIMARY, weight=ft.FontWeight.BOLD, no_wrap=True)
        return ft.Column(
            spacing=2,
            tight=True,
            controls=[
                ft.Text(label, size=10, color=TEXT_MUTED, no_wrap=True),
                ft.Container(content=content_ctrl, tooltip=tooltip),
            ],
        )

    def _header_row(self, label: str, value: str) -> ft.Control:
        return ft.Row(
            spacing=6,
            controls=[
                ft.Text(label, size=10, color=TEXT_MUTED, width=120),
                ft.Text(value, size=10, color=TEXT_PRIMARY, expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ],
        )

    async def _live_update_loop(self) -> None:
        """Periodically updates speeds, file progress, and ETA while download is active."""
        while self._ticker_active:
            try:
                await asyncio.sleep(0.5)
                if not self._ticker_active:
                    break

                # Update live speeds
                if self._live_speed_text:
                    self._live_speed_text.value = format_speed(self.task.speed_bps)
                    safe_update(self._live_speed_text)

                if self._live_upload_text:
                    self._live_upload_text.value = format_speed(self.task.upload_speed_bps)
                    safe_update(self._live_upload_text)

                if self._live_progress_text:
                    self._live_progress_text.value = f"{int(self.task.progress_ratio * 100)}%"
                    safe_update(self._live_progress_text)

                if self._live_downloaded_text:
                    self._live_downloaded_text.value = format_bytes(self.task.downloaded_bytes)
                    safe_update(self._live_downloaded_text)

                if self._live_seeds_text:
                    self._live_seeds_text.value = f"{self.task.seeds} seeds • {self.task.peers} peers"
                    safe_update(self._live_seeds_text)

                if self._live_eta_text:
                    self._live_eta_text.value = self.coordinator.task_etas.get(self.task.task_id, "--")
                    safe_update(self._live_eta_text)

                # For torrents, refresh individual file rows
                if self.task.task_type == "TORRENT":
                    self._refresh_torrent_files_data()
                    self._render_torrent_file_rows()
                    safe_update(self._file_rows_column)

                # Stop ticker if task has completed or failed
                if self.task.status != DownloadStatus.DOWNLOADING:
                    self._ticker_active = False
                    break
            except Exception:
                pass
