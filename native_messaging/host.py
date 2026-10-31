#!/usr/bin/env python3
"""
Native Messaging Host for Resilient Heisenberg Download Manager.
Communicates with Chromium-based browsers (Chrome, Edge, Brave) via standard stdio protocol
(4-byte length prefix in native byte order followed by UTF-8 encoded JSON).
Forwards incoming download requests to the local daemon (http://127.0.0.1:9666/api/add_download)
or falls back directly to atomic SQLite database insertion if the daemon is offline.
"""

import json
import logging
import os
from pathlib import Path
import struct
import sys
import urllib.error
import urllib.request
import uuid
from typing import Optional, Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.scheduler.state_store import StateStore
from core.scheduler.task import DownloadTask, DownloadStatus
from config.settings import settings

# Setup file logging for troubleshooting native messaging calls outside the terminal
LOG_FILE = PROJECT_ROOT / "native_host.log"
logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [NativeHost] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("NativeHost")


def read_message(stream=None) -> Optional[Dict[str, Any]]:
    """
    Reads a single native message from stream (default: sys.stdin.buffer).
    Format: 4-byte unsigned integer (length) + JSON string in UTF-8.
    """
    if stream is None:
        stream = sys.stdin.buffer

    raw_length = stream.read(4)
    if len(raw_length) < 4:
        return None

    message_length = struct.unpack("@I", raw_length)[0]
    if message_length == 0 or message_length > 10 * 1024 * 1024:
        logger.warning("Invalid message length received: %d", message_length)
        return None

    raw_message = stream.read(message_length)
    if len(raw_message) < message_length:
        logger.warning("Incomplete message received: got %d bytes, expected %d", len(raw_message), message_length)
        return None

    try:
        return json.loads(raw_message.decode("utf-8"))
    except Exception as exc:
        logger.error("Failed to decode JSON message: %s", exc)
        return None


def send_message(message: Dict[str, Any], stream=None) -> None:
    """
    Writes a single native message to stream (default: sys.stdout.buffer).
    Format: 4-byte unsigned integer (length) + JSON string in UTF-8.
    """
    if stream is None:
        stream = sys.stdout.buffer

    encoded = json.dumps(message).encode("utf-8")
    stream.write(struct.pack("@I", len(encoded)))
    stream.write(encoded)
    stream.flush()


def fallback_save_to_db(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fallback mechanism when local daemon (port 9666) is offline.
    Directly writes a new task with PENDING status to SQLite state database
    so it gets processed as soon as the download manager opens.
    """
    download_dir = Path(payload.get("destination_dir") or settings.storage.download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    db_path = download_dir / ".state.db"

    try:
        store = StateStore(db_path)
        task = DownloadTask(
            url=payload["url"],
            destination_dir=download_dir,
            filename=payload.get("filename"),
            cookies=payload.get("cookies"),
            referer=payload.get("referer"),
            user_agent=payload.get("user_agent"),
            status=DownloadStatus.PENDING,
        )
        store.save_task(task)
        logger.info("Saved task directly to DB (offline fallback): %s -> %s", task.task_id, task.url)
        return {
            "status": "ok",
            "task_id": task.task_id,
            "filename": task.filename,
            "fallback": True,
            "message": "Saved to database (daemon offline)",
        }
    except Exception as exc:
        logger.error("Offline DB fallback failed: %s", exc)
        return {
            "status": "error",
            "message": f"Offline DB fallback failed: {exc}",
        }


def forward_download(payload: Dict[str, Any], daemon_url: str = "http://127.0.0.1:9666/api/add_download") -> Dict[str, Any]:
    """
    Handles an intercepted download payload by sending it to the daemon API,
    or falling back to direct SQLite persistence if unreachable.
    """
    if payload.get("action") == "ping":
        return {
            "status": "ok",
            "message": "Resilient Downloader Native Host online",
            "pid": os.getpid(),
        }

    url = payload.get("url")
    if not url or not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
        return {
            "status": "error",
            "message": "Missing or invalid 'url' (must be http:// or https://)",
        }

    logger.info("Forwarding download: %s (filename: %s)", url, payload.get("filename"))

    # 1. Attempt POST to running daemon
    try:
        data_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            daemon_url,
            data=data_bytes,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            if resp.status == 200:
                resp_json = json.loads(resp.read().decode("utf-8"))
                logger.info("Daemon accepted download: %s", resp_json)
                return resp_json
    except (urllib.error.URLError, TimeoutError, ConnectionRefusedError, OSError) as exc:
        logger.info("Daemon unreachable (%s), activating SQLite offline fallback...", exc)

    # 2. Fallback to direct DB insertion
    return fallback_save_to_db(payload)


def main() -> None:
    logger.info("Native Messaging Host started (PID: %d)", os.getpid())
    while True:
        try:
            message = read_message()
            if message is None:
                # End of input stream (browser closed the connection)
                break

            response = forward_download(message)
            send_message(response)
        except Exception as exc:
            logger.error("Unhandled error in native messaging loop: %s", exc)
            send_message({"status": "error", "message": str(exc)})
            break

    logger.info("Native Messaging Host exiting.")


if __name__ == "__main__":
    main()
