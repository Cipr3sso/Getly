import asyncio
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional
import flet as ft
import pyperclip

from core.engine.site_grabber import SiteGrabber, GrabbedResource
from core.scheduler.categories import (
    CATEGORY_COMPRESSED,
    CATEGORY_DOCUMENTS,
    CATEGORY_VIDEO,
    CATEGORY_AUDIO,
    CATEGORY_IMAGES_ISO,
    CATEGORY_COLORS,
    get_category_subfolder,
)
from ui.coordinator import UICoordinator
from ui.components.download_card import get_file_icon
from ui.components.add_download_dialog import _pick_directory_sync
from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    build_fluent_text_field,
    safe_update,
    safe_page_update,
)


class SiteGrabberDialog(ft.AlertDialog):
    """
    Windows 11 Fluent Dark Site Explorer & Web Grabber modal dialog:
    Analyzes any webpage, lists discovered files, supports category batch filters,
    and enqueues selected files with automatic folder organization.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_added: Optional[Callable[[], None]] = None,
        initial_url: Optional[str] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_added = on_added
        self.grabber = SiteGrabber()

        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        self.discovered_items: List[GrabbedResource] = []
        self.selected_urls: Dict[str, bool] = {}  # url -> is_checked
        self.checkbox_controls: Dict[str, ft.Checkbox] = {}

        # Default destination folder is base directory
        self.base_dir = self.coordinator.queue_manager.default_download_dir

        # Title
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
                    content=ft.Icon(ft.Icons.PUBLIC_ROUNDED, color=ACCENT_BLUE, size=18),
                ),
                ft.Column(
                    spacing=1,
                    tight=True,
                    controls=[
                        ft.Text("Site Explorer & Web Grabber", weight=ft.FontWeight.BOLD, size=15, color=TEXT_PRIMARY),
                        ft.Text("Esplora e scarica file da qualsiasi pagina web", size=11, color=TEXT_SECONDARY),
                    ],
                ),
            ],
        )

        # 1. URL Input Bar + Analyze Button
        self.url_field = build_fluent_text_field(
            label="URL Pagina Web da analizzare",
            hint_text="https://esempio.com/pagina",
            value=initial_url or "",
            autofocus=True,
            expand=True,
        )

        self.analyze_btn = ft.FilledButton(
            icon=ft.Icons.SEARCH_ROUNDED,
            content="Analizza Pagina",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=self._handle_analyze,
        )

        url_row = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.url_field,
                self.analyze_btn,
            ],
        )

        # 2. Loading Indicator & Status
        self.status_text = ft.Text("Inserisci un URL e clicca su 'Analizza Pagina' per iniziare.", size=12, color=TEXT_SECONDARY)
        self.loading_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_BLUE, visible=False)
        status_row = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[self.loading_ring, self.status_text],
        )

        # 3. Batch Action Filter Buttons
        self.btn_select_all = self._build_pill_btn("Tutti", lambda e: self._select_by_filter("ALL"))
        self.btn_archives = self._build_pill_btn("Solo Archivi", lambda e: self._select_by_filter("ARCHIVES"))
        self.btn_documents = self._build_pill_btn("Solo PDF/Doc", lambda e: self._select_by_filter("DOCS"))
        self.btn_media = self._build_pill_btn("Solo Media", lambda e: self._select_by_filter("MEDIA"))
        self.btn_invert = self._build_pill_btn("Inverti", lambda e: self._select_by_filter("INVERT"))

        self.selection_summary = ft.Text("Trovati: 0 | Selezionati: 0", size=11, color=TEXT_MUTED)

        batch_toolbar = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=6,
                    tight=True,
                    controls=[
                        self.btn_select_all,
                        self.btn_archives,
                        self.btn_documents,
                        self.btn_media,
                        self.btn_invert,
                    ],
                ),
                self.selection_summary,
            ],
        )

        # 4. Results List View
        self.results_list = ft.ListView(
            expand=True,
            spacing=4,
            height=260,
        )

        # 5. Destination Folder with Native Browse
        self.dir_field = build_fluent_text_field(
            label="Cartella Base per il Download",
            value=str(self.base_dir),
            expand=True,
        )

        browse_btn = ft.OutlinedButton(
            icon=ft.Icons.FOLDER_OPEN_ROUNDED,
            content="Sfoglia...",
            on_click=self._handle_browse_dir,
            style=ft.ButtonStyle(
                color=TEXT_PRIMARY,
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, BORDER_COLOR),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
        )

        dest_row = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[self.dir_field, browse_btn],
        )

        # Full layout inside container
        self.content = ft.Container(
            width=760,
            padding=ft.Padding(top=8, bottom=8, left=4, right=4),
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=[
                    url_row,
                    status_row,
                    batch_toolbar,
                    ft.Container(
                        bgcolor=SURFACE_CARD,
                        border_radius=8,
                        border=ft.Border.all(1, BORDER_COLOR),
                        padding=ft.Padding(left=8, right=8, top=6, bottom=6),
                        content=self.results_list,
                    ),
                    dest_row,
                ],
            ),
        )

        # Dialog Actions
        cancel_btn = ft.TextButton(
            content="Chiudi",
            style=ft.ButtonStyle(color=TEXT_SECONDARY),
            on_click=self._handle_close,
        )

        self.download_btn = ft.FilledButton(
            icon=ft.Icons.DOWNLOAD_ROUNDED,
            content="Scarica Selezionati (0)",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=self._handle_download_selected,
        )

        self.actions = [cancel_btn, self.download_btn]
        self.actions_alignment = ft.MainAxisAlignment.END

    def _build_pill_btn(self, label: str, on_click: Callable) -> ft.Control:
        return ft.OutlinedButton(
            content=label,
            style=ft.ButtonStyle(
                color=TEXT_PRIMARY,
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, BORDER_COLOR),
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(8, 4, 8, 4),
            ),
            on_click=on_click,
        )

    def _handle_browse_dir(self, e: ft.ControlEvent) -> None:
        curr_dir = self.dir_field.value.strip() if self.dir_field.value else ""

        async def _browse():
            chosen = await asyncio.to_thread(_pick_directory_sync, curr_dir)
            if chosen:
                self.dir_field.value = chosen
                if e.page:
                    e.page.update()

        if e.page:
            e.page.run_task(_browse)

    def _handle_analyze(self, e: ft.ControlEvent) -> None:
        url = self.url_field.value.strip() if self.url_field.value else ""
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            self.status_text.value = "⚠️ Inserisci un URL HTTP/HTTPS valido."
            self.status_text.color = "#EF4444"
            if e.page:
                e.page.update()
            return

        async def _do_crawl():
            self.loading_ring.visible = True
            self.status_text.value = f"Analisi di {url} in corso..."
            self.status_text.color = TEXT_SECONDARY
            self.results_list.controls.clear()
            if e.page:
                e.page.update()

            try:
                resources = await self.grabber.crawl_page(url)
                self.discovered_items = resources
                self.selected_urls.clear()
                self.checkbox_controls.clear()

                for res in resources:
                    self.selected_urls[res.url] = True

                self.loading_ring.visible = False
                if not resources:
                    self.status_text.value = "Nessun file scaricabile trovato nella pagina."
                    self.status_text.color = "#F59E0B"
                else:
                    self.status_text.value = f"✅ Trovati {len(resources)} file unici!"
                    self.status_text.color = "#10B981"
                    self._populate_results()

            except Exception as exc:
                self.loading_ring.visible = False
                self.status_text.value = f"Errore durante l'analisi: {exc}"
                self.status_text.color = "#EF4444"

            self._update_selection_summary()
            if e.page:
                e.page.update()

        if e.page:
            e.page.run_task(_do_crawl)

    def _populate_results(self) -> None:
        self.results_list.controls.clear()
        for res in self.discovered_items:
            cb = ft.Checkbox(
                value=self.selected_urls.get(res.url, False),
                active_color=ACCENT_BLUE,
                on_change=lambda ev, u=res.url: self._on_checkbox_changed(u, ev.control.value),
            )
            self.checkbox_controls[res.url] = cb

            file_icon = get_file_icon(res.filename)
            cat_color = CATEGORY_COLORS.get(res.category, "#948FA8")

            cat_pill = ft.Container(
                bgcolor=ft.Colors.with_opacity(0.15, cat_color),
                border=ft.Border.all(1, ft.Colors.with_opacity(0.35, cat_color)),
                border_radius=8,
                padding=ft.Padding(6, 2, 6, 2),
                content=ft.Text(res.category, size=10, color=cat_color, weight=ft.FontWeight.BOLD),
            )

            row = ft.Container(
                bgcolor="rgba(255, 255, 255, 0.02)",
                border_radius=6,
                padding=ft.Padding(left=8, right=8, top=4, bottom=4),
                content=ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                cb,
                                ft.Icon(file_icon, size=18, color=ACCENT_BLUE),
                                ft.Column(
                                    spacing=2,
                                    controls=[
                                        ft.Text(res.filename, weight=ft.FontWeight.W_500, size=12, color=TEXT_PRIMARY),
                                        ft.Text(res.url, size=10, color=TEXT_MUTED, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, width=420),
                                    ],
                                ),
                            ],
                        ),
                        cat_pill,
                    ],
                ),
            )
            self.results_list.controls.append(row)

    def _on_checkbox_changed(self, url: str, is_checked: bool) -> None:
        self.selected_urls[url] = is_checked
        self._update_selection_summary()
        safe_page_update(self)

    def _select_by_filter(self, filter_type: str) -> None:
        for res in self.discovered_items:
            url = res.url
            if filter_type == "ALL":
                self.selected_urls[url] = True
            elif filter_type == "ARCHIVES":
                self.selected_urls[url] = (res.category == CATEGORY_COMPRESSED)
            elif filter_type == "DOCS":
                self.selected_urls[url] = (res.category == CATEGORY_DOCUMENTS)
            elif filter_type == "MEDIA":
                self.selected_urls[url] = (res.category in (CATEGORY_VIDEO, CATEGORY_AUDIO, CATEGORY_IMAGES_ISO))
            elif filter_type == "INVERT":
                self.selected_urls[url] = not self.selected_urls.get(url, False)

            if url in self.checkbox_controls:
                self.checkbox_controls[url].value = self.selected_urls[url]

        self._update_selection_summary()
        safe_page_update(self)

    def _update_selection_summary(self) -> None:
        total = len(self.discovered_items)
        selected = sum(1 for val in self.selected_urls.values() if val)
        self.selection_summary.value = f"Trovati: {total} | Selezionati: {selected}"
        self.download_btn.content = f"Scarica Selezionati ({selected})"

    def _handle_close(self, e: ft.ControlEvent) -> None:
        if e.page:
            e.page.pop_dialog()

    def _handle_download_selected(self, e: ft.ControlEvent) -> None:
        selected_items = [res for res in self.discovered_items if self.selected_urls.get(res.url, False)]
        if not selected_items:
            self.status_text.value = "⚠️ Nessun file selezionato per il download."
            self.status_text.color = "#F59E0B"
            if e.page:
                e.page.update()
            return

        base_dest = Path(self.dir_field.value.strip()) if self.dir_field.value else self.base_dir

        async def _add_all():
            for res in selected_items:
                cat_subfolder = get_category_subfolder(base_dest, res.category)
                await self.coordinator.add_download(
                    url=res.url,
                    destination_dir=cat_subfolder,
                    filename=res.filename,
                    category=res.category,
                    start_paused=False,
                )
            if self.on_added:
                self.on_added()

        if e.page:
            e.page.run_task(_add_all)
            e.page.pop_dialog()
