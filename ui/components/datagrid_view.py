import csv
import io
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set
import flet as ft

from core.scheduler.task import DownloadTask, DownloadStatus
from core.scheduler.categories import CATEGORY_COLORS
from ui.components.download_card import get_file_icon, STATUS_CONFIG
from ui.coordinator import UICoordinator, format_bytes, format_speed
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
    build_gradient_progress_bar,
    safe_update,
)


def export_tasks_to_csv(tasks: List[DownloadTask], destination_path: Path) -> Path:
    """Exports list of DownloadTask items to a UTF-8 CSV spreadsheet."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with open(destination_path, mode="w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "ID", "Nome File", "Categoria", "Sorgente", "Stato",
            "Scaricati (Bytes)", "Totale (Bytes)", "Progresso %",
            "Velocita (bps)", "Data Aggiunta", "URL", "Cartella Destinazione"
        ])
        for t in tasks:
            writer.writerow([
                t.task_id,
                t.filename or "",
                t.category or "Altro",
                t.task_type or "HTTP",
                t.status.value if hasattr(t.status, "value") else str(t.status),
                t.downloaded_bytes,
                t.total_size,
                f"{int(t.progress_ratio * 100)}%",
                t.download_speed_bps,
                t.created_at,
                t.url,
                str(t.destination_dir),
            ])
    return destination_path


class DownloadDataGrid(ft.Container):
    """
    Windows 11 Fluent Dark DataGrid:
    - Columns: [☑️] | Nome file | Categoria | Dimensione | Velocità / Stato | Progresso | Sorgente | Azioni
    - Multi-selection support
    - Native ft.IconButton inline controls
    - Surgical update_live_metrics() updating ONLY moving rows without touching page or header
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_selection_changed: Optional[Callable[[Set[str]], None]] = None,
        on_action: Optional[Callable[[], None]] = None,
        on_open_details: Optional[Callable[[DownloadTask], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_selection_changed = on_selection_changed
        self.on_action = on_action
        self.on_open_details = on_open_details
        self.expand = True

        self.selected_task_ids: Set[str] = set()
        self._current_tasks: List[DownloadTask] = []
        self._row_elements: Dict[str, dict] = {}

        # Header Select-All Checkbox
        self.select_all_cb = ft.Checkbox(
            value=False,
            active_color=ACCENT_BLUE,
            on_change=self._toggle_select_all,
        )

        self.rows_column = ft.Column(
            spacing=2,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

        self.header_row = self._build_header_row()

        self.content = ft.Column(
            expand=True,
            spacing=0,
            controls=[
                self.header_row,
                ft.Divider(color=BORDER_COLOR, height=1),
                ft.Container(expand=True, content=self.rows_column),
            ],
        )

    def _build_header_row(self) -> ft.Container:
        return ft.Container(
            height=38,
            bgcolor=SURFACE_CARD,
            padding=ft.Padding(left=8, right=8, top=6, bottom=6),
            border_radius=6,
            content=ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(width=34, content=self.select_all_cb),
                    ft.Container(
                        expand=4,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        content=ft.Text(
                            "Nome file",
                            size=11,
                            weight=ft.FontWeight.BOLD,
                            color=TEXT_PRIMARY,
                            no_wrap=True,
                            max_lines=1,
                            overflow=ft.TextOverflow.ELLIPSIS,
                            expand=True,
                        ),
                    ),
                    ft.Container(expand=2, content=ft.Text("Categoria", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, no_wrap=True)),
                    ft.Container(expand=1, content=ft.Text("Dimensione", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, no_wrap=True)),
                    ft.Container(expand=2, content=ft.Text("Velocità / Stato", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, no_wrap=True)),
                    ft.Container(expand=2, content=ft.Text("Progresso", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, no_wrap=True)),
                    ft.Container(expand=1, content=ft.Text("Sorgente", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, no_wrap=True)),
                    ft.Container(width=75, content=ft.Text("Azioni", size=11, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY, no_wrap=True)),
                ],
            ),
        )

    def set_tasks(self, tasks: List[DownloadTask]) -> None:
        """Renders the rows in the DataGrid."""
        self._current_tasks = list(tasks)
        self.rows_column.controls.clear()
        self._row_elements.clear()

        if not tasks:
            self.select_all_cb.value = False
            return

        all_selected = len(self._current_tasks) > 0 and all(t.task_id in self.selected_task_ids for t in self._current_tasks)
        self.select_all_cb.value = all_selected

        for task in self._current_tasks:
            self.rows_column.controls.append(self._build_row(task))

    def update_live_metrics(self) -> None:
        """
        Surgically updates ONLY the rows of currently downloading tasks.
        Never calls page.update() or re-renders untouched rows!
        """
        for task in self._current_tasks:
            if task.status == DownloadStatus.DOWNLOADING and task.task_id in self._row_elements:
                elem = self._row_elements[task.task_id]
                speed = self.coordinator.task_speeds.get(task.task_id, task.download_speed_bps)
                ratio = task.progress_ratio

                # Update speed text
                if "speed_text" in elem:
                    elem["speed_text"].value = format_speed(speed)

                # Update percentage text
                if "pct_text" in elem:
                    elem["pct_text"].value = f"{int(ratio * 100)}%"

                # Update progress bar container
                if "progress_bar_box" in elem:
                    elem["progress_bar_box"].content = build_gradient_progress_bar(ratio, "DOWNLOADING")

                # Surgical update on this specific row only!
                safe_update(elem["row_ctrl"])

    def _build_row(self, task: DownloadTask) -> ft.Container:
        is_selected = task.task_id in self.selected_task_ids
        row_bg = "rgba(0, 120, 212, 0.18)" if is_selected else "transparent"

        cb = ft.Checkbox(
            value=is_selected,
            active_color=ACCENT_BLUE,
            on_change=lambda e, tid=task.task_id: self._toggle_row_selection(tid, e.control.value),
        )

        fn = task.filename or (task.url.split("/")[-1].split("?")[0] if "/" in task.url else task.url) or "File"
        file_icon = get_file_icon(task.filename)

        cat = task.category or "Altro"
        cat_color = CATEGORY_COLORS.get(cat, "#948FA8")
        cat_badge = ft.Container(
            bgcolor=ft.Colors.with_opacity(0.15, cat_color),
            border=ft.Border.all(1, ft.Colors.with_opacity(0.35, cat_color)),
            border_radius=6,
            padding=ft.Padding(6, 2, 6, 2),
            content=ft.Text(cat, size=10, color=cat_color, weight=ft.FontWeight.BOLD),
        )

        size_str = format_bytes(task.total_size) if task.total_size > 0 else (format_bytes(task.downloaded_bytes) if task.downloaded_bytes > 0 else "--")

        st_cfg = STATUS_CONFIG.get(task.status, STATUS_CONFIG[DownloadStatus.DOWNLOADING])
        speed_text_ctrl = None
        if task.status == DownloadStatus.DOWNLOADING:
            speed = self.coordinator.task_speeds.get(task.task_id, task.download_speed_bps)
            speed_text_ctrl = ft.Text(format_speed(speed), size=11, color="#10B981", weight=ft.FontWeight.BOLD)
            status_ctrl = speed_text_ctrl
        else:
            err_tooltip = f"Errore: {task.error_message}" if (task.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED) and task.error_message) else None
            status_ctrl = ft.Container(
                bgcolor=st_cfg["bg"],
                border=ft.Border.all(1, st_cfg["border"]),
                border_radius=6,
                padding=ft.Padding(6, 1, 6, 1),
                content=ft.Text(st_cfg["text"], size=10, color=st_cfg["color"], weight=ft.FontWeight.BOLD),
                tooltip=err_tooltip,
            )

        ratio = task.progress_ratio
        pct_text = ft.Text(f"{int(ratio * 100)}%", size=10, color=TEXT_PRIMARY, width=32)
        progress_bar_box = ft.Container(
            expand=True,
            content=build_gradient_progress_bar(ratio, task.status.value if hasattr(task.status, "value") else str(task.status)),
        )

        progress_ctrl = ft.Row(
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                progress_bar_box,
                pct_text,
            ],
        )

        src = task.task_type or "HTTP"
        src_ctrl = ft.Container(
            bgcolor="rgba(255, 255, 255, 0.05)",
            border_radius=4,
            padding=ft.Padding(5, 2, 5, 2),
            content=ft.Text(src, size=10, color=TEXT_SECONDARY),
        )

        # Inline actions with native ft.IconButton
        if task.status == DownloadStatus.DOWNLOADING:
            action_btn = ft.IconButton(
                icon=ft.Icons.PAUSE_ROUNDED,
                icon_size=15,
                icon_color="#F59E0B",
                tooltip="Metti in Pausa",
                on_click=lambda e, tid=task.task_id: self._handle_pause(tid),
            )
        elif task.status in (DownloadStatus.PAUSED, DownloadStatus.FAILED, DownloadStatus.CANCELLED):
            action_btn = ft.IconButton(
                icon=ft.Icons.PLAY_ARROW_ROUNDED,
                icon_size=15,
                icon_color="#10B981",
                tooltip="Riprendi Download",
                on_click=lambda e, tid=task.task_id: self._handle_resume(tid),
            )
        else:
            action_btn = ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                icon_size=15,
                icon_color=TEXT_SECONDARY,
                tooltip="Apri Cartella",
                on_click=lambda e, path=task.destination_dir: self.coordinator.open_folder(path),
            )

        more_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT_ROUNDED,
            icon_size=15,
            icon_color=TEXT_MUTED,
            tooltip="Altre azioni",
            items=[
                ft.PopupMenuItem(
                    content="Apri Cartella",
                    icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                    on_click=lambda e, path=task.destination_dir: self.coordinator.open_folder(path),
                ),
                ft.PopupMenuItem(
                    content="Copia Link",
                    icon=ft.Icons.COPY_ROUNDED,
                    on_click=lambda e, u=task.url: self.coordinator.copy_to_clipboard(u),
                ),
                ft.PopupMenuItem(
                    content="Elimina Download",
                    icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                    on_click=lambda e, tid=task.task_id: self._handle_delete(tid),
                ),
            ],
        )

        info_btn = ft.IconButton(
            icon=ft.Icons.INFO_OUTLINE_ROUNDED,
            icon_size=15,
            icon_color=TEXT_MUTED,
            tooltip="Dettagli Download",
            on_click=lambda e, t=task: self._handle_open_details(t),
        )

        row_ctrl = ft.Container(
            bgcolor=row_bg,
            border_radius=6,
            padding=ft.Padding(left=8, right=8, top=4, bottom=4),
            content=ft.Row(
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(width=34, content=cb),
                    ft.Container(
                        expand=4,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        content=ft.Row(
                            spacing=6,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Icon(file_icon, size=15, color=ACCENT_BLUE),
                                ft.Text(
                                    fn,
                                    size=11,
                                    color=TEXT_PRIMARY,
                                    weight=ft.FontWeight.W_500,
                                    max_lines=1,
                                    no_wrap=True,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                    expand=True,
                                ),
                            ],
                        ),
                    ),
                    ft.Container(expand=2, content=cat_badge),
                    ft.Container(expand=1, content=ft.Text(size_str, size=11, color=TEXT_PRIMARY, no_wrap=True)),
                    ft.Container(expand=2, content=status_ctrl),
                    ft.Container(expand=2, content=progress_ctrl),
                    ft.Container(expand=1, content=src_ctrl),
                    ft.Container(
                        width=75,
                        content=ft.Row(spacing=0, controls=[info_btn, action_btn, more_btn]),
                    ),
                ],
            ),
        )

        self._row_elements[task.task_id] = {
            "row_ctrl": row_ctrl,
            "speed_text": speed_text_ctrl,
            "pct_text": pct_text,
            "progress_bar_box": progress_bar_box,
            "task": task,
        }

        context_items = self._build_context_items(task)
        context_menu = ft.ContextMenu(
            content=row_ctrl,
            secondary_items=context_items,
        )

        return ft.GestureDetector(
            on_double_tap=lambda e, t=task: self._handle_open_details(t),
            on_secondary_tap=lambda e, t=task: self._handle_secondary_tap(t),
            content=context_menu,
        )

    def _build_context_items(self, task: DownloadTask) -> list[ft.PopupMenuItem]:
        items: list[ft.PopupMenuItem] = []

        # 1. Avvia / Pausa
        if task.status == DownloadStatus.DOWNLOADING:
            items.append(
                ft.PopupMenuItem(
                    content="Metti in Pausa",
                    icon=ft.Icons.PAUSE_ROUNDED,
                    on_click=lambda e, tid=task.task_id: self._handle_pause(tid),
                )
            )
        else:
            items.append(
                ft.PopupMenuItem(
                    content="Avvia Download",
                    icon=ft.Icons.PLAY_ARROW_ROUNDED,
                    on_click=lambda e, tid=task.task_id: self._handle_resume(tid),
                )
            )

        # 2. Apri cartella contenitrice
        items.append(
            ft.PopupMenuItem(
                content="Apri cartella contenitrice",
                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                on_click=lambda e, t=task: self.coordinator.open_containing_folder(t),
            )
        )

        # 3. Apri / Esegui file (solo se COMPLETED)
        is_completed = (task.status == DownloadStatus.COMPLETED)
        items.append(
            ft.PopupMenuItem(
                content="Apri / Esegui file",
                icon=ft.Icons.FILE_OPEN_ROUNDED,
                disabled=not is_completed,
                on_click=lambda e, t=task: self.coordinator.open_file(t),
            )
        )

        # 4. Copia indirizzo sorgente
        items.append(
            ft.PopupMenuItem(
                content="Copia indirizzo sorgente",
                icon=ft.Icons.COPY_ROUNDED,
                on_click=lambda e, u=task.url: self._copy_source_url(u),
            )
        )

        # 5. Aggiorna link di download...
        items.append(
            ft.PopupMenuItem(
                content="Aggiorna link di download...",
                icon=ft.Icons.SYNC_ROUNDED,
                on_click=lambda e, t=task: self._open_refresh_url_dialog(t),
            )
        )

        # 6. Verifica integrità (Hash)...
        items.append(
            ft.PopupMenuItem(
                content="Verifica integrità (Hash)...",
                icon=ft.Icons.SHIELD_ROUNDED,
                on_click=lambda e, t=task: self._open_hash_dialog(t),
            )
        )

        # 7. Visualizza dettagli
        items.append(
            ft.PopupMenuItem(
                content="Visualizza dettagli",
                icon=ft.Icons.INFO_OUTLINE_ROUNDED,
                on_click=lambda e, t=task: self._handle_open_details(t),
            )
        )

        # 8. Divisore
        items.append(
            ft.PopupMenuItem(
                content=ft.Divider(color=BORDER_COLOR, height=1),
                height=10,
            )
        )

        # 9. Rimuovi dalla lista
        items.append(
            ft.PopupMenuItem(
                content="Rimuovi dalla lista",
                icon=ft.Icons.CLOSE_ROUNDED,
                on_click=lambda e, tid=task.task_id: self._handle_delete(tid),
            )
        )

        # 10. Elimina file su disco e rimuovi
        items.append(
            ft.PopupMenuItem(
                content="Elimina file su disco e rimuovi",
                icon=ft.Icons.DELETE_FOREVER_ROUNDED,
                on_click=lambda e, t=task: self._confirm_delete_file_and_task(t),
            )
        )

        return items

    def _copy_source_url(self, url: str) -> None:
        self.coordinator.copy_to_clipboard(url)
        if self.page:
            self.page.snack_bar = ft.SnackBar(
                content=ft.Text("Indirizzo sorgente copiato negli appunti!", size=12, color="#FFFFFF"),
                bgcolor="rgba(24, 24, 27, 0.95)",
                duration=2000,
            )
            self.page.snack_bar.open = True
            safe_update(self.page)

    def _open_hash_dialog(self, task: DownloadTask) -> None:
        from ui.components.hash_dialog import HashDialog
        dialog = HashDialog(task=task, coordinator=self.coordinator)
        dialog.show(self.page)

    def _open_refresh_url_dialog(self, task: DownloadTask) -> None:
        url_input = ft.TextField(
            label="Nuovo indirizzo URL di download",
            value=task.url,
            text_size=11,
            dense=True,
            bgcolor=SURFACE_CARD,
            border_color=BORDER_COLOR,
            focused_border_color=ACCENT_BLUE,
            color=TEXT_PRIMARY,
            multiline=True,
            min_lines=2,
            max_lines=4,
        )
        dialog = ft.AlertDialog(
            bgcolor=BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=10),
            title=ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.SYNC_ROUNDED, color=ACCENT_LIGHT, size=20),
                    ft.Text("Aggiorna Link di Download", size=15, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
                ],
            ),
            content=ft.Container(
                width=520,
                content=ft.Column(
                    tight=True,
                    spacing=10,
                    controls=[
                        ft.Text(
                            "Inserisci il nuovo URL valido. I chunk e byte già scaricati su disco verranno mantenuti.",
                            size=11,
                            color=TEXT_SECONDARY,
                        ),
                        url_input,
                    ],
                ),
            ),
            actions=[
                ft.TextButton(
                    content="Annulla",
                    on_click=lambda e: self._close_dialog(dialog),
                    style=ft.ButtonStyle(color=TEXT_SECONDARY),
                ),
                ft.FilledButton(
                    content="Aggiorna e Riprendi",
                    style=ft.ButtonStyle(bgcolor=ACCENT_BLUE, color="#FFFFFF"),
                    on_click=lambda e: self._execute_update_url(task.task_id, url_input.value, dialog),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dialog)

    def _confirm_delete_file_and_task(self, task: DownloadTask) -> None:
        fn = task.filename or task.url
        dialog = ft.AlertDialog(
            bgcolor=BG_PANEL,
            shape=ft.RoundedRectangleBorder(radius=10),
            title=ft.Row(
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Icon(ft.Icons.DELETE_FOREVER_ROUNDED, color="#EF4444", size=20),
                    ft.Text("Elimina File Fisico e Rimuovi", size=15, weight=ft.FontWeight.W_600, color=TEXT_PRIMARY),
                ],
            ),
            content=ft.Container(
                width=480,
                content=ft.Text(
                    f"Sei sicuro di voler eliminare definitivamente il file '{fn}' dal disco e rimuoverlo dalla lista?",
                    size=12,
                    color=TEXT_PRIMARY,
                ),
            ),
            actions=[
                ft.TextButton(
                    content="Annulla",
                    on_click=lambda e: self._close_dialog(dialog),
                    style=ft.ButtonStyle(color=TEXT_SECONDARY),
                ),
                ft.FilledButton(
                    content="Elimina definitivamente",
                    style=ft.ButtonStyle(bgcolor="#EF4444", color="#FFFFFF"),
                    on_click=lambda e: self._execute_delete_file_and_task(task.task_id, dialog),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog(dialog)

    def _execute_update_url(self, task_id: str, new_url: str, dialog: ft.AlertDialog) -> None:
        clean = (new_url or "").strip()
        if not clean:
            return

        async def _run():
            await self.coordinator.update_task_url(task_id, clean, restart=True)
            if self.on_action:
                self.on_action()

        if self.page:
            self.page.run_task(_run)
        self._close_dialog(dialog)

    def _execute_delete_file_and_task(self, task_id: str, dialog: ft.AlertDialog) -> None:
        async def _run():
            await self.coordinator.delete_task_and_file(task_id)
            if self.on_action:
                self.on_action()

        if self.page:
            self.page.run_task(_run)
        self._close_dialog(dialog)

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

    def _handle_secondary_tap(self, task: DownloadTask) -> None:
        # Programmatic handler for right click
        pass

    def _handle_open_details(self, task: DownloadTask) -> None:
        if self.on_open_details:
            self.on_open_details(task)

    def select_all(self) -> None:
        """Selects all currently displayed tasks in the grid."""
        for t in self._current_tasks:
            self.selected_task_ids.add(t.task_id)
        self.select_all_cb.value = True
        self.set_tasks(self._current_tasks)
        if self.on_selection_changed:
            self.on_selection_changed(set(self.selected_task_ids))
        safe_update(self)

    def clear_selection(self) -> None:
        """Deselects all tasks in the grid."""
        self.selected_task_ids.clear()
        self.select_all_cb.value = False
        self.set_tasks(self._current_tasks)
        if self.on_selection_changed:
            self.on_selection_changed(set(self.selected_task_ids))
        safe_update(self)

    def _toggle_select_all(self, e: ft.ControlEvent) -> None:
        check = bool(e.control.value)
        if check:
            for t in self._current_tasks:
                self.selected_task_ids.add(t.task_id)
        else:
            for t in self._current_tasks:
                self.selected_task_ids.discard(t.task_id)
        self.set_tasks(self._current_tasks)
        if self.on_selection_changed:
            self.on_selection_changed(set(self.selected_task_ids))
        safe_update(self)

    def _toggle_row_selection(self, task_id: str, checked: bool) -> None:
        if checked:
            self.selected_task_ids.add(task_id)
        else:
            self.selected_task_ids.discard(task_id)
        all_sel = len(self._current_tasks) > 0 and all(t.task_id in self.selected_task_ids for t in self._current_tasks)
        self.select_all_cb.value = all_sel
        if self.on_selection_changed:
            self.on_selection_changed(set(self.selected_task_ids))
        safe_update(self)

    def _handle_pause(self, task_id: str) -> None:
        async def _run():
            await self.coordinator.pause_download(task_id)
            if self.on_action:
                self.on_action()
        if self.page:
            self.page.run_task(_run)

    def _handle_resume(self, task_id: str) -> None:
        async def _run():
            await self.coordinator.resume_download(task_id)
            if self.on_action:
                self.on_action()
        if self.page:
            self.page.run_task(_run)

    def _handle_delete(self, task_id: str) -> None:
        async def _run():
            await self.coordinator.cancel_download(task_id)
            self.selected_task_ids.discard(task_id)
            if self.on_action:
                self.on_action()
        if self.page:
            self.page.run_task(_run)
