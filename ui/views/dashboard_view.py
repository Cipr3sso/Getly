import asyncio
from pathlib import Path
from typing import Callable, List, Optional, Set
import flet as ft

from core.scheduler.categories import ALL_CATEGORIES
from core.scheduler.task import DownloadTask, DownloadStatus
from ui.coordinator import UICoordinator, format_bytes, format_speed
from ui.components.download_card import DownloadCard
from ui.components.add_download_dialog import AddDownloadDialog
from ui.components.accordion_filters import AccordionFilters, ALL_STATUS_KEYS, ALL_SOURCES
from ui.components.datagrid_view import DownloadDataGrid, export_tasks_to_csv
from ui.theme import (
    BG_WINDOW,
    BG_PANEL,
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


class DashboardView(ft.Container):
    """
    Windows 11 Fluent Dark Downloads Management Dashboard:
    - Page Header: 'Gestione download' with dynamic counters
    - Horizontal Command Bar with native buttons (+ Nuovo Download, Avvia selezione, Pausa selezione,
      Ricarica, Rimuovi selezionati, Pulisci completati, Esporta in CSV)
    - Accordion Filters column on the left (width=195)
    - Central DataGrid Table with Checkboxes & Cards View switcher
    - Sort selector and real-time search support
    - Surgical update_live_metrics() without global page update
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_add_requested: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_add_requested = on_add_requested
        self.expand = True
        self.bgcolor = BG_WINDOW
        self.padding = ft.Padding(left=14, right=14, top=10, bottom=10)

        # Filters and search state
        self.current_filter = "ALL"
        self.current_category_filter = "ALL"
        self.search_query = ""
        self.current_sort = "date"

        # Responsive & overlay state
        self._filters_visible = True
        self._is_narrow_screen = False
        self._overlay_filters_open = False
        self._command_bar_compact = False
        self._secondary_labels: list[ft.Text] = []
        self._secondary_buttons: list[ft.OutlinedButton] = []
        self._last_page_width: float = 1280.0
        self._last_rail_width: float = 56.0

        # View mode: "DATAGRID" or "CARDS"
        self.view_mode = "DATAGRID"

        self._card_map: dict[str, DownloadCard] = {}
        self._empty_state_container = self._build_empty_state()

        # 1. Page Header & Command Bar
        self.title_text = ft.Text("Gestione download", size=18, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY)
        self.stats_text = ft.Text("0 download totali", size=11, color=TEXT_SECONDARY)

        # 2. Accordion Filters Column (width=195)
        self.accordion_filters = AccordionFilters(on_filters_changed=self._on_accordion_filters_changed)
        self.selected_statuses: Set[str] = set(self.accordion_filters.selected_statuses)
        self.selected_categories: Set[str] = set(self.accordion_filters.selected_categories)
        self.selected_sources: Set[str] = set(self.accordion_filters.selected_sources)

        self.command_bar = self._build_command_bar()

        # 3. Central Views: DataGrid & Cards
        self.datagrid = DownloadDataGrid(
            coordinator=self.coordinator,
            on_selection_changed=self._on_grid_selection_changed,
            on_action=self.refresh_tasks,
            on_open_details=self._open_task_details,
        )

        self.card_list_view = ft.ListView(
            expand=True,
            spacing=4,
            padding=ft.Padding(top=4, bottom=20),
        )

        self.central_view_container = ft.Container(
            expand=True,
            content=self.datagrid,
        )

        # 4. Bottom Grid Footer Bar
        self.selected_count_text = ft.Text("0 elementi selezionati", size=11, color=TEXT_MUTED)

        self.sort_dropdown = ft.Dropdown(
            label="Ordina per",
            value="date",
            options=[
                ft.dropdown.Option("date", "Data di aggiunta"),
                ft.dropdown.Option("name_asc", "Nome (A-Z)"),
                ft.dropdown.Option("name_desc", "Nome (Z-A)"),
                ft.dropdown.Option("size", "Dimensione"),
                ft.dropdown.Option("progress", "Progresso %"),
                ft.dropdown.Option("speed", "Velocità"),
            ],
            border_radius=6,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            bgcolor=SURFACE_CARD,
            color=TEXT_PRIMARY,
            label_style=ft.TextStyle(color=TEXT_SECONDARY, size=11),
            text_size=11,
            dense=True,
            width=170,
            on_select=self._on_sort_changed,
        )

        self.view_switch_grid_btn = ft.IconButton(
            icon=ft.Icons.TABLE_ROWS_ROUNDED,
            icon_size=18,
            icon_color=ACCENT_LIGHT if self.view_mode == "DATAGRID" else TEXT_MUTED,
            tooltip="Visualizzazione Tabella DataGrid",
            on_click=lambda e: self._switch_view_mode("DATAGRID"),
        )

        self.view_switch_cards_btn = ft.IconButton(
            icon=ft.Icons.VIEW_AGENDA_ROUNDED,
            icon_size=18,
            icon_color=ACCENT_LIGHT if self.view_mode == "CARDS" else TEXT_MUTED,
            tooltip="Visualizzazione Card Dettagliate",
            on_click=lambda e: self._switch_view_mode("CARDS"),
        )

        self.bottom_grid_bar = ft.Container(
            padding=ft.Padding(left=10, right=10, top=6, bottom=4),
            border=ft.Border(top=ft.BorderSide(1, BORDER_COLOR)),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self.selected_count_text,
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            self.sort_dropdown,
                            ft.Row(spacing=2, controls=[self.view_switch_grid_btn, self.view_switch_cards_btn]),
                        ],
                    ),
                ],
            ),
        )

        # Center column (Central View + Bottom Bar)
        self.center_deck = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self.central_view_container,
                self.bottom_grid_bar,
            ],
        )

        # Separator pill handle (|) between filters and table (UniGetUI style)
        self.filter_handle_pill = ft.Container(
            width=4,
            height=28,
            border_radius=2,
            bgcolor="#505050",
        )
        self.filter_divider = ft.Container(
            width=12,
            alignment=ft.Alignment(0, 0),
            content=self.filter_handle_pill,
        )

        self.inline_filter_slot = ft.Container(
            content=self.accordion_filters,
        )

        self.inline_filter_container = ft.Row(
            spacing=0,
            tight=True,
            visible=True,
            controls=[
                self.inline_filter_slot,
                self.filter_divider,
            ],
        )

        # Horizontal layout: Inline Filters + Center deck
        self.body_split = ft.Row(
            expand=True,
            spacing=0,
            controls=[
                self.inline_filter_container,
                self.center_deck,
            ],
        )

        # Overlay backdrop & floating panel for narrow screens (< 900px)
        self.overlay_backdrop = ft.Container(
            expand=True,
            bgcolor=ft.Colors.with_opacity(0.55, "#000000"),
            visible=False,
            on_click=self._close_overlay_filters,
        )

        self.overlay_filters_panel = ft.Container(
            left=0,
            top=0,
            bottom=0,
            width=215,
            bgcolor=BG_PANEL,
            border=ft.Border(right=ft.BorderSide(1, BORDER_COLOR)),
            shadow=GLASS_CARD_SHADOW,
            padding=ft.Padding(4, 4, 4, 4),
            visible=False,
        )

        self.body_stack = ft.Stack(
            expand=True,
            controls=[
                self.body_split,
                self.overlay_backdrop,
                self.overlay_filters_panel,
            ],
        )

        # Full view content column
        self.content = ft.Column(
            expand=True,
            spacing=8,
            controls=[
                # Top Page Title + Stats
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Column(spacing=1, controls=[self.title_text, self.stats_text]),
                    ],
                ),
                # Command Bar
                self.command_bar,
                ft.Divider(color=BORDER_COLOR, height=1),
                # Body: Stack with inline & overlay filters
                self.body_stack,
            ],
        )

        self.refresh_tasks()

    def _build_command_bar(self) -> ft.Container:
        """Builds compact single-row WinUI 3 Command Bar adapting between icon+text and icon-only."""
        self._secondary_labels.clear()
        self._secondary_buttons.clear()

        # 1. Filtri button (Leftmost)
        icon_filters = ft.Icon(ft.Icons.FILTER_ALT_OUTLINED, size=15, color=ACCENT_LIGHT)
        lbl_filters = ft.Text("Filtri", size=12, color=TEXT_PRIMARY)
        self._secondary_labels.append(lbl_filters)
        self.btn_filters = ft.OutlinedButton(
            content=ft.Row([icon_filters, lbl_filters], tight=True, spacing=5, alignment=ft.MainAxisAlignment.CENTER),
            style=ft.ButtonStyle(
                color=TEXT_PRIMARY,
                bgcolor=SURFACE_CARD,
                side=ft.BorderSide(1, BORDER_COLOR),
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(8, 5, 8, 5),
            ),
            height=34,
            on_click=self._handle_toggle_filters,
            tooltip="Mostra / Nascondi pannello filtri",
        )

        # 2. Nuovo Download (Primary - always keeps icon + text)
        btn_add = ft.FilledButton(
            icon=ft.Icons.ADD_ROUNDED,
            content="Nuovo Download",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
                padding=ft.Padding(10, 6, 12, 6),
            ),
            height=34,
            on_click=lambda e: self._handle_add_download(e),
            tooltip="Aggiungi un nuovo download URL o Torrent",
        )

        def _make_divider() -> ft.Container:
            return ft.Container(height=18, width=1, bgcolor="#3D3D3D", margin=ft.Margin(2, 0, 2, 0))

        def _make_sec_btn(
            icon_name: str,
            text: str,
            on_click: Callable,
            tooltip: str,
            color: str = TEXT_PRIMARY,
            border_color: str = BORDER_COLOR,
        ) -> ft.OutlinedButton:
            ic = ft.Icon(icon_name, size=15, color=color)
            lb = ft.Text(text, size=12, color=color)
            self._secondary_labels.append(lb)
            btn = ft.OutlinedButton(
                content=ft.Row([ic, lb], tight=True, spacing=5, alignment=ft.MainAxisAlignment.CENTER),
                style=ft.ButtonStyle(
                    color=color,
                    bgcolor=SURFACE_CARD,
                    side=ft.BorderSide(1, border_color),
                    shape=ft.RoundedRectangleBorder(radius=6),
                    padding=ft.Padding(8, 5, 8, 5),
                ),
                height=34,
                on_click=on_click,
                tooltip=tooltip,
            )
            self._secondary_buttons.append(btn)
            return btn

        btn_resume_sel = _make_sec_btn(ft.Icons.PLAY_ARROW_ROUNDED, "Avvia selezione", self._handle_resume_selected, "Avvia tutti i download selezionati")
        btn_pause_sel = _make_sec_btn(ft.Icons.PAUSE_ROUNDED, "Pausa selezione", self._handle_pause_selected, "Metti in pausa i download selezionati")
        btn_details = _make_sec_btn(ft.Icons.INFO_OUTLINE_ROUNDED, "Dettagli", self._handle_open_selected_details, "Mostra dettagli completi del download selezionato")
        btn_refresh = _make_sec_btn(ft.Icons.REFRESH_ROUNDED, "Ricarica", lambda e: self.refresh_tasks(), "Aggiorna la lista dei download")
        btn_delete_sel = _make_sec_btn(ft.Icons.DELETE_OUTLINE_ROUNDED, "Rimuovi selezionati", self._handle_delete_selected, "Rimuove i download selezionati dalla lista", color="#EF4444", border_color="rgba(239, 68, 68, 0.35)")
        btn_pause_all = _make_sec_btn(ft.Icons.PAUSE_CIRCLE_OUTLINE_ROUNDED, "Pausa Tutti", self._handle_pause_all, "Mette in pausa tutti i download attivi", color="#F59E0B", border_color="rgba(245, 158, 11, 0.35)")
        btn_clear_inactive = _make_sec_btn(ft.Icons.CLEANING_SERVICES_ROUNDED, "Pulisci completati", self._handle_clear_inactive, "Rimuove i download completati ed errori dalla lista", color=TEXT_SECONDARY)
        btn_export_csv = _make_sec_btn(ft.Icons.FILE_DOWNLOAD_ROUNDED, "Esporta CSV", self._handle_export_csv, "Esporta l'elenco dei download in un file CSV", color=TEXT_SECONDARY)

        return ft.Container(
            content=ft.Row(
                spacing=5,
                wrap=False,
                scroll=ft.ScrollMode.HIDDEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    self.btn_filters,
                    btn_add,
                    _make_divider(),
                    btn_resume_sel,
                    _make_divider(),
                    btn_pause_sel,
                    _make_divider(),
                    btn_details,
                    _make_divider(),
                    btn_refresh,
                    _make_divider(),
                    btn_delete_sel,
                    _make_divider(),
                    btn_pause_all,
                    _make_divider(),
                    btn_clear_inactive,
                    _make_divider(),
                    btn_export_csv,
                ],
            )
        )

    def _build_empty_state(self) -> ft.Container:
        box = ft.Container(
            padding=ft.Padding(30, 25, 30, 25),
            border_radius=12,
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            content=ft.Column(
                tight=True,
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=12,
                controls=[
                    ft.Container(
                        width=56,
                        height=56,
                        border_radius=28,
                        bgcolor="rgba(0, 120, 212, 0.15)",
                        border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                        alignment=ft.Alignment(0, 0),
                        content=ft.Icon(ft.Icons.INBOX_ROUNDED, size=28, color=ACCENT_BLUE),
                    ),
                    ft.Text("Nessun download presente", size=15, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
                    ft.Text(
                        "Incolla un URL o copia un link negli appunti per avviare il download.",
                        size=12,
                        color=TEXT_SECONDARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.FilledButton(
                        icon=ft.Icons.ADD_LINK_ROUNDED,
                        content="Aggiungi URL",
                        style=ft.ButtonStyle(
                            bgcolor=ACCENT_BLUE,
                            color="#FFFFFF",
                            shape=ft.RoundedRectangleBorder(radius=6),
                        ),
                        on_click=self._handle_add_download,
                    ),
                ],
            ),
        )
        return ft.Container(
            expand=True,
            alignment=ft.Alignment(0, 0),
            padding=ft.Padding(top=70, bottom=40),
            content=box,
        )

    def _update_command_bar_mode(self, is_compact: bool) -> None:
        if self._command_bar_compact == is_compact:
            return
        self._command_bar_compact = is_compact

        for lb in self._secondary_labels:
            lb.visible = not is_compact

        for btn in self._secondary_buttons:
            if is_compact:
                btn.width = 38
                btn.height = 34
                btn.style.padding = ft.Padding(0, 0, 0, 0)
            else:
                btn.width = None
                btn.height = 34
                btn.style.padding = ft.Padding(8, 5, 8, 5)

        if hasattr(self, "btn_filters") and self.btn_filters:
            if is_compact:
                self.btn_filters.width = 38
                self.btn_filters.height = 34
                self.btn_filters.style.padding = ft.Padding(0, 0, 0, 0)
            else:
                self.btn_filters.width = None
                self.btn_filters.height = 34
                self.btn_filters.style.padding = ft.Padding(8, 5, 8, 5)

        safe_update(self.command_bar)

    def on_page_resize(self, width: Optional[float] = None, rail_width: Optional[float] = None) -> None:
        """Adapts the Command Bar and filters panel based on real available width and window width."""
        if width is None:
            try:
                p = self.page
                if p:
                    width = getattr(p, "width", None)
            except (RuntimeError, Exception):
                width = None

        if width is None or width <= 0:
            width = self._last_page_width
        else:
            self._last_page_width = float(width)

        if rail_width is not None:
            self._last_rail_width = float(rail_width)

        current_rail_width = self._last_rail_width

        # 1. Filters responsive: inline when >= 900, overlay when < 900
        is_narrow = (width < 900)
        if self._is_narrow_screen != is_narrow:
            self._is_narrow_screen = is_narrow
            if is_narrow:
                # Screen became narrow: hide inline filters
                self.inline_filter_container.visible = False
                if not self._overlay_filters_open:
                    self.overlay_backdrop.visible = False
                    self.overlay_filters_panel.visible = False
            else:
                # Screen became wide: close overlay if open, restore inline
                if self._overlay_filters_open:
                    self._overlay_filters_open = False
                    self.overlay_backdrop.visible = False
                    self.overlay_filters_panel.visible = False
                    self.overlay_filters_panel.content = None
                    self.inline_filter_slot.content = self.accordion_filters
                self.inline_filter_container.visible = self._filters_visible
            safe_update(self.body_stack)

        # 2. Command Bar available width calculation:
        # available_width = page.width - rail_width - (210 if filters_open else 0)
        filters_open = (self._filters_visible and not self._is_narrow_screen)
        available_width = width - current_rail_width - (210 if filters_open else 0)

        is_compact_command_bar = (available_width < 1180)
        self._update_command_bar_mode(is_compact_command_bar)

    def _handle_toggle_filters(self, e: Optional[ft.ControlEvent] = None) -> None:
        """Toggles filters visibility (inline in wide mode, overlay backdrop in narrow mode)."""
        if self._is_narrow_screen:
            if self._overlay_filters_open:
                self._close_overlay_filters()
            else:
                self._overlay_filters_open = True
                self.inline_filter_slot.content = None
                self.overlay_filters_panel.content = self.accordion_filters
                self.overlay_backdrop.visible = True
                self.overlay_filters_panel.visible = True
                safe_update(self.body_stack)
        else:
            self._filters_visible = not self._filters_visible
            self.inline_filter_container.visible = self._filters_visible
            safe_update(self.body_split)
            # Recompute command bar available width immediately
            self.on_page_resize(self._last_page_width, rail_width=self._last_rail_width)

    def _close_overlay_filters(self, e: Optional[ft.ControlEvent] = None) -> None:
        """Closes the overlay filters panel and restores accordion to inline slot."""
        if not self._overlay_filters_open:
            return
        self._overlay_filters_open = False
        self.overlay_backdrop.visible = False
        self.overlay_filters_panel.visible = False
        self.overlay_filters_panel.content = None
        self.inline_filter_slot.content = self.accordion_filters
        safe_update(self.body_stack)
        self.on_page_resize(self._last_page_width, rail_width=self._last_rail_width)

    def _switch_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        self.view_switch_grid_btn.icon_color = ACCENT_LIGHT if mode == "DATAGRID" else TEXT_MUTED
        self.view_switch_cards_btn.icon_color = ACCENT_LIGHT if mode == "CARDS" else TEXT_MUTED
        self.refresh_tasks()
        safe_update(self)

    def set_search_query(self, query: str) -> None:
        self.search_query = query.strip().lower()
        self.refresh_tasks()
        safe_update(self)

    def _on_sort_changed(self, e: ft.ControlEvent) -> None:
        self.current_sort = self.sort_dropdown.value or "date"
        self.refresh_tasks()
        safe_update(self)

    def _on_accordion_filters_changed(
        self,
        statuses: Set[str],
        categories: Set[str],
        sources: Set[str],
    ) -> None:
        self.selected_statuses = statuses
        self.selected_categories = categories
        self.selected_sources = sources
        self.refresh_tasks()
        safe_update(self)

    def _on_grid_selection_changed(self, selected_ids: Set[str]) -> None:
        n = len(selected_ids)
        self.selected_count_text.value = f"{n} element{'i' if n != 1 else 'o'} selezionat{'i' if n != 1 else 'o'}"
        safe_update(self.selected_count_text)

    def set_filters(self, filter_status: str = "ALL", filter_category: Optional[str] = "ALL") -> None:
        self.current_filter = filter_status
        self.current_category_filter = filter_category or "ALL"
        self.refresh_tasks()
        safe_update(self)

    def _set_filter(self, filter_key: str) -> None:
        self.current_filter = filter_key
        self.refresh_tasks()
        safe_update(self)

    def _set_category_filter(self, cat_key: str) -> None:
        self.current_category_filter = cat_key
        self.refresh_tasks()
        safe_update(self)

    def update_live_metrics(self) -> None:
        """
        Surgical live metrics update without global re-renders.
        Updates only downloading rows or cards in place.
        """
        if self.view_mode == "DATAGRID":
            self.datagrid.update_live_metrics()
        else:
            for card in self._card_map.values():
                if card.task.status == DownloadStatus.DOWNLOADING:
                    card.update_live_metrics()

    def refresh_tasks(self) -> None:
        """Fetches tasks from coordinator, filters, sorts, and renders in active view mode."""
        all_tasks = self.coordinator.get_tasks("ALL")

        # 1. Update stats text
        total = len(all_tasks)
        active = sum(1 for t in all_tasks if t.status == DownloadStatus.DOWNLOADING)
        completed = sum(1 for t in all_tasks if t.status == DownloadStatus.COMPLETED)
        self.stats_text.value = f"{total} download totali • {active} in corso • {completed} completati"

        # 2. Filter tasks
        filtered: List[DownloadTask] = []
        for t in all_tasks:
            # A. Sidebar status filter
            if self.current_filter != "ALL":
                if self.current_filter == "ACTIVE" and t.status != DownloadStatus.DOWNLOADING:
                    continue
                elif self.current_filter == "COMPLETED" and t.status != DownloadStatus.COMPLETED:
                    continue
                elif self.current_filter == "PAUSED" and t.status != DownloadStatus.PAUSED:
                    continue
                elif self.current_filter == "FAILED" and t.status not in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                    continue

            # B. Sidebar category filter
            if self.current_category_filter != "ALL":
                if t.category != self.current_category_filter:
                    continue

            # C. Accordion status filter
            if self.selected_statuses and len(self.selected_statuses) < len(ALL_STATUS_KEYS):
                st_val = t.status.value if hasattr(t.status, "value") else str(t.status)
                if st_val not in self.selected_statuses:
                    continue
            elif not self.selected_statuses:
                continue

            # D. Accordion category filter
            if self.selected_categories and len(self.selected_categories) < len(ALL_CATEGORIES):
                if t.category not in self.selected_categories:
                    continue
            elif not self.selected_categories:
                continue

            # E. Accordion source filter
            if self.selected_sources and len(self.selected_sources) < len(ALL_SOURCES):
                src = (t.task_type or "HTTP").upper()
                if src not in self.selected_sources:
                    continue
            elif not self.selected_sources:
                continue

            # F. Search query
            if self.search_query:
                fn = (t.filename or "").lower()
                url = (t.url or "").lower()
                cat = (t.category or "").lower()
                if self.search_query not in fn and self.search_query not in url and self.search_query not in cat:
                    continue

            filtered.append(t)

        # 3. Sort tasks
        if self.current_sort == "name_asc":
            filtered.sort(key=lambda t: (t.filename or t.url).lower())
        elif self.current_sort == "name_desc":
            filtered.sort(key=lambda t: (t.filename or t.url).lower(), reverse=True)
        elif self.current_sort == "size":
            filtered.sort(key=lambda t: t.total_size, reverse=True)
        elif self.current_sort == "progress":
            filtered.sort(key=lambda t: t.progress_ratio, reverse=True)
        elif self.current_sort == "speed":
            filtered.sort(key=lambda t: t.download_speed_bps, reverse=True)
        else:
            filtered.sort(key=lambda t: t.created_at, reverse=True)

        # 4. Render in View Mode
        if not filtered:
            self._card_map.clear()
            self.card_list_view.controls = [self._empty_state_container]
            self.central_view_container.content = self._empty_state_container
            self.datagrid.set_tasks([])
            return

        self.datagrid.set_tasks(filtered)

        current_tids = {t.task_id for t in filtered}
        for tid in list(self._card_map.keys()):
            if tid not in current_tids:
                del self._card_map[tid]

        new_controls: list[ft.Control] = []
        for task in filtered:
            if task.task_id in self._card_map:
                card = self._card_map[task.task_id]
                card.update_task_state(task)
                new_controls.append(card)
            else:
                card = DownloadCard(
                    task=task,
                    coordinator=self.coordinator,
                    on_action=self.refresh_tasks,
                    on_open_details=self._open_task_details,
                )
                self._card_map[task.task_id] = card
                new_controls.append(card)

        self.card_list_view.controls = new_controls

        if self.view_mode == "DATAGRID":
            self.central_view_container.content = self.datagrid
        else:
            self.central_view_container.content = self.card_list_view

    # Command Bar Handlers
    def _handle_resume_selected(self, e: ft.ControlEvent) -> None:
        sel = list(self.datagrid.selected_task_ids)
        if not sel:
            return

        async def _run():
            for tid in sel:
                await self.coordinator.resume_download(tid)
            self.refresh_tasks()
            safe_update(self)

        if e.page:
            e.page.run_task(_run)

    def _handle_pause_selected(self, e: ft.ControlEvent) -> None:
        sel = list(self.datagrid.selected_task_ids)
        if not sel:
            return

        async def _run():
            for tid in sel:
                await self.coordinator.pause_download(tid)
            self.refresh_tasks()
            safe_update(self)

        if e.page:
            e.page.run_task(_run)

    def _handle_delete_selected(self, e: ft.ControlEvent) -> None:
        sel = list(self.datagrid.selected_task_ids)
        if not sel:
            return

        async def _run():
            for tid in sel:
                await self.coordinator.cancel_download(tid)
            self.datagrid.selected_task_ids.clear()
            self._on_grid_selection_changed(set())
            self.refresh_tasks()
            safe_update(self)

        if e.page:
            e.page.run_task(_run)

    def _handle_pause_all(self, e: ft.ControlEvent) -> None:
        async def _pause():
            await self.coordinator.pause_all()
            self.refresh_tasks()
            safe_update(self)

        if e.page:
            e.page.run_task(_pause)

    def _handle_clear_inactive(self, e: ft.ControlEvent) -> None:
        async def _clear():
            await self.coordinator.clear_inactive_tasks()
            self.refresh_tasks()
            safe_update(self)

        if e.page:
            e.page.run_task(_clear)

    def _handle_export_csv(self, e: ft.ControlEvent) -> None:
        tasks = self.coordinator.get_tasks("ALL")
        if not tasks:
            return
        dest_csv = self.coordinator.queue_manager.default_download_dir / "Getly_Downloads_Export.csv"
        try:
            export_tasks_to_csv(tasks, dest_csv)
            if hasattr(self.coordinator, "tray_manager") and self.coordinator.tray_manager:
                self.coordinator.tray_manager.notify("Esportazione Completata", f"Salvato in {dest_csv}")
        except Exception:
            pass

    def _handle_add_download(self, e: ft.ControlEvent) -> None:
        if self.on_add_requested:
            self.on_add_requested()
        elif e.page:
            dialog = AddDownloadDialog(
                coordinator=self.coordinator,
                on_added=self.refresh_tasks,
            )
            e.page.show_dialog(dialog)

    def _open_task_details(self, task: DownloadTask) -> None:
        """Opens complete technical and file details dialog for the given task."""
        if not self.page:
            return
        from ui.components.task_details_dialog import TaskDetailsDialog
        dialog = TaskDetailsDialog(task=task, coordinator=self.coordinator)
        dialog.show(self.page)

    def _handle_open_selected_details(self, e: ft.ControlEvent) -> None:
        sel = list(self.datagrid.selected_task_ids)
        if not sel:
            displayed = self.datagrid._current_tasks
            if displayed:
                self._open_task_details(displayed[0])
            return
        task = self.coordinator.get_task(sel[0])
        if task:
            self._open_task_details(task)
