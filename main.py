import asyncio
import argparse
import logging
import signal
import sys
from pathlib import Path

from config.settings import settings
from core.network.interface_detector import InterfaceDetector
from core.network.session_pool import MultiInterfaceSessionPool
from core.cnl.server import CNLServer
from core.cnl.models import CNLDecryptedPackage
from core.scheduler.state_store import StateStore
from core.scheduler.queue_manager import QueueManager
from core.extensions.manager import ExtensionManager
from core.system.paths import get_database_path, is_frozen
from ui.coordinator import UICoordinator
from ui.app import run_gui

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("resilient_downloader")


def show_interfaces() -> None:
    print("=" * 70)
    print("DETECTION SCHEDE DI RETE DISPONIBILI (MULTI-INTERFACE NET-MERGE)")
    print("=" * 70)
    adapters = InterfaceDetector.detect_all_adapters()
    for ad in adapters:
        status_str = "ATTIVA [UP]" if ad.is_up else "DISCONNESSA [DOWN]"
        if_idx = f"IfIndex={ad.if_index}" if ad.if_index is not None else ""
        gw = f"Gateway={ad.gateway}" if ad.gateway else "Nessun Gateway"
        speed = f"{ad.speed_mbps} Mbps" if ad.speed_mbps else "N/A"
        print(f"* [{status_str}] {ad.friendly_name or ad.name}")
        print(f"    IP: {ad.ip} | {if_idx} | {gw} | Velocita': {speed}")

    print("\nSCHEDE INTERNET VALIDE PER NET-MERGE AGGREGATION:")
    active = InterfaceDetector.get_active_internet_adapters()
    if not active:
        print("  [!] Nessuna scheda di rete con accesso Internet attivo rilevata.")
    for ad in active:
        prim_tag = " [INTERFACCIA PRIMARIA]" if ad.is_primary else ""
        print(f"  -> {ad.friendly_name or ad.name} ({ad.ip}) [IfIndex={ad.if_index}]{prim_tag}")
    print("=" * 70)


async def run_daemon(download_dir: Path, port: int, url_to_download: str = None) -> None:
    """Headless CLI Daemon mode."""
    download_dir.mkdir(parents=True, exist_ok=True)
    db_path = get_database_path(download_dir)

    show_interfaces()

    # 1. Initialize Network Session Pool
    session_pool = MultiInterfaceSessionPool(
        max_connections_per_host=settings.max_connections_per_host,
        total_limit=settings.max_global_connections,
    )
    await session_pool.initialize()

    # 2. Initialize State Store and Queue Manager
    state_store = StateStore(db_path)
    extension_manager = ExtensionManager()
    queue_manager = QueueManager(
        session_pool=session_pool,
        state_store=state_store,
        default_download_dir=download_dir,
        extension_manager=extension_manager,
    )
    await queue_manager.start()

    # 3. Setup CNL2 Server
    async def on_cnl_package(package: CNLDecryptedPackage) -> None:
        print("\n" + "#" * 60)
        print(f"[CNL2] Intercettato container Filecrypt: '{package.package_name}'")
        print(f"[CNL2] Trovati {len(package.urls)} link decifrati con successo!")
        if package.passwords:
            print(f"[CNL2] Password incluse: {', '.join(package.passwords)}")
        print("#" * 60)
        await queue_manager.on_cnl_package(package)

    cnl_server = CNLServer(
        host=settings.cnl.host,
        port=port,
        on_package_received=on_cnl_package,
        on_download_requested=lambda data: queue_manager.add_url(
            url=data.get("url"),
            destination_dir=Path(data.get("destination_dir")) if data.get("destination_dir") else download_dir,
            filename=data.get("filename"),
            cookies=data.get("cookies"),
            referer=data.get("referer"),
            user_agent=data.get("user_agent"),
        ),
    )
    await cnl_server.start()
    print(f"\n[CNL2 Engine] In ascolto su http://{settings.cnl.host}:{port}/flash/check")
    print(f"[Download Engine] Directory di salvataggio: {download_dir}")

    if url_to_download:
        print(f"\n[CLI] Aggiunta URL al download queue: {url_to_download}")
        await queue_manager.add_url(url_to_download)

    print("\n[+] Getly attivo in modalita' CLI. Premi Ctrl+C per arrestare.\n")

    stop_event = asyncio.Event()

    def signal_handler(*_):
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except (NotImplementedError, AttributeError):
            pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print("\nArresto in corso...")
        await cnl_server.stop()
        await queue_manager.stop()
        await session_pool.close()
        print("Arresto completato.")


