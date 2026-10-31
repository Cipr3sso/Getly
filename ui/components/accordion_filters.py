from typing import Callable, Optional, Set
import flet as ft

from core.scheduler.categories import ALL_CATEGORIES, CATEGORY_COLORS
from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    ACCENT_LIGHT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    safe_update,
)

ALL_STATUS_KEYS = ["DOWNLOADING", "PAUSED", "COMPLETED", "FAILED", "PENDING"]
STATUS_LABELS = {
    "DOWNLOADING": "In download",
    "PAUSED": "In pausa",
    "COMPLETED": "Completati",
    "FAILED": "Errori e annullati",
    "PENDING": "In coda",
}

ALL_SOURCES = ["HTTP", "TORRENT", "MAGNET", "GOPEED"]
SOURCE_LABELS = {
    "HTTP": "HTTP / HTTPS",
    "TORRENT": "Torrent (.torrent)",
    "MAGNET": "Magnet Link",
    "GOPEED": "Estensione Gopeed",
}

CATEGORY_LABELS = {
    "Torrents": "Torrent & Magnet",
    "Compressi": "Compressi",
    "Programmi": "Programmi",
    "Video": "Video",
    "Audio": "Audio",
    "Documenti": "Documenti",
    "Immagini_ISO": "Immagini & ISO",
    "Altro": "Altro",
}


