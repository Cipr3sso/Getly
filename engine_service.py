#!/usr/bin/env python3
"""
Getly Headless Engine Service & Named Pipe IPC Server.
Runs the core download engine, CNL2 server, and IPC pipe server as a detached background process.

Usage:
  python engine_service.py                  # Starts the headless engine daemon
  python engine_service.py --test-client    # Connects to the engine pipe and tests system.get_stats
"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time

from config.settings import settings
from core.cnl.models import CNLDecryptedPackage
from core.cnl.server import CNLServer
from core.extensions.manager import ExtensionManager
from core.ipc.pipe_server import NamedPipeServer, DEFAULT_PIPE_NAME, connect_pipe_client
from core.ipc.telemetry import TelemetryDispatcher
from core.network.interface_detector import InterfaceDetector
from core.network.session_pool import MultiInterfaceSessionPool
from core.scheduler.queue_manager import QueueManager
from core.scheduler.state_store import StateStore
from core.system.paths import get_database_path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("getly.engine_service")


async def run_test_client(pipe_name: str, custom_cmd: str = None) -> int:
    """CLI test client: connects to Getly Named Pipe, sends RPC request, prints response."""
    print("=" * 65)
    print(f"GETLY IPC TEST CLIENT -> Connessione a {pipe_name}...")
    print("=" * 65)

    try:
        reader, writer = await connect_pipe_client(pipe_name=pipe_name, timeout=5.0)
    except Exception as exc:
        print(f"[!] Errore di connessione alla Named Pipe: {exc}")
        print("    Verifica che 'engine_service.py' sia attivo in background.")
        return 1

    print("[+] Connesso con successo al server IPC Getly!")

    # Prepare command
    if custom_cmd:
        if custom_cmd.startswith("{"):
            req_data = json.loads(custom_cmd)
            if "id" not in req_data:
                req_data["id"] = "cli-test-cmd"
        else:
            req_data = {"id": "cli-test-cmd", "method": custom_cmd, "params": {}}
    else:
        req_data = {"id": "cli-test-stats", "method": "system.get_stats", "params": {}}

    payload = (json.dumps(req_data) + "\n").encode("utf-8")
    writer.write(payload)
    await writer.drain()
    print(f"[*] Inviata richiesta RPC: {req_data['method']}")

    # Read lines until we find the response matching our request id
    while True:
        line = await reader.readline()
        if not line:
            print("[!] Connessione interrotta dal server prima della risposta.")
            writer.close()
            await writer.wait_closed()
            return 1

        try:
            msg = json.loads(line.decode("utf-8").strip())
        except Exception:
            continue

        # If it's a broadcast tick event, print compact notification and continue waiting
        if msg.get("event") == "tick":
            stats = msg.get("stats", {})
            dl_mb = round(stats.get("download_speed_bps", 0) / (1024 * 1024), 2)
            ul_kb = round(stats.get("upload_speed_bps", 0) / 1024, 2)
            print(f"    [TELEMETRIA TICK] DL: {dl_mb} MB/s | UL: {ul_kb} KB/s | Task modificati: {len(msg.get('tasks', []))}")
            continue

        # Found RPC response
        if msg.get("id") == req_data["id"]:
            print("\n" + "=" * 65)
            print("RISPOSTA SERVER RPC:")
            print("=" * 65)
            print(json.dumps(msg, indent=2, ensure_ascii=False))
            print("=" * 65)
            break

    writer.close()
    await writer.wait_closed()
    return 0


async def run_engine_service(download_dir: Path, port: int, pipe_name: str) -> None:
    """Main headless engine daemon lifecycle."""
    download_dir.mkdir(parents=True, exist_ok=True)
    db_path = get_database_path(download_dir)

    print("=" * 70)
    print("GETLY HEADLESS ENGINE SERVICE (DECOUPLED ARCHITECTURE)")
    print("=" * 70)
    print(f"Directory Download:  {download_dir}")
    print(f"Database SQLite:     {db_path}")
    print(f"Named Pipe IPC:      {pipe_name}")
    print(f"Porta CNL2:          {port}")
    print("=" * 70)

    # 1. Network Session Pool
    session_pool = MultiInterfaceSessionPool(
        max_connections_per_host=settings.max_connections_per_host,
        total_limit=settings.max_global_connections,
    )
    await session_pool.initialize()

    # 2. State Store, Extension Manager & Queue Manager
    state_store = StateStore(db_path)
    extension_manager = ExtensionManager()
    queue_manager = QueueManager(
        session_pool=session_pool,
        state_store=state_store,
        default_download_dir=download_dir,
        extension_manager=extension_manager,
    )
    await queue_manager.start()

    # 3. CNL2 Container Server
    async def on_cnl_package(package: CNLDecryptedPackage) -> None:
        logger.info("[CNL2] Intercettato container Filecrypt '%s' con %d URLs", package.package_name, len(package.urls))
        await queue_manager.on_cnl_package(package)

    async def on_download_req(data: dict):
        return await queue_manager.add_url(
            url=data.get("url"),
            destination_dir=Path(data.get("destination_dir")) if data.get("destination_dir") else download_dir,
            filename=data.get("filename"),
            cookies=data.get("cookies"),
            referer=data.get("referer"),
            user_agent=data.get("user_agent"),
            category=data.get("category"),
        )

    cnl_server = CNLServer(
        host=settings.cnl.host,
        port=port,
        on_package_received=on_cnl_package,
        on_download_requested=on_download_req,
    )
    await cnl_server.start()
    logger.info("CNL2 Server in ascolto su http://%s:%d/flash/check", settings.cnl.host, port)

    # 4. IPC Pipe Server & 10 Hz Telemetry Dispatcher
    pipe_server = NamedPipeServer(
        queue_manager=queue_manager,
        session_pool=session_pool,
        pipe_name=pipe_name,
    )
    await pipe_server.start()

    telemetry = TelemetryDispatcher(
        pipe_server=pipe_server,
        queue_manager=queue_manager,
        session_pool=session_pool,
        hz=10.0,
    )
    telemetry.start()

    logger.info("Getly Engine Headless pronto e in ascolto. In attesa di comandi IPC / CNL2...")

    # 5. Shutdown Signal Handling
    stop_event = asyncio.Event()

    def _trigger_stop(*_):
        logger.info("Ricevuto segnale di terminazione. Avvio arresto aggraziato...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _trigger_stop)
        except (NotImplementedError, AttributeError):
            pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Arresto componenti in corso...")
        await telemetry.stop()
        await pipe_server.stop()
        await cnl_server.stop()
        await queue_manager.stop()

        # Close libtorrent session if active
        try:
            from core.engine.torrent_engine import TorrentSessionManager, LIBTORRENT_AVAILABLE
            if LIBTORRENT_AVAILABLE and TorrentSessionManager._instance:
                TorrentSessionManager._instance.close()
                logger.info("Sessione BitTorrent chiusa con successo.")
        except Exception as exc:
            logger.debug("Chiusura libtorrent sessione: %s", exc)

        await session_pool.close()
        logger.info("Getly Engine Headless arrestato correttamente senza perdite di dati.")


def main():
    parser = argparse.ArgumentParser(description="Getly Headless Engine Service & Named Pipe IPC Server")
    parser.add_argument("--pipe", default=DEFAULT_PIPE_NAME, help="Named Pipe address (default: \\\\.\\pipe\\GetlyEngine)")
    parser.add_argument("--port", type=int, default=settings.cnl.port, help=f"CNL2 HTTP Port (default: {settings.cnl.port})")
    parser.add_argument("--download-dir", default=str(settings.storage.download_dir), help="Cartella download predefinita")
    parser.add_argument("--test-client", action="store_true", help="Esegue un test client rapido su Named Pipe interrogando system.get_stats")
    parser.add_argument("--test-client-cmd", default=None, help="Invia un comando RPC personalizzato con il test client")

    args = parser.parse_args()

    if args.test_client or args.test_client_cmd:
        code = asyncio.run(run_test_client(pipe_name=args.pipe, custom_cmd=args.test_client_cmd))
        sys.exit(code)

    download_dir = Path(args.download_dir)
    try:
        asyncio.run(run_engine_service(download_dir=download_dir, port=args.port, pipe_name=args.pipe))
    except KeyboardInterrupt:
        print("\nProcesso terminato dall'utente.")


if __name__ == "__main__":
    main()
