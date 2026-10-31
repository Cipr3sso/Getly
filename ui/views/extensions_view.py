import asyncio
import json
from pathlib import Path
from typing import Any, Callable, Optional
import flet as ft

from core.extensions.models import InstalledExtension
from ui.coordinator import UICoordinator
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
    build_fluent_text_field,
    safe_update,
)


class InstallExtensionDialog(ft.AlertDialog):
    """
    Windows 11 Fluent Dark Modal dialog to install Gopeed extensions from a remote GitHub/web URL
    or from a local .zip/.js file.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_installed: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_installed = on_installed

        self.modal = True
        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        # Controls
        self.url_field = build_fluent_text_field(
            label="URL GitHub o Web (.zip / .js)",
            hint_text="https://github.com/user/gopeed-extension-sample",
            expand=True,
        )

        self.file_field = build_fluent_text_field(
            label="Percorso File Locale (.zip / .js)",
            hint_text="C:\\percorso\\estensione.zip",
            expand=True,
        )

        self.status_text = ft.Text("", size=11, color="#EF4444", visible=False)
        self.loading_indicator = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_BLUE, visible=False)

        # File picker for local files
        self.file_picker = ft.FilePicker(on_result=self._handle_file_picked)

        # Dialog Layout
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
                    content=ft.Icon(ft.Icons.EXTENSION_ROUNDED, color=ACCENT_BLUE, size=18),
                ),
                ft.Text("Installa Estensione Gopeed", size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ],
        )

        self.content = ft.Container(
            width=500,
            padding=ft.Padding(top=10, bottom=6, left=0, right=0),
            content=ft.Column(
                tight=True,
                spacing=14,
                controls=[
                    self.file_picker,
                    ft.Text(
                        "Puoi installare un'estensione incollando un link GitHub / Web oppure selezionando un archivio ZIP locale.",
                        size=11,
                        color=TEXT_SECONDARY,
                    ),
                    # Section 1: URL input
                    ft.Row(
                        spacing=6,
                        controls=[
                            self.url_field,
                            ft.IconButton(
                                icon=ft.Icons.CONTENT_PASTE_ROUNDED,
                                icon_color=ACCENT_LIGHT,
                                tooltip="Incolla dagli appunti",
                                on_click=self._handle_paste_url,
                            ),
                        ],
                    ),
                    ft.Row(
                        alignment=ft.MainAxisAlignment.CENTER,
                        controls=[
                            ft.Text("— oppure da file locale —", size=10, color=TEXT_MUTED),
                        ],
                    ),
                    # Section 2: Local file input
                    ft.Row(
                        spacing=6,
                        controls=[
                            self.file_field,
                            ft.IconButton(
                                icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                                icon_color=ACCENT_LIGHT,
                                tooltip="Sfoglia file locale...",
                                on_click=lambda e: self.file_picker.pick_files(
                                    dialog_title="Seleziona estensione (.zip, .js)",
                                    allowed_extensions=["zip", "js"],
                                ),
                            ),
                        ],
                    ),
                    # Status label
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[self.loading_indicator, self.status_text],
                    ),
                ],
            ),
        )

        self.actions = [
            ft.TextButton(
                "Annulla",
                style=ft.ButtonStyle(color=TEXT_SECONDARY),
                on_click=self._close_dialog,
            ),
            ft.FilledButton(
                "Installa",
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_BLUE,
                    color="#FFFFFF",
                    shape=ft.RoundedRectangleBorder(radius=6),
                ),
                on_click=self._handle_install,
            ),
        ]

    def _close_dialog(self, e: Optional[ft.ControlEvent] = None) -> None:
        self.open = False
        try:
            if self.page:
                self.page.update()
        except Exception:
            pass

    def _handle_paste_url(self, e: ft.ControlEvent) -> None:
        async def _paste():
            try:
                import pyperclip
                txt = pyperclip.paste()
                if txt:
                    self.url_field.value = txt.strip()
                    self.status_text.visible = False
                    if e.page:
                        e.page.update()
            except Exception:
                pass
        if e.page:
            e.page.run_task(_paste)

    def _handle_file_picked(self, e: ft.FilePickerResultEvent) -> None:
        if e.files and len(e.files) > 0:
            self.file_field.value = e.files[0].path
            self.url_field.value = ""
            self.status_text.visible = False
            try:
                if self.page:
                    self.page.update()
            except Exception:
                pass

    def _handle_install(self, e: ft.ControlEvent) -> None:
        async def _run():
            target_url = (self.url_field.value or "").strip()
            target_file = (self.file_field.value or "").strip()

            if not target_url and not target_file:
                self.status_text.value = "Inserisci un URL valido o seleziona un file .zip/.js"
                self.status_text.color = "#EF4444"
                self.status_text.visible = True
                if e.page:
                    e.page.update()
                return

            self.loading_indicator.visible = True
            self.status_text.value = "Installazione in corso..."
            self.status_text.color = TEXT_SECONDARY
            self.status_text.visible = True
            if e.page:
                e.page.update()

            try:
                if target_url:
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.coordinator.install_extension_from_url, target_url)
                else:
                    loop = asyncio.get_running_loop()
                    p = Path(target_file)
                    await loop.run_in_executor(None, self.coordinator.install_extension_from_file, p)

                self._close_dialog()
                if self.on_installed:
                    self.on_installed()

            except Exception as exc:
                self.loading_indicator.visible = False
                self.status_text.value = f"Errore installazione: {exc}"
                self.status_text.color = "#EF4444"
                self.status_text.visible = True
                if e.page:
                    e.page.update()

        if e.page:
            e.page.run_task(_run)


class ExtensionSettingsDialog(ft.AlertDialog):
    """
    Modal dialog to view and update settings for a Gopeed extension.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        ext: InstalledExtension,
        on_saved: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.ext = ext
        self.on_saved = on_saved

        self.modal = True
        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        current_settings = self.coordinator.extension_manager.get_extension_settings(ext.id)
        self.fields_map: dict[str, Any] = {}
        controls_list: list[ft.Control] = []

        manifest_settings = ext.manifest.settings or []
        if manifest_settings:
            for s in manifest_settings:
                if not isinstance(s, dict):
                    continue
                name = s.get("name", "")
                title = s.get("title") or name
                desc = s.get("description", "")
                val_type = s.get("type", "string")
                curr_val = current_settings.get(name, s.get("default", ""))

                if val_type == "boolean":
                    sw = ft.Switch(label=title, value=bool(curr_val), active_color=ACCENT_BLUE)
                    self.fields_map[name] = ("bool", sw)
                    controls_list.append(sw)
                elif val_type in ("number", "integer"):
                    tf = build_fluent_text_field(
                        label=title,
                        hint_text=desc,
                        value=str(curr_val) if curr_val is not None else "",
                    )
                    self.fields_map[name] = ("int", tf)
                    controls_list.append(tf)
                else:
                    tf = build_fluent_text_field(
                        label=title,
                        hint_text=desc,
                        value=str(curr_val) if curr_val is not None else "",
                    )
                    self.fields_map[name] = ("str", tf)
                    controls_list.append(tf)
        else:
            controls_list.append(
                ft.Text(
                    "Questa estensione non dichiara opzioni di configurazione nel file manifest.json.",
                    size=12,
                    color=TEXT_SECONDARY,
                )
            )

        self.title = ft.Row(
            spacing=10,
            controls=[
                ft.Icon(ft.Icons.SETTINGS_ROUNDED, color=ACCENT_BLUE, size=20),
                ft.Text(f"Impostazioni: {ext.manifest.display_name}", size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ],
        )

        self.content = ft.Container(
            width=480,
            padding=ft.Padding(10, 10, 10, 10),
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=controls_list,
            ),
        )

        self.actions = [
            ft.TextButton(
                "Chiudi",
                style=ft.ButtonStyle(color=TEXT_SECONDARY),
                on_click=self._handle_close,
            ),
            ft.FilledButton(
                "Salva Impostazioni",
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_BLUE,
                    color="#FFFFFF",
                    shape=ft.RoundedRectangleBorder(radius=6),
                ),
                on_click=self._handle_save,
            ),
        ]

    def _handle_close(self, e: ft.ControlEvent) -> None:
        if e.page:
            e.page.pop_dialog()

    def _handle_save(self, e: ft.ControlEvent) -> None:
        new_settings = {}
        for name, (kind, ctrl) in self.fields_map.items():
            if kind == "bool":
                new_settings[name] = bool(ctrl.value)
            elif kind == "int":
                try:
                    new_settings[name] = int(ctrl.value)
                except Exception:
                    new_settings[name] = ctrl.value
            else:
                new_settings[name] = str(ctrl.value or "")

        self.coordinator.extension_manager.update_extension_settings(self.ext.id, new_settings)
        if self.on_saved:
            self.on_saved()
        if e.page:
            e.page.pop_dialog()


