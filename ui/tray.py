import asyncio
import logging
from typing import Any, Callable, Optional
from PIL import Image, ImageDraw
import pystray
import flet as ft

from ui.coordinator import UICoordinator
from ui.icon_generator import get_rocket_icon_image

logger = logging.getLogger(__name__)


def create_tray_icon_image() -> Image.Image:
    """Returns the official Getly purple-magenta rocket squircle icon for the system tray."""
    return get_rocket_icon_image(64)


class SystemTrayManager:
    """
    Manages the Windows System Tray icon (Pystray), context menu actions,
    Windows Toast notifications, and intercepts window close/minimize events.
    Executes actions thread-safely on the main asyncio event loop.
    """

    def __init__(
        self,
        coordinator: UICoordinator,
        on_exit: Optional[Callable[[], Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self.coordinator = coordinator
        self.on_exit = on_exit
        self.loop = loop

        self.page: Optional[ft.Page] = None
        self.icon: Optional[pystray.Icon] = None
        self._is_running = False

    def setup(self, page: ft.Page) -> None:
        """Configures window event interception and launches the tray icon."""
        self.page = page
        if not self.loop:
            try:
                self.loop = asyncio.get_running_loop()
            except RuntimeError:
                self.loop = getattr(page, "loop", None)

        # Intercept window close to minimize to tray
        page.window.prevent_close = True
        page.window.on_event = self._on_window_event

        # Build pystray icon with menu
        menu = pystray.Menu(
            pystray.MenuItem("Apri Getly", self._menu_show_window, default=True),
            pystray.MenuItem("Metti in Pausa Tutti", self._menu_pause_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Esci da Getly", self._menu_quit),
        )

        self.icon = pystray.Icon(
            name="Getly",
            icon=create_tray_icon_image(),
            title="Getly",
            menu=menu,
        )

        try:
            self.icon.run_detached()
            self._is_running = True
            logger.info("System Tray icon started detached.")
        except Exception as exc:
            logger.warning("Could not initialize system tray: %s", exc)

    def _on_window_event(self, e: ft.WindowEvent) -> None:
        """Handles Flet native window events."""
        event_type = e.type if isinstance(e.type, str) else getattr(e.type, "value", str(e.type))
        if event_type == "close":
            # Hide window and notify in tray
            if self.page:
                self.page.window.visible = False
                self.page.update()
                self.notify(
                    title="Getly",
                    message="L'applicazione continua a scaricare in background nella System Tray.",
                )

    def notify(self, title: str, message: str) -> None:
        """Sends a native Windows Toast notification via Pystray."""
        if self.icon:
            try:
                self.icon.notify(message, title)
            except Exception as exc:
                logger.debug("Failed to dispatch tray notification: %s", exc)

    def _menu_show_window(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if not self.page:
            return

        async def _show():
            self.page.window.visible = True
            await self.page.window.to_front()
            self.page.update()

        target_loop = self.loop or getattr(self.page, "loop", None)
        if target_loop and target_loop.is_running():
            asyncio.run_coroutine_threadsafe(_show(), target_loop)
        else:
            self.page.run_task(_show)

    def _menu_pause_all(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if not self.page:
            return

        async def _pause():
            await self.coordinator.pause_all()

        target_loop = self.loop or getattr(self.page, "loop", None)
        if target_loop and target_loop.is_running():
            asyncio.run_coroutine_threadsafe(_pause(), target_loop)
        else:
            self.page.run_task(_pause)

    def _menu_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        if not self.page:
            return

        async def _quit():
            logger.info("Tray menu requested application exit...")
            if self.icon:
                try:
                    self.icon.stop()
                except Exception:
                    pass

            if self.on_exit:
                try:
                    res = self.on_exit()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:
                    logger.debug("Error in on_exit callback: %s", exc)

            try:
                await self.page.window.destroy()
            except Exception as exc:
                logger.debug("Error in window.destroy: %s", exc)

        target_loop = self.loop or getattr(self.page, "loop", None)
        if target_loop and target_loop.is_running():
            asyncio.run_coroutine_threadsafe(_quit(), target_loop)
        else:
            self.page.run_task(_quit)

    def stop(self) -> None:
        """Stops the system tray icon loop."""
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        self._is_running = False