async def run_desktop_app(download_dir: Path, port: int, start_minimized: bool = False) -> None:
    """Modern Desktop GUI mode with Flet."""
    download_dir.mkdir(parents=True, exist_ok=True)
    db_path = get_database_path(download_dir)

    session_pool = MultiInterfaceSessionPool(
        max_connections_per_host=settings.max_connections_per_host,
        total_limit=settings.max_global_connections,
    )
    await session_pool.initialize()

    state_store = StateStore(db_path)
    queue_manager = QueueManager(
        session_pool=session_pool,
        state_store=state_store,
        default_download_dir=download_dir,
    )
    await queue_manager.start()
    coordinator = UICoordinator(queue_manager, session_pool)

    async def on_cnl_package(package: CNLDecryptedPackage) -> None:
        logger.info("[CNL2] Received container '%s' with %d URLs", package.package_name, len(package.urls))
        await queue_manager.on_cnl_package(package)

    async def _on_download_requested(data: dict):
        if coordinator.ask_before_download and coordinator.prompt_download_handler:
            coordinator.prompt_download_handler(data)
            return {"status": "ok", "message": "Prompted user for confirmation"}
        return await queue_manager.add_url(
            url=data.get("url"),
            destination_dir=Path(data.get("destination_dir")) if data.get("destination_dir") else None,
            filename=data.get("filename"),
            cookies=data.get("cookies"),
            referer=data.get("referer"),
            user_agent=data.get("user_agent"),
        )

    async def _on_focus():
        if coordinator.focus_handler:
            try:
                res = coordinator.focus_handler()
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.debug("Error in focus_handler: %s", exc)

    cnl_server = CNLServer(
        host=settings.cnl.host,
        port=port,
        on_package_received=on_cnl_package,
        on_download_requested=_on_download_requested,
        on_focus_requested=_on_focus,
    )
    await cnl_server.start()
    logger.info("CNL2 Server listening on http://%s:%d/flash/check", settings.cnl.host, port)

    _shutdown_done = False

    async def shutdown_all():
        nonlocal _shutdown_done
        if _shutdown_done:
            return
        _shutdown_done = True
        logger.info("Shutting down core services...")
        await cnl_server.stop()
        await queue_manager.stop()
        await session_pool.close()
        logger.info("Shutdown complete.")

    try:
        await run_gui(
            queue_manager=queue_manager,
            session_pool=session_pool,
            cnl_port=port,
            on_shutdown=shutdown_all,
            coordinator=coordinator,
            start_minimized=start_minimized,
        )
    finally:
        await shutdown_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="Getly - Modular Download Manager with Net-Merge")
    parser.add_argument("--gui", action="store_true", help="Avvia l'interfaccia grafica Flet (default)")
    parser.add_argument("--cli", action="store_true", help="Forza modalita' headless da terminale")
    parser.add_argument("--minimized", action="store_true", help="Avvia l'applicazione nascosta nella System Tray all'avvio di Windows")
    parser.add_argument("--interfaces", action="store_true", help="Elenca le schede di rete e termina")
    parser.add_argument("--register-browser", action="store_true", help="Registra il Native Messaging Host per Chrome, Edge e Brave")
    parser.add_argument("--extension-id", type=str, help="ID estensione personalizzato da abilitare (opzionale)")
    parser.add_argument("--url", type=str, help="URL da scaricare direttamente da linea di comando")
    parser.add_argument("--dir", type=str, default=str(settings.storage.download_dir), help="Directory di download")
    parser.add_argument("--port", type=int, default=settings.cnl.port, help="Porta per il server CNL (default: 9666)")

    args = parser.parse_args()

    if args.interfaces:
        show_interfaces()
        sys.exit(0)

    if args.register_browser:
        from native_messaging.register import register_host
        register_host(args.extension_id or "kicdlfenjgbnjhldfagmoobkdnoniaok")
        sys.exit(0)

    # CLI mode is selected if --cli or --url is provided
    use_cli = args.cli or bool(args.url)

    if not use_cli:
        # Single-Instance check: if Getly is already running, focus existing window and exit gracefully
        try:
            import urllib.request
            req = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/focus", method="POST")
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    print(f"[SingleInstance] Un'istanza di Getly e' gia' attiva sulla porta {args.port}. Finestra portata in primo piano.")
                    sys.exit(0)
        except Exception:
            pass

    try:
        if use_cli:
            asyncio.run(run_daemon(Path(args.dir), args.port, args.url))
        else:
            if sys.platform == "win32":
                try:
                    from ui.icon_generator import set_windows_app_user_model_id
                    set_windows_app_user_model_id("Getly")
                except Exception:
                    pass
            asyncio.run(run_desktop_app(Path(args.dir), args.port, start_minimized=args.minimized))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
