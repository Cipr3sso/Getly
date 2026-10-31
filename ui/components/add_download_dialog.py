import asyncio
import os
from pathlib import Path
from typing import Callable, Optional
import flet as ft
import pyperclip

from core.scheduler.categories import (
    ALL_CATEGORIES,
    CATEGORY_OTHER,
    detect_category,
    get_category_subfolder,
    CATEGORY_COLORS,
)
from core.extensions.models import ResolvedFile, ResolvedResource
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
    build_fluent_text_field,
)


def _pick_directory_sync(initial_dir: Optional[str] = None) -> Optional[str]:
    """Native Windows folder picker using Tkinter executed inside a worker thread."""
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            folder = filedialog.askdirectory(
                initialdir=initial_dir or os.path.expanduser("~"),
                title="Seleziona Cartella di Salvataggio",
            )
            return folder if folder else None
        finally:
            try:
                root.destroy()
            except Exception:
                pass
    except Exception:
        return None


class AddDownloadDialog(ft.AlertDialog):
    """
    Windows 11 Fluent Dark IDM-style confirmation modal:
    - Editable filename
    - Category selection with automatic subfolder routing
    - Full destination path with native 'Sfoglia...' browse button
    - Dual execution modes: 'Scarica Ora' or 'Aggiungi in Coda (In Pausa)'
    - Dark theme: #202020 background, #2B2B2B surface, #FFFFFF text, #3D3D3D border
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_added: Optional[Callable[[], None]] = None,
        prefill_url: Optional[str] = None,
        prefill_filename: Optional[str] = None,
        prefill_category: Optional[str] = None,
        prefill_dir: Optional[Path] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        user_agent: Optional[str] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_added = on_added
        self.cookies = cookies
        self.referer = referer
        self.user_agent = user_agent

        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        # Detect initial filename and category
        initial_url = prefill_url or ""
        initial_fn = prefill_filename
        if not initial_fn and initial_url:
            raw = initial_url.split("/")[-1].split("?")[0] if "/" in initial_url else ""
            if raw and "." in raw:
                initial_fn = raw

        detected_cat = prefill_category or detect_category(initial_fn or initial_url)
        self.current_category = detected_cat if detected_cat in ALL_CATEGORIES else CATEGORY_OTHER

        base_download_dir = self.coordinator.queue_manager.default_download_dir
        if prefill_dir:
            initial_dest = str(prefill_dir)
        else:
            initial_dest = str(get_category_subfolder(base_download_dir, self.current_category))

        # Title
        self.title = ft.Row(
            spacing=10,
            controls=[
                ft.Container(
                    width=32,
                    height=32,
                    border_radius=16,
                    bgcolor="rgba(0, 120, 212, 0.15)",
                    border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                    alignment=ft.Alignment(0, 0),
                    content=ft.Icon(ft.Icons.DOWNLOAD_ROUNDED, color=ACCENT_BLUE, size=18),
                ),
                ft.Column(
                    spacing=1,
                    tight=True,
                    controls=[
                        ft.Text("Conferma Download", weight=ft.FontWeight.BOLD, size=15, color=TEXT_PRIMARY),
                        ft.Text("Net-Merge Multi-Interface Engine", size=11, color=TEXT_SECONDARY),
                    ],
                ),
            ],
        )

        # 1. URL Field
        self.url_field = build_fluent_text_field(
            label="URL File da scaricare",
            hint_text="https://esempio.com/file.zip",
            value=initial_url,
            autofocus=not bool(initial_url),
            width=580,
            on_change=self._on_url_changed,
        )

        # 2. Filename Field
        self.filename_field = build_fluent_text_field(
            label="Nome File",
            hint_text="es. archivio.zip",
            value=initial_fn or "",
            width=580,
            on_change=self._on_filename_changed,
        )

        # 3. Category Dropdown
        category_options = [ft.dropdown.Option(cat) for cat in ALL_CATEGORIES]
        self.category_dropdown = ft.Dropdown(
            label="Categoria",
            value=self.current_category,
            options=category_options,
            bgcolor=SURFACE_CARD,
            color=TEXT_PRIMARY,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=12),
            text_size=12,
            border_radius=6,
            dense=True,
            width=240,
            on_select=self._on_category_changed,
        )

        # 4. Destination Directory Field & Browse Button
        self.dir_field = build_fluent_text_field(
            label="Cartella di Salvataggio",
            value=initial_dest,
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
            tooltip="Sfoglia cartelle su Windows",
        )

        self.resolved_files: list[ResolvedFile] = []
        self.selected_file_indices: set[int] = set()
        self.extra_headers: dict = {}

        self.resolve_spinner = ft.ProgressRing(width=14, height=14, stroke_width=2, color=ACCENT_BLUE, visible=False)
        self.resolve_status_text = ft.Text("", size=11, color=TEXT_SECONDARY, visible=False)

        self.resolve_btn = ft.OutlinedButton(
            icon=ft.Icons.EXTENSION_ROUNDED,
            content="Risolvi con Estensione",
            on_click=self._handle_resolve_with_extension,
            style=ft.ButtonStyle(
                color=ACCENT_LIGHT,
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, "rgba(0, 120, 212, 0.40)"),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            tooltip="Risolvi link o flussi video protetti tramite sandbox Gopeed",
        )

        self.multi_file_column = ft.Column(
            spacing=4,
            tight=True,
            visible=False,
            controls=[],
        )

        self.multi_file_container = ft.Container(
            visible=False,
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=ft.Padding(10, 10, 10, 10),
            content=ft.Column(
                tight=True,
                spacing=6,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("File Multipli Rilevati dall'Estensione:", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                            ft.TextButton(
                                content="Seleziona Tutti",
                                style=ft.ButtonStyle(color=ACCENT_LIGHT),
                                on_click=self._handle_select_all_multi_files,
                            ),
                        ],
                    ),
                    self.multi_file_column,
                ],
            ),
        )

        dir_row = ft.Row(
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                self.dir_field,
                browse_btn,
            ],
        )

        self.error_text = ft.Text("", color="#EF4444", size=12, visible=False)

        # Content assembly
        self.content = ft.Container(
            width=600,
            padding=ft.Padding(top=8, bottom=8, left=4, right=4),
            content=ft.Column(
                tight=True,
                spacing=10,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("Configura Parametri di Salvataggio:", size=12, color=TEXT_SECONDARY),
                            ft.Row(
                                spacing=8,
                                controls=[
                                    self.resolve_btn,
                                    ft.TextButton(
                                        icon=ft.Icons.CONTENT_PASTE_ROUNDED,
                                        content="Incolla appunti",
                                        style=ft.ButtonStyle(color=ACCENT_LIGHT),
                                        on_click=self._handle_paste_clipboard,
                                    ),
                                ],
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=6,
                        controls=[
                            self.resolve_spinner,
                            self.resolve_status_text,
                        ],
                    ),
                    self.url_field,
                    self.multi_file_container,
                    self.filename_field,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.START,
                        controls=[self.category_dropdown],
                    ),
                    dir_row,
                    self.error_text,
                ],
            ),
        )

        # Actions
        cancel_btn = ft.TextButton(
            content="Annulla",
            style=ft.ButtonStyle(color=TEXT_SECONDARY),
            on_click=self._handle_close,
        )

        pause_btn = ft.OutlinedButton(
            icon=ft.Icons.PAUSE_CIRCLE_OUTLINE_ROUNDED,
            content="Aggiungi in Coda (In Pausa)",
            style=ft.ButtonStyle(
                color="#F59E0B",
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, "rgba(245, 158, 11, 0.40)"),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=lambda e: self._handle_submit(e, start_paused=True),
            tooltip="Aggiunge il file nella lista senza avviarlo subito",
        )

        download_now_btn = ft.FilledButton(
            icon=ft.Icons.ROCKET_LAUNCH_ROUNDED,
            content="Scarica Ora",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=lambda e: self._handle_submit(e, start_paused=False),
            tooltip="Avvia il download immediatamente con aggregazione banda",
        )

        self.actions = [cancel_btn, pause_btn, download_now_btn]
        self.actions_alignment = ft.MainAxisAlignment.END

    def _on_url_changed(self, e: ft.ControlEvent) -> None:
        url = self.url_field.value.strip() if self.url_field.value else ""
        if url and not self.filename_field.value:
            detected_fn = url.split("/")[-1].split("?")[0] if "/" in url else ""
            if detected_fn and "." in detected_fn:
                self.filename_field.value = detected_fn

        new_cat = detect_category(self.filename_field.value or url)
        self._update_category_and_folder(new_cat, e.page)

    def _on_filename_changed(self, e: ft.ControlEvent) -> None:
        fn = self.filename_field.value.strip() if self.filename_field.value else ""
        new_cat = detect_category(fn or (self.url_field.value or ""))
        self._update_category_and_folder(new_cat, e.page)

    def _on_category_changed(self, e: ft.ControlEvent) -> None:
        cat = getattr(e.control, "value", None) or getattr(e, "data", None) or self.category_dropdown.value
        if cat:
            self.current_category = cat
            base_dir = self.coordinator.queue_manager.default_download_dir
            new_path = str(get_category_subfolder(base_dir, cat))
            self.dir_field.value = new_path
            if getattr(e, "page", None):
                e.page.update()

    def _update_category_and_folder(self, new_cat: str, page: Optional[ft.Page]) -> None:
        if new_cat and new_cat in ALL_CATEGORIES:
            self.current_category = new_cat
            self.category_dropdown.value = new_cat
            base_dir = self.coordinator.queue_manager.default_download_dir
            self.dir_field.value = str(get_category_subfolder(base_dir, new_cat))
            if page:
                page.update()

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

    def _handle_resolve_with_extension(self, e: ft.ControlEvent) -> None:
        url = self.url_field.value.strip() if self.url_field.value else ""
        if not url:
            self.error_text.value = "Inserisci un URL da risolvere."
            self.error_text.visible = True
            if e.page:
                e.page.update()
            return

        async def _resolve():
            self.resolve_spinner.visible = True
            self.resolve_status_text.value = "Risoluzione link tramite estensioni Gopeed in corso..."
            self.resolve_status_text.color = TEXT_SECONDARY
            self.resolve_status_text.visible = True
            self.error_text.visible = False
            if e.page:
                e.page.update()

            try:
                res: Optional[ResolvedResource] = await asyncio.to_thread(
                    self.coordinator.extension_manager.resolve_url,
                    url,
                )
                self.resolve_spinner.visible = False
                if res and (res.url or res.files):
                    self.extra_headers.update(res.headers)
                    if res.files and len(res.files) > 1:
                        # Multi-file presentation
                        self.resolved_files = res.files
                        self.selected_file_indices = set(range(len(res.files)))
                        self.multi_file_column.controls.clear()

                        for idx, rf in enumerate(res.files):
                            size_str = f" ({format_bytes(rf.size)})" if rf.size else ""
                            fn_display = rf.name or f"File #{idx + 1}"
                            cb = ft.Checkbox(
                                label=f"{fn_display}{size_str}",
                                value=True,
                                active_color=ACCENT_BLUE,
                                on_change=lambda ev, i=idx: self._on_file_check_toggle(i, ev.control.value),
                            )
                            self.multi_file_column.controls.append(cb)

                        self.multi_file_container.visible = True
                        self.multi_file_column.visible = True
                        self.resolve_status_text.value = f"✅ Trovati {len(res.files)} file/flussi scaricabili!"
                        self.resolve_status_text.color = "#10B981"
                        if res.filename:
                            self.filename_field.value = res.filename
                    else:
                        # Single resolved file
                        resolved_url = res.url
                        resolved_name = res.filename
                        if res.files and len(res.files) == 1:
                            resolved_url = res.files[0].url or resolved_url
                            resolved_name = res.files[0].name or resolved_name
                            if res.files[0].headers:
                                self.extra_headers.update(res.files[0].headers)

                        self.url_field.value = resolved_url
                        if resolved_name:
                            self.filename_field.value = resolved_name
                            new_cat = detect_category(resolved_name)
                            self._update_category_and_folder(new_cat, e.page)

                        self.multi_file_container.visible = False
                        self.resolve_status_text.value = f"✅ Link diretto risolto con successo! ({resolved_name or 'file'})"
                        self.resolve_status_text.color = "#10B981"
                else:
                    self.resolve_status_text.value = "Nessuna estensione compatibile o link non risolvibile."
                    self.resolve_status_text.color = "#F59E0B"
            except Exception as exc:
                self.resolve_spinner.visible = False
                self.resolve_status_text.value = f"Errore risoluzione: {exc}"
                self.resolve_status_text.color = "#EF4444"

            if e.page:
                e.page.update()

        if e.page:
            e.page.run_task(_resolve)

    def _on_file_check_toggle(self, idx: int, checked: bool) -> None:
        if checked:
            self.selected_file_indices.add(idx)
        else:
            self.selected_file_indices.discard(idx)

    def _handle_select_all_multi_files(self, e: ft.ControlEvent) -> None:
        if self.resolved_files:
            self.selected_file_indices = set(range(len(self.resolved_files)))
            for ctrl in self.multi_file_column.controls:
                if isinstance(ctrl, ft.Checkbox):
                    ctrl.value = True
            if e.page:
                e.page.update()

    def _handle_paste_clipboard(self, e: ft.ControlEvent) -> None:
        try:
            text = pyperclip.paste()
            if text and (text.startswith("http://") or text.startswith("https://") or text.startswith("magnet:")):
                self.url_field.value = text.strip()
                self._on_url_changed(e)
                for ext in self.coordinator.extension_manager.list_extensions():
                    if ext.enabled:
                        for s in ext.manifest.scripts:
                            if s.match_urls and any(p != "*" and ("//" in p) for p in s.match_urls):
                                self.resolve_status_text.value = "💡 Estensione compatibile rilevata! Clicca 'Risolvi con Estensione'."
                                self.resolve_status_text.color = ACCENT_LIGHT
                                self.resolve_status_text.visible = True
                                break
        except Exception:
            pass

    def _handle_close(self, e: ft.ControlEvent) -> None:
        if e.page:
            e.page.pop_dialog()

    def _handle_submit(self, e: ft.ControlEvent, start_paused: bool = False) -> None:
        url = self.url_field.value.strip() if self.url_field.value else ""
        dest_dir = Path(self.dir_field.value.strip()) if self.dir_field.value else None
        custom_fn = self.filename_field.value.strip() if self.filename_field.value else None
        cat = self.category_dropdown.value or self.current_category

        # Case 1: Multi-file selection from Gopeed extension
        if self.multi_file_container.visible and self.resolved_files:
            if not self.selected_file_indices:
                self.error_text.value = "Seleziona almeno un file da scaricare."
                self.error_text.visible = True
                if e.page:
                    e.page.update()
                return

            async def _add_all_selected():
                for idx in sorted(self.selected_file_indices):
                    rf = self.resolved_files[idx]
                    f_url = rf.url
                    f_name = rf.name or f"file_{idx + 1}"
                    f_headers = dict(self.extra_headers)
                    f_headers.update(rf.headers)

                    await self.coordinator.add_download(
                        url=f_url,
                        destination_dir=dest_dir,
                        filename=f_name,
                        category=cat,
                        start_paused=start_paused,
                        cookies=self.cookies or (f_headers.get("Cookie") if f_headers else None),
                        referer=self.referer or (f_headers.get("Referer") if f_headers else None),
                        user_agent=self.user_agent or (f_headers.get("User-Agent") if f_headers else None),
                    )
                if self.on_added:
                    self.on_added()

            if e.page:
                e.page.run_task(_add_all_selected)
                e.page.pop_dialog()
            return

        # Case 2: Standard single-file submission
        if not url or not (url.startswith("http://") or url.startswith("https://") or url.startswith("magnet:")):
            self.error_text.value = "Inserisci un URL valido (HTTP, HTTPS o Magnet)."
            self.error_text.visible = True
            if e.page:
                e.page.update()
            return

        async def _add():
            await self.coordinator.add_download(
                url=url,
                destination_dir=dest_dir,
                filename=custom_fn,
                category=cat,
                start_paused=start_paused,
                cookies=self.cookies,
                referer=self.referer,
                user_agent=self.user_agent,
            )
            if self.on_added:
                self.on_added()

        if e.page:
            e.page.run_task(_add)
            e.page.pop_dialog()