class AccordionFilters(ft.Container):
    """
    Windows 11 Fluent Dark Accordion Filter Column (width=195):
    - Collapsible accordion sections for Stati, Categorie, and Sorgente
    - All checkboxes checked by default ('Tutti' state)
    - Quick actions: 'Tutti' / 'Nessuno' per section
    - Smooth scroll=ft.ScrollMode.HIDDEN without scrollbar clutter
    - Real-time reactive updates notifying parent view
    """

    def __init__(
        self,
        on_filters_changed: Optional[Callable[[Set[str], Set[str], Set[str]], None]] = None,
    ):
        super().__init__()
        self.on_filters_changed = on_filters_changed
        self.width = 195
        self.padding = ft.Padding(left=6, right=6, top=6, bottom=6)
        self.bgcolor = BG_PANEL
        self.border = ft.Border(right=ft.BorderSide(1, BORDER_COLOR))

        # Active selections (default: ALL selected)
        self.selected_statuses: Set[str] = set(ALL_STATUS_KEYS)
        self.selected_categories: Set[str] = set(ALL_CATEGORIES)
        self.selected_sources: Set[str] = set(ALL_SOURCES)

        # Section visibility toggles
        self.status_expanded = True
        self.cat_expanded = True
        self.src_expanded = True

        # Checkbox maps
        self.status_checkboxes: dict[str, ft.Checkbox] = {}
        self.cat_checkboxes: dict[str, ft.Checkbox] = {}
        self.src_checkboxes: dict[str, ft.Checkbox] = {}

        self.status_body = self._build_status_section()
        self.cat_body = self._build_category_section()
        self.src_body = self._build_source_section()

        self.content = ft.ListView(
            expand=True,
            spacing=6,
            padding=ft.Padding(2, 2, 2, 2),
            scroll=ft.ScrollMode.HIDDEN,
            controls=[
                self.status_body,
                ft.Divider(color=BORDER_COLOR, height=1),
                self.cat_body,
                ft.Divider(color=BORDER_COLOR, height=1),
                self.src_body,
            ],
        )

    def _notify_filters_changed(self) -> None:
        if self.on_filters_changed:
            self.on_filters_changed(
                set(self.selected_statuses),
                set(self.selected_categories),
                set(self.selected_sources),
            )

    # 1. Stati Section
    def _build_status_section(self) -> ft.Container:
        self.status_column = ft.Column(spacing=2, tight=True)
        for st_key in ALL_STATUS_KEYS:
            cb = ft.Checkbox(
                label=STATUS_LABELS[st_key],
                value=True,
                scale=0.85,
                active_color=ACCENT_BLUE,
                label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12),
                on_change=lambda e, k=st_key: self._on_status_changed(k, e.control.value),
            )
            self.status_checkboxes[st_key] = cb
            self.status_column.controls.append(cb)

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.Icon(ft.Icons.FILTER_LIST_ROUNDED, size=14, color=ACCENT_LIGHT),
                        ft.Text("Stati", size=12, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ],
                ),
                ft.IconButton(
                    icon=ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
                    icon_size=16,
                    icon_color=TEXT_MUTED,
                    on_click=self._toggle_status_section,
                ),
            ],
        )

        quick_links = ft.Row(
            spacing=4,
            controls=[
                ft.TextButton(
                    content="Tutti",
                    style=ft.ButtonStyle(color=ACCENT_LIGHT, padding=ft.Padding(4, 2, 4, 2)),
                    on_click=self._select_all_statuses,
                ),
                ft.Text("|", size=10, color=TEXT_MUTED),
                ft.TextButton(
                    content="Nessuno",
                    style=ft.ButtonStyle(color=TEXT_MUTED, padding=ft.Padding(4, 2, 4, 2)),
                    on_click=self._clear_statuses,
                ),
            ],
        )

        return ft.Container(
            content=ft.Column(
                tight=True,
                spacing=4,
                controls=[
                    header,
                    quick_links,
                    self.status_column,
                ],
            )
        )

    def _toggle_status_section(self, e: ft.ControlEvent) -> None:
        self.status_expanded = not self.status_expanded
        self.status_column.visible = self.status_expanded
        e.control.icon = (
            ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            if self.status_expanded
            else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED
        )
        safe_update(self)

    def _on_status_changed(self, key: str, checked: bool) -> None:
        if checked:
            self.selected_statuses.add(key)
        else:
            self.selected_statuses.discard(key)
        self._notify_filters_changed()

    def _select_all_statuses(self, e: ft.ControlEvent) -> None:
        for k, cb in self.status_checkboxes.items():
            cb.value = True
        self.selected_statuses = set(ALL_STATUS_KEYS)
        safe_update(self)
        self._notify_filters_changed()

    def _clear_statuses(self, e: ft.ControlEvent) -> None:
        for k, cb in self.status_checkboxes.items():
            cb.value = False
        self.selected_statuses.clear()
        safe_update(self)
        self._notify_filters_changed()

    # 2. Categorie Section
    def _build_category_section(self) -> ft.Container:
        self.cat_column = ft.Column(spacing=2, tight=True)
        for cat in ALL_CATEGORIES:
            cb = ft.Checkbox(
                label=CATEGORY_LABELS.get(cat, cat),
                value=True,
                scale=0.85,
                active_color=ACCENT_BLUE,
                label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12),
                on_change=lambda e, c=cat: self._on_cat_changed(c, e.control.value),
            )
            self.cat_checkboxes[cat] = cb
            self.cat_column.controls.append(cb)

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.Icon(ft.Icons.CATEGORY_ROUNDED, size=14, color=ACCENT_LIGHT),
                        ft.Text("Categorie", size=12, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ],
                ),
                ft.IconButton(
                    icon=ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
                    icon_size=16,
                    icon_color=TEXT_MUTED,
                    on_click=self._toggle_cat_section,
                ),
            ],
        )

        quick_links = ft.Row(
            spacing=4,
            controls=[
                ft.TextButton(
                    content="Tutti",
                    style=ft.ButtonStyle(color=ACCENT_LIGHT, padding=ft.Padding(4, 2, 4, 2)),
                    on_click=self._select_all_cats,
                ),
                ft.Text("|", size=10, color=TEXT_MUTED),
                ft.TextButton(
                    content="Nessuno",
                    style=ft.ButtonStyle(color=TEXT_MUTED, padding=ft.Padding(4, 2, 4, 2)),
                    on_click=self._clear_cats,
                ),
            ],
        )

        return ft.Container(
            content=ft.Column(
                tight=True,
                spacing=4,
                controls=[
                    header,
                    quick_links,
                    self.cat_column,
                ],
            )
        )

    def _toggle_cat_section(self, e: ft.ControlEvent) -> None:
        self.cat_expanded = not self.cat_expanded
        self.cat_column.visible = self.cat_expanded
        e.control.icon = (
            ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            if self.cat_expanded
            else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED
        )
        safe_update(self)

    def _on_cat_changed(self, cat: str, checked: bool) -> None:
        if checked:
            self.selected_categories.add(cat)
        else:
            self.selected_categories.discard(cat)
        self._notify_filters_changed()

    def _select_all_cats(self, e: ft.ControlEvent) -> None:
        for c, cb in self.cat_checkboxes.items():
            cb.value = True
        self.selected_categories = set(ALL_CATEGORIES)
        safe_update(self)
        self._notify_filters_changed()

    def _clear_cats(self, e: ft.ControlEvent) -> None:
        for c, cb in self.cat_checkboxes.items():
            cb.value = False
        self.selected_categories.clear()
        safe_update(self)
        self._notify_filters_changed()

    # 3. Sorgente Section
    def _build_source_section(self) -> ft.Container:
        self.src_column = ft.Column(spacing=2, tight=True)
        for src_key in ALL_SOURCES:
            cb = ft.Checkbox(
                label=SOURCE_LABELS[src_key],
                value=True,
                scale=0.85,
                active_color=ACCENT_BLUE,
                label_style=ft.TextStyle(color=TEXT_PRIMARY, size=12),
                on_change=lambda e, s=src_key: self._on_src_changed(s, e.control.value),
            )
            self.src_checkboxes[src_key] = cb
            self.src_column.controls.append(cb)

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Row(
                    spacing=6,
                    controls=[
                        ft.Icon(ft.Icons.SOURCE_ROUNDED, size=14, color=ACCENT_LIGHT),
                        ft.Text("Sorgente", size=12, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                    ],
                ),
                ft.IconButton(
                    icon=ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED,
                    icon_size=16,
                    icon_color=TEXT_MUTED,
                    on_click=self._toggle_src_section,
                ),
            ],
        )

        quick_links = ft.Row(
            spacing=4,
            controls=[
                ft.TextButton(
                    content="Tutti",
                    style=ft.ButtonStyle(color=ACCENT_LIGHT, padding=ft.Padding(4, 2, 4, 2)),
                    on_click=self._select_all_sources,
                ),
                ft.Text("|", size=10, color=TEXT_MUTED),
                ft.TextButton(
                    content="Nessuno",
                    style=ft.ButtonStyle(color=TEXT_MUTED, padding=ft.Padding(4, 2, 4, 2)),
                    on_click=self._clear_sources,
                ),
            ],
        )

        return ft.Container(
            content=ft.Column(
                tight=True,
                spacing=4,
                controls=[
                    header,
                    quick_links,
                    self.src_column,
                ],
            )
        )

    def _toggle_src_section(self, e: ft.ControlEvent) -> None:
        self.src_expanded = not self.src_expanded
        self.src_column.visible = self.src_expanded
        e.control.icon = (
            ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
            if self.src_expanded
            else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED
        )
        safe_update(self)

    def _on_src_changed(self, src: str, checked: bool) -> None:
        if checked:
            self.selected_sources.add(src)
        else:
            self.selected_sources.discard(src)
        self._notify_filters_changed()

    def _select_all_sources(self, e: ft.ControlEvent) -> None:
        for s, cb in self.src_checkboxes.items():
            cb.value = True
        self.selected_sources = set(ALL_SOURCES)
        safe_update(self)
        self._notify_filters_changed()

    def _clear_sources(self, e: ft.ControlEvent) -> None:
        for s, cb in self.src_checkboxes.items():
            cb.value = False
        self.selected_sources.clear()
        safe_update(self)
        self._notify_filters_changed()