class ExtensionTestUrlDialog(ft.AlertDialog):
    """
    Modal dialog to test a URL in real time against an installed Gopeed extension.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        ext: InstalledExtension,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.ext = ext

        self.modal = True
        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

        sample_url = ""
        for s in ext.manifest.scripts:
            if s.match_urls:
                first = s.match_urls[0]
                sample_url = first.replace("*://", "https://").replace("/*", "/item/12345")
                break

        self.url_field = build_fluent_text_field(
            label="URL di Test",
            value=sample_url,
            hint_text="https://esempio.com/video/12345",
            expand=True,
        )

        self.spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, color=ACCENT_BLUE, visible=False)
        self.status_msg = ft.Text("", size=11, color=TEXT_SECONDARY, visible=False)

        self.result_container = ft.Container(
            bgcolor=SURFACE_CARD,
            border=ft.Border.all(1, BORDER_COLOR),
            border_radius=8,
            padding=10,
            visible=False,
            height=200,
            content=ft.ListView(
                expand=True,
                controls=[
                    ft.Text("", size=11, font_family="monospace", color="#38BDF8", selectable=True)
                ],
            ),
        )

        self.title = ft.Row(
            spacing=10,
            controls=[
                ft.Icon(ft.Icons.SCIENCE_ROUNDED, color=ACCENT_BLUE, size=20),
                ft.Text(f"Test URL: {ext.manifest.display_name}", size=15, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
            ],
        )

        self.content = ft.Container(
            width=540,
            padding=ft.Padding(10, 10, 10, 10),
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=[
                    ft.Text("Incolla un URL per verificare la risoluzione da parte dell'estensione:", size=11, color=TEXT_SECONDARY),
                    self.url_field,
                    ft.Row(spacing=8, controls=[self.spinner, self.status_msg]),
                    self.result_container,
                ],
            ),
        )

        self.actions = [
            ft.TextButton(
                "Chiudi",
                style=ft.ButtonStyle(color=TEXT_SECONDARY),
                on_click=lambda e: e.page.pop_dialog() if e.page else None,
            ),
            ft.FilledButton(
                "Esegui Test",
                icon=ft.Icons.PLAY_ARROW_ROUNDED,
                style=ft.ButtonStyle(
                    bgcolor=ACCENT_BLUE,
                    color="#FFFFFF",
                    shape=ft.RoundedRectangleBorder(radius=6),
                ),
                on_click=self._handle_run_test,
            ),
        ]

    def _handle_run_test(self, e: ft.ControlEvent) -> None:
        test_url = (self.url_field.value or "").strip()
        if not test_url:
            self.status_msg.value = "Inserisci un URL valido."
            self.status_msg.color = "#EF4444"
            self.status_msg.visible = True
            if e.page:
                e.page.update()
            return

        async def _run():
            self.spinner.visible = True
            self.status_msg.value = "Esecuzione onResolve(ctx) in corso..."
            self.status_msg.color = TEXT_SECONDARY
            self.status_msg.visible = True
            self.result_container.visible = False
            if e.page:
                e.page.update()

            try:
                script = None
                for s in self.ext.manifest.scripts:
                    if s.event == "onResolve":
                        entry_file = self.ext.dir_path / s.entry
                        if entry_file.exists():
                            script = entry_file.read_text(encoding="utf-8")
                            break

                if not script:
                    raise FileNotFoundError(f"Entry script non trovato per l'estensione {self.ext.id}")

                settings = self.coordinator.extension_manager.get_extension_settings(self.ext.id)
                res = await asyncio.to_thread(
                    self.coordinator.extension_manager.sandbox.resolve_url,
                    script_code=script,
                    raw_url=test_url,
                    settings=settings,
                    ext_id=self.ext.id,
                )

                self.spinner.visible = False
                self.result_container.visible = True

                if res and (res.url or res.files):
                    self.status_msg.value = "✅ Risoluzione completata con successo!"
                    self.status_msg.color = "#10B981"
                    res_dict = {
                        "name": res.filename,
                        "url": res.url,
                        "headers": res.headers,
                        "files": [
                            {"name": f.name, "url": f.url, "size": f.size, "headers": f.headers}
                            for f in res.files
                        ],
                        "extra": res.extra,
                    }
                    self.result_container.content.controls[0].value = json.dumps(res_dict, indent=2)
                    self.result_container.content.controls[0].color = "#10B981"
                else:
                    self.status_msg.value = "⚠️ Nessun risultato restituito (ctx.res è null o url vuoto)."
                    self.status_msg.color = "#F59E0B"
                    self.result_container.content.controls[0].value = "null"
                    self.result_container.content.controls[0].color = "#F59E0B"

            except Exception as exc:
                self.spinner.visible = False
                self.status_msg.value = f"❌ Errore JS / Sandbox: {exc}"
                self.status_msg.color = "#EF4444"
                self.result_container.visible = True
                self.result_container.content.controls[0].value = str(exc)
                self.result_container.content.controls[0].color = "#EF4444"

            if e.page:
                e.page.update()

        if e.page:
            e.page.run_task(_run)


class ExtensionsView(ft.Container):
    """
    Management view for Gopeed JavaScript extensions:
    - Lists all installed extensions with version, description, and match patterns.
    - Enable / Disable toggles per extension.
    - Delete button to uninstall an extension.
    - Header with "+ Installa Estensione" button.
    Windows 11 Fluent Dark theme.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_extension_updated: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.coordinator = coordinator
        self.on_extension_updated = on_extension_updated
        self.expand = True
        self.bgcolor = BG_WINDOW
        self.padding = ft.Padding(left=20, right=20, top=14, bottom=20)

        self.list_view = ft.ListView(
            expand=True,
            spacing=10,
            padding=ft.Padding(top=8, bottom=20),
        )

        self.content = ft.Column(
            expand=True,
            spacing=14,
            controls=[
                self._build_header(),
                ft.Divider(color=BORDER_COLOR, height=1),
                self.list_view,
            ],
        )

        self.refresh_extensions()

    def _build_header(self) -> ft.Row:
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(
                    spacing=12,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Container(
                            width=36,
                            height=36,
                            border_radius=18,
                            bgcolor="rgba(0, 120, 212, 0.15)",
                            border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                            alignment=ft.Alignment(0, 0),
                            content=ft.Icon(ft.Icons.EXTENSION_ROUNDED, color=ACCENT_BLUE, size=18),
                        ),
                        ft.Column(
                            spacing=1,
                            controls=[
                                ft.Text(
                                    "Estensioni Gopeed (QuickJS Sandbox)",
                                    size=16,
                                    weight=ft.FontWeight.BOLD,
                                    color=TEXT_PRIMARY,
                                ),
                                ft.Text(
                                    "Risolvi link diretti e flussi video da hoster complessi prima del download.",
                                    size=11,
                                    color=TEXT_SECONDARY,
                                ),
                            ],
                        ),
                    ],
                ),
                ft.FilledButton(
                    icon=ft.Icons.ADD_ROUNDED,
                    content="Installa Estensione",
                    style=ft.ButtonStyle(
                        bgcolor=ACCENT_BLUE,
                        color="#FFFFFF",
                        shape=ft.RoundedRectangleBorder(radius=6),
                    ),
                    on_click=self._open_install_dialog,
                ),
            ],
        )

    def refresh_extensions(self) -> None:
        """Reloads extensions from coordinator and builds cards."""
        self.list_view.controls.clear()
        extensions = self.coordinator.get_extensions()

        if not extensions:
            self.list_view.controls.append(
                ft.Container(
                    alignment=ft.Alignment(0, 0),
                    padding=60,
                    border_radius=12,
                    bgcolor=SURFACE_CARD,
                    border=ft.Border.all(1, BORDER_COLOR),
                    content=ft.Column(
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
                                content=ft.Icon(ft.Icons.EXTENSION_OFF_ROUNDED, size=28, color=ACCENT_BLUE),
                            ),
                            ft.Text("Nessuna estensione installata", size=15, color=TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
                            ft.Text(
                                "Installa estensioni Gopeed (.zip o URL GitHub) per supportare hoster complessi.",
                                size=12,
                                color=TEXT_SECONDARY,
                                text_align=ft.TextAlign.CENTER,
                                width=480,
                            ),
                            ft.FilledButton(
                                icon=ft.Icons.ADD_ROUNDED,
                                content="Installa la prima estensione",
                                style=ft.ButtonStyle(
                                    bgcolor=ACCENT_BLUE,
                                    color="#FFFFFF",
                                    shape=ft.RoundedRectangleBorder(radius=6),
                                ),
                                on_click=self._open_install_dialog,
                            ),
                        ],
                    ),
                )
            )
        else:
            for ext in extensions:
                self.list_view.controls.append(self._build_extension_card(ext))

    def _build_extension_card(self, ext: InstalledExtension) -> ft.Container:
        m = ext.manifest

        match_chips: list[ft.Control] = []
        all_patterns = []
        for s in m.scripts:
            if s.match_urls:
                all_patterns.extend(s.match_urls)
            else:
                all_patterns.append("* (Tutti i siti)")

        for p in all_patterns[:6]:
            match_chips.append(
                ft.Container(
                    bgcolor="rgba(0, 120, 212, 0.15)",
                    border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                    border_radius=6,
                    padding=ft.Padding(7, 2, 7, 2),
                    content=ft.Text(p, size=9, color=ACCENT_LIGHT, weight=ft.FontWeight.BOLD),
                )
            )
        if len(all_patterns) > 6:
            match_chips.append(
                ft.Text(f"+ altri {len(all_patterns) - 6}", size=9, color=TEXT_MUTED)
            )

        card = ft.Container(
            bgcolor=SURFACE_CARD,
            border_radius=10,
            padding=ft.Padding(left=18, right=18, top=14, bottom=14),
            border=ft.Border.all(1, BORDER_COLOR),
            shadow=GLASS_CARD_SHADOW,
            content=ft.Column(
                spacing=8,
                controls=[
                    # Top Row: Icon + Name/Meta + Switch + Delete
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Row(
                                spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.Container(
                                        width=36,
                                        height=36,
                                        border_radius=18,
                                        bgcolor="rgba(0, 120, 212, 0.15)",
                                        border=ft.Border.all(1, "rgba(0, 120, 212, 0.35)"),
                                        alignment=ft.Alignment(0, 0),
                                        content=ft.Icon(ft.Icons.EXTENSION_ROUNDED, color=ACCENT_LIGHT, size=18),
                                    ),
                                    ft.Column(
                                        spacing=2,
                                        controls=[
                                            ft.Row(
                                                spacing=8,
                                                tight=True,
                                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                                controls=[
                                                    ft.Text(m.display_name, size=13, weight=ft.FontWeight.BOLD, color=TEXT_PRIMARY),
                                                    ft.Container(
                                                        bgcolor="rgba(255, 255, 255, 0.08)",
                                                        border_radius=4,
                                                        padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                                                        content=ft.Text(f"v{m.version}", size=9, color=TEXT_SECONDARY),
                                                    ),
                                                    ft.Text(f"di {m.author or 'Autore Sconosciuto'}", size=10, color=TEXT_MUTED),
                                                ],
                                            ),
                                            ft.Text(
                                                m.description or "Nessuna descrizione specificata.",
                                                size=11,
                                                color=TEXT_SECONDARY,
                                                max_lines=2,
                                                overflow=ft.TextOverflow.ELLIPSIS,
                                                width=500,
                                            ),
                                        ],
                                    ),
                                ],
                            ),
                            ft.Row(
                                spacing=4,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                controls=[
                                    ft.IconButton(
                                        icon=ft.Icons.SCIENCE_ROUNDED,
                                        icon_color=ACCENT_LIGHT,
                                        icon_size=18,
                                        tooltip="Testa URL in tempo reale",
                                        on_click=lambda e, target_ext=ext: self._open_test_dialog(target_ext),
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.SETTINGS_ROUNDED,
                                        icon_color=ACCENT_LIGHT,
                                        icon_size=18,
                                        tooltip="Impostazioni estensione",
                                        on_click=lambda e, target_ext=ext: self._open_settings_dialog(target_ext),
                                    ),
                                    ft.Switch(
                                        value=ext.enabled,
                                        active_color=ACCENT_BLUE,
                                        tooltip="Attiva / Disattiva estensione",
                                        on_change=lambda e, eid=ext.id: self._toggle_extension(eid, e.control.value),
                                    ),
                                    ft.IconButton(
                                        icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                        icon_color=TEXT_MUTED,
                                        icon_size=18,
                                        tooltip="Elimina Estensione",
                                        on_click=lambda e, eid=ext.id: self._delete_extension(eid),
                                    ),
                                ],
                            ),
                        ],
                    ),
                    # Bottom Row: Match Patterns
                    ft.Row(
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Text("Siti supportati:", size=10, color=TEXT_MUTED),
                            ft.Row(spacing=4, wrap=True, controls=match_chips),
                        ],
                    ),
                ],
            ),
        )
        return card

    def _toggle_extension(self, ext_id: str, enabled: bool) -> None:
        if enabled:
            self.coordinator.enable_extension(ext_id)
        else:
            self.coordinator.disable_extension(ext_id)
        self.refresh_extensions()
        if self.on_extension_updated:
            self.on_extension_updated()
        safe_update(self)

    def _delete_extension(self, ext_id: str) -> None:
        self.coordinator.delete_extension(ext_id)
        self.refresh_extensions()
        if self.on_extension_updated:
            self.on_extension_updated()
        safe_update(self)

    def _open_install_dialog(self, e: ft.ControlEvent) -> None:
        dialog = InstallExtensionDialog(
            coordinator=self.coordinator,
            on_installed=self._on_extension_installed,
        )
        if self.page:
            self.page.show_dialog(dialog)

    def _open_settings_dialog(self, ext: InstalledExtension) -> None:
        dialog = ExtensionSettingsDialog(
            coordinator=self.coordinator,
            ext=ext,
            on_saved=self._on_extension_installed,
        )
        if self.page:
            self.page.show_dialog(dialog)

    def _open_test_dialog(self, ext: InstalledExtension) -> None:
        dialog = ExtensionTestUrlDialog(
            coordinator=self.coordinator,
            ext=ext,
        )
        if self.page:
            self.page.show_dialog(dialog)

    def _on_extension_installed(self) -> None:
        self.refresh_extensions()
        if self.on_extension_updated:
            self.on_extension_updated()
        safe_update(self)
