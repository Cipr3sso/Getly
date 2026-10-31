from typing import Callable, Optional
import flet as ft

from ui.coordinator import UICoordinator
from ui.theme import (
    BG_PANEL,
    SURFACE_CARD,
    BORDER_COLOR,
    ACCENT_BLUE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
)


class ClipboardDownloadDialog(ft.AlertDialog):
    """
    Non-intrusive Windows 11 Fluent Dark prompt triggered when a downloadable link is detected in the clipboard.
    """

    def __init__(
        self,
        url: str,
        coordinator: UICoordinator,
        on_confirmed: Optional[Callable[[], None]] = None,
    ):
        super().__init__()
        self.url = url
        self.coordinator = coordinator
        self.on_confirmed = on_confirmed

        self.bgcolor = BG_PANEL
        self.shape = ft.RoundedRectangleBorder(radius=10)

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
                    content=ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, color=ACCENT_BLUE, size=18),
                ),
                ft.Text("Download Rilevato negli Appunti", weight=ft.FontWeight.BOLD, size=15, color=TEXT_PRIMARY),
            ],
        )

        display_name = url.split("/")[-1].split("?")[0] if "/" in url else url

        self.content = ft.Container(
            width=480,
            padding=ft.Padding(top=8, bottom=8, left=4, right=4),
            content=ft.Column(
                tight=True,
                spacing=12,
                controls=[
                    ft.Text(
                        "È stato copiato un link di download valido. Vuoi avviare lo scaricamento con aggregazione di banda?",
                        size=12,
                        color=TEXT_SECONDARY,
                    ),
                    ft.Container(
                        bgcolor=SURFACE_CARD,
                        padding=12,
                        border_radius=8,
                        border=ft.Border.all(1, BORDER_COLOR),
                        content=ft.Column(
                            spacing=4,
                            controls=[
                                ft.Text(display_name, weight=ft.FontWeight.BOLD, size=13, color=TEXT_PRIMARY),
                                ft.Text(
                                    url,
                                    size=11,
                                    color=TEXT_MUTED,
                                    max_lines=2,
                                    overflow=ft.TextOverflow.ELLIPSIS,
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )

        submit_btn = ft.FilledButton(
            icon=ft.Icons.ROCKET_LAUNCH_ROUNDED,
            content="Scarica Subito",
            style=ft.ButtonStyle(
                bgcolor=ACCENT_BLUE,
                color="#FFFFFF",
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=self._handle_download,
        )

        ignore_btn = ft.TextButton(
            content="Ignora",
            style=ft.ButtonStyle(color=TEXT_SECONDARY),
            on_click=self._handle_ignore,
        )

        self.actions = [ignore_btn, submit_btn]
        self.actions_alignment = ft.MainAxisAlignment.END

    def _handle_ignore(self, e: ft.ControlEvent) -> None:
        if e.page:
            e.page.pop_dialog()

    def _handle_download(self, e: ft.ControlEvent) -> None:
        async def _add():
            await self.coordinator.add_download(self.url)
            if self.on_confirmed:
                self.on_confirmed()

        if e.page:
            e.page.run_task(_add)
            e.page.pop_dialog()
