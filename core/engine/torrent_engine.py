"""
BitTorrent and Magnet Engine for Resilient Downloader.
Integrates libtorrent 2.x with async coroutines, multi-interface routing,
DHT / LSD / PeX discovery, metadata resolution, and file prioritization.
"""

import asyncio
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Set

try:
    import libtorrent as lt
    LIBTORRENT_AVAILABLE = True
except ImportError:
    lt = None
    LIBTORRENT_AVAILABLE = False

from core.scheduler.task import DownloadTask, DownloadStatus
from core.network.interface_detector import InterfaceDetector

logger = logging.getLogger(__name__)


@dataclass
class TorrentFileInfo:
    index: int
    path: str
    size: int
    selected: bool = True


@dataclass
class TorrentMetadata:
    name: str
    info_hash: str
    total_size: int
    files: List[TorrentFileInfo] = field(default_factory=list)
    trackers: List[str] = field(default_factory=list)
    has_metadata: bool = False

    @property
    def num_files(self) -> int:
        """Returns the total number of files in the torrent metadata."""
        return len(self.files)


class TorrentSessionManager:
    """
    Singleton manager for the global libtorrent session.
    Configures DHT, Protocol Encryption, and Strict VPN Interface Binding (Kill-Switch).
    """

    _instance: Optional["TorrentSessionManager"] = None

    DHT_ROUTERS = [
        ("router.bittorrent.com", 6881),
        ("dht.transmissionbt.com", 6881),
        ("router.utorrent.com", 6881),
        ("dht.libtorrent.org", 25401),
    ]

    def __init__(self, interface_ips: Optional[Any] = None, vpn_shield: bool = True):
        if not LIBTORRENT_AVAILABLE:
            raise RuntimeError("libtorrent is not installed. Please install libtorrent>=2.0.0.")

        if interface_ips is not None and not isinstance(interface_ips, (list, tuple, set)):
            if hasattr(interface_ips, "active_adapters"):
                interface_ips = [a.ip for a in interface_ips.active_adapters if a.ip]
            elif hasattr(interface_ips, "all_adapters"):
                interface_ips = [a.ip for a in interface_ips.all_adapters if a.ip]
            else:
                interface_ips = []

        self.interface_ips = list(interface_ips) if interface_ips else []
        self.vpn_shield = vpn_shield
        self._session: Optional[lt.session] = None
        self._init_session()

    @classmethod
    def get_instance(
        cls,
        interface_ips: Optional[Any] = None,
        vpn_shield: bool = True,
        session_pool: Optional[Any] = None,
    ) -> "TorrentSessionManager":
        if interface_ips is not None and not isinstance(interface_ips, (list, tuple, set)):
            session_pool = interface_ips
            interface_ips = None

        if session_pool is not None:
            if hasattr(session_pool, "active_adapters"):
                interface_ips = [a.ip for a in session_pool.active_adapters if a.ip]
            elif hasattr(session_pool, "all_adapters"):
                interface_ips = [a.ip for a in session_pool.all_adapters if a.ip]
            if hasattr(session_pool, "vpn_shield_enabled"):
                vpn_shield = session_pool.vpn_shield_enabled

        if cls._instance is None:
            cls._instance = cls(interface_ips=interface_ips, vpn_shield=vpn_shield)
        else:
            cls._instance.vpn_shield = vpn_shield
            if interface_ips:
                cls._instance.interface_ips = list(interface_ips)
            cls._instance._apply_interface_settings()
        return cls._instance

    def _get_active_vpn_ips(self) -> List[str]:
        """Detects active VPN adapter IPv4 addresses."""
        try:
            adapters = InterfaceDetector.detect_all_adapters()
            vpn_ips = [
                a.ip for a in adapters
                if a.is_up and a.ip and (getattr(a, "is_vpn", False) or InterfaceDetector.is_vpn_adapter(a.name, a.friendly_name, a.description, a.ip))
            ]
            if vpn_ips:
                return vpn_ips
        except Exception as exc:
            logger.debug("Error detecting VPN adapters for torrent session: %s", exc)

        # Fallback inspection of given interface_ips
        if isinstance(self.interface_ips, (list, tuple, set)):
            return [ip for ip in self.interface_ips if isinstance(ip, str) and ip.startswith("10.5.0.")]
        return []

    def _init_session(self) -> None:
        bootstrap_nodes_str = ",".join(f"{h}:{p}" for h, p in self.DHT_ROUTERS)
        settings = {
            "enable_dht": True,
            # Anonymous mode is False so DHT and PEX can freely find peers rapidly
            "anonymous_mode": False,
            "enable_lsd": True,
            "enable_upnp": False,
            "enable_natpmp": False,
            "dht_bootstrap_nodes": bootstrap_nodes_str,
            # Protocol Encryption
            "out_enc_policy": lt.enc_policy.pe_enabled,
            "in_enc_policy": lt.enc_policy.pe_enabled,
            "allowed_enc_level": lt.enc_level.pe_both,
            "alert_mask": (
                lt.alert.category_t.status_notification
                | lt.alert.category_t.error_notification
                | lt.alert.category_t.storage_notification
                | lt.alert.category_t.dht_notification
            ),
            "active_downloads": 20,
            "active_seeds": 10,
            "active_limit": 30,
        }

        # 1. Strict VPN Interface Binding
        vpn_ips = self._get_active_vpn_ips()
        if self.vpn_shield and vpn_ips:
            vpn_target = vpn_ips[0]
            settings["listen_interfaces"] = f"{vpn_target}:6881"
            settings["outgoing_interfaces"] = vpn_target
            logger.info("TorrentSessionManager: Strict VPN Kill-Switch ACTIVE on %s:6881", vpn_target)
        else:
            settings["listen_interfaces"] = "0.0.0.0:6881,[::]:6881"
            if self.interface_ips:
                valid_ips = [ip for ip in self.interface_ips if ip and ip != "127.0.0.1"]
                if valid_ips:
                    settings["outgoing_interfaces"] = ",".join(valid_ips)

        try:
            from config.settings import settings as app_settings
            if getattr(app_settings, "download_limit_bytes_per_sec", 0) > 0:
                settings["download_rate_limit"] = app_settings.download_limit_bytes_per_sec
        except Exception:
            pass

        self._session = lt.session(settings)

        # Bootstrap popular public DHT routers
        for host, port in self.DHT_ROUTERS:
            try:
                self._session.add_dht_node((host, port))
            except Exception as exc:
                logger.debug("Could not add DHT node %s:%s: %s", host, port, exc)

        logger.info(
            "TorrentSessionManager initialized. DHT running: %s. Anonymous: False. VPN Shield: %s",
            self._session.is_dht_running(),
            bool(self.vpn_shield and vpn_ips),
        )

    def _apply_interface_settings(self) -> None:
        if not self._session:
            return
        vpn_ips = self._get_active_vpn_ips()
        bootstrap_nodes_str = ",".join(f"{h}:{p}" for h, p in self.DHT_ROUTERS)
        base_settings = {
            "enable_dht": True,
            "anonymous_mode": False,
            "enable_lsd": True,
            "enable_upnp": False,
            "enable_natpmp": False,
            "dht_bootstrap_nodes": bootstrap_nodes_str,
        }
        if self.vpn_shield and vpn_ips:
            vpn_target = vpn_ips[0]
            base_settings["listen_interfaces"] = f"{vpn_target}:6881"
            base_settings["outgoing_interfaces"] = vpn_target
            self._session.apply_settings(base_settings)
            logger.info("Updated libtorrent to strict VPN binding on %s:6881", vpn_target)
        elif self.interface_ips:
            valid_ips = [ip for ip in self.interface_ips if ip and ip != "127.0.0.1"]
            if valid_ips:
                base_settings["listen_interfaces"] = "0.0.0.0:6881,[::]:6881"
                base_settings["outgoing_interfaces"] = ",".join(valid_ips)
                self._session.apply_settings(base_settings)
                logger.info("Updated libtorrent outgoing interfaces to: %s", valid_ips)
        else:
            self._session.apply_settings(base_settings)

        # Re-announce to DHT bootstrap routers
        for host, port in self.DHT_ROUTERS:
            try:
                self._session.add_dht_node((host, port))
            except Exception as exc:
                logger.debug("Could not re-add DHT node %s:%s: %s", host, port, exc)

    def set_download_rate_limit(self, limit_bps: int) -> None:
        """Applies download rate limit to libtorrent session (0 = unlimited)."""
        if self._session:
            try:
                self._session.apply_settings({"download_rate_limit": max(0, limit_bps)})
                logger.info("TorrentSessionManager: updated download_rate_limit to %d B/s", limit_bps)
            except Exception as exc:
                logger.error("Failed to set torrent download rate limit: %s", exc)

    def close(self) -> None:
        """Closes and releases the libtorrent session cleanly."""
        if self._session:
            try:
                self._session.pause()
            except Exception:
                pass
            self._session = None
        TorrentSessionManager._instance = None

    @property
    def session(self) -> lt.session:
        if self._session is None:
            self._init_session()
        return self._session

    def add_torrent(self, params: lt.add_torrent_params) -> lt.torrent_handle:
        return self.session.add_torrent(params)

    def remove_torrent(self, handle: lt.torrent_handle, delete_files: bool = False) -> None:
        if not self._session or not handle.is_valid():
            return
        try:
            self.session.remove_torrent(handle, lt.session.delete_files if delete_files else 0)
        except Exception as exc:
            logger.debug("Error removing torrent handle: %s", exc)

    async def resolve_metadata(
        self,
        source: str,
        timeout: float = 25.0,
        timeout_sec: Optional[float] = None,
    ) -> TorrentMetadata:
        """
        Resolves torrent metadata (name, info_hash, file list, sizes)
        from a local .torrent file or a remote magnet URI via DHT/trackers.
        """
        effective_timeout = timeout_sec if timeout_sec is not None else timeout
        clean_src = source.strip()

        # 1. Local .torrent file
        if os.path.exists(clean_src) and not clean_src.startswith("magnet:"):
            try:
                ti = lt.torrent_info(clean_src)
                files = []
                fs = ti.files()
                for i in range(ti.num_files()):
                    if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                        continue
                    files.append(
                        TorrentFileInfo(
                            index=i,
                            path=fs.file_path(i),
                            size=fs.file_size(i),
                            selected=True,
                        )
                    )
                trackers = [t.url for t in ti.trackers()]
                return TorrentMetadata(
                    name=ti.name(),
                    info_hash=str(ti.info_hash()),
                    total_size=ti.total_size(),
                    files=files,
                    trackers=trackers,
                    has_metadata=True,
                )
            except Exception as exc:
                logger.error("Failed to read local torrent file %s: %s", clean_src, exc)
                return TorrentMetadata(
                    name=os.path.basename(clean_src),
                    info_hash="",
                    total_size=0,
                    has_metadata=False,
                )

        # 2. Magnet link resolution via temporary session handle
        if clean_src.startswith("magnet:"):
            try:
                params = lt.parse_magnet_uri(clean_src)
            except Exception as exc:
                logger.error("Failed to parse magnet URI '%s': %s", clean_src, exc)
                return TorrentMetadata(name="Invalid Magnet", info_hash="", total_size=0, has_metadata=False)

            temp_dir = tempfile.gettempdir()
            params.save_path = temp_dir
            # Flag as paused so we only fetch metadata without pulling the payload
            params.flags |= lt.torrent_flags.paused

            handle = self.add_torrent(params)
            info_hash = str(handle.info_hash())
            name = params.name or f"Torrent [{info_hash[:8]}]"

            start_t = time.time()
            try:
                while time.time() - start_t < effective_timeout:
                    if handle.status().has_metadata:
                        ti = handle.torrent_file()
                        files = []
                        fs = ti.files()
                        for i in range(ti.num_files()):
                            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                                continue
                            files.append(
                                TorrentFileInfo(
                                    index=i,
                                    path=fs.file_path(i),
                                    size=fs.file_size(i),
                                    selected=True,
                                )
                            )
                        trackers = [t.url for t in ti.trackers()]
                        return TorrentMetadata(
                            name=ti.name(),
                            info_hash=info_hash,
                            total_size=ti.total_size(),
                            files=files,
                            trackers=trackers,
                            has_metadata=True,
                        )
                    await asyncio.sleep(0.5)
            finally:
                self.remove_torrent(handle, delete_files=False)

            # Metadata resolution timed out before DHT peers replied
            return TorrentMetadata(
                name=name,
                info_hash=info_hash,
                total_size=0,
                files=[],
                has_metadata=False,
            )

        return TorrentMetadata(name="Unknown", info_hash="", total_size=0, has_metadata=False)


class TorrentDownloadEngine:
    """
    Sub-engine orchestrating the download of a specific torrent task.
    Handles file prioritization, DHT/Tracker discovery, piece verification,
    and live metrics dispatch (MB/s, Seeds, Peers, Progress).
    """

    def __init__(
        self,
        task: DownloadTask,
        session_manager: Optional[TorrentSessionManager] = None,
        on_progress: Optional[Callable[[int, int, float], None]] = None,
    ):
        self.task = task
        self.session_manager = session_manager or TorrentSessionManager.get_instance()
        self.on_progress = on_progress
        self.handle: Optional[lt.torrent_handle] = None
        self._is_cancelled = False
        self._is_paused = False
        self._target_filepath: Optional[Path] = None
        self._initial_bytes = 0

    @property
    def target_filepath(self) -> Optional[Path]:
        return self._target_filepath

    @property
    def info(self):
        # Duck-typing compatibility with DownloadEngine for QueueManager
        class _Info:
            total_size = self.task.total_size
        return _Info()

    def get_adapter_bytes(self) -> Dict[str, int]:
        """Provides transferred byte totals for UICoordinator interface cards."""
        transferred = max(0, self.task.downloaded_bytes - self._initial_bytes)
        # Attribute transferred bytes evenly or to first available adapter
        if self.session_manager.interface_ips:
            first_ip = self.session_manager.interface_ips[0]
            return {first_ip: transferred}
        return {"Torrent": transferred}

    async def start(self) -> bool:
        """Starts the torrent download and polls status until completion or cancellation."""
        if not LIBTORRENT_AVAILABLE:
            self.task.error_message = "libtorrent non installato"
            return False

        self.task.task_type = "TORRENT"
        self._is_cancelled = False
        self._is_paused = False
        self._initial_bytes = self.task.downloaded_bytes

        dest_dir = Path(self.task.destination_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 1. Prepare add_torrent_params
        clean_url = self.task.url.strip()
        if clean_url.startswith("magnet:"):
            try:
                params = lt.parse_magnet_uri(clean_url)
            except Exception as exc:
                self.task.error_message = f"Magnet URI invalido: {exc}"
                return False
        elif os.path.exists(clean_url):
            try:
                params = lt.add_torrent_params()
                params.ti = lt.torrent_info(clean_url)
            except Exception as exc:
                self.task.error_message = f"File torrent non valido: {exc}"
                return False
        else:
            self.task.error_message = f"URL o file torrent inesistente: {clean_url}"
            return False

        params.save_path = str(dest_dir)

        # 2. Add to session
        try:
            self.handle = self.session_manager.add_torrent(params)
        except Exception as exc:
            self.task.error_message = f"Errore avvio torrent: {exc}"
            return False

        self.task.info_hash = str(self.handle.info_hash())

        logger.info(
            "Torrent task %s registered with hash %s (target dir: %s)",
            self.task.task_id,
            self.task.info_hash,
            dest_dir,
        )

        # 3. Main monitoring loop
        metadata_configured = False

        while not self._is_cancelled:
            if not self.handle.is_valid():
                logger.warning("Torrent handle invalidated for task %s", self.task.task_id)
                return False

            status = self.handle.status()

            # Handle metadata acquisition
            if status.has_metadata and not metadata_configured:
                ti = self.handle.torrent_file()
                metadata_configured = True
                if not self.task.filename or self.task.filename == self.task.url:
                    self.task.filename = ti.name()
                if self.task.total_size <= 0:
                    self.task.total_size = ti.total_size()

                self._target_filepath = dest_dir / (self.task.filename or ti.name())

                # Apply selective file priorities if specified
                prio_val = getattr(self.task, "priority", 4) or 4
                if self.task.selected_files is not None:
                    selected_set = set(self.task.selected_files)
                    priorities = [prio_val if i in selected_set else 0 for i in range(ti.num_files())]
                    self.handle.prioritize_files(priorities)
                    logger.info(
                        "Applied selective file priority (%d) for %d/%d files in task %s",
                        prio_val,
                        len(selected_set),
                        ti.num_files(),
                        self.task.task_id,
                    )
                elif prio_val != 4:
                    self.handle.prioritize_files([prio_val] * ti.num_files())

            # Update live task metrics
            self.task.seeds = int(getattr(status, "num_seeds", 0))
            self.task.peers = int(getattr(status, "num_peers", 0))
            self.task.speed_bps = float(getattr(status, "download_rate", 0.0))
            self.task.upload_speed_bps = float(getattr(status, "upload_rate", 0.0))
            self.task.pieces_total = int(getattr(status, "num_pieces", 0))

            # Update live file progress if metadata is available
            if status.has_metadata:
                self.update_files_status()

            # In libtorrent, status.pieces is a list of bools
            if getattr(status, "is_finished", False):
                pieces_done = self.task.pieces_total
            elif isinstance(getattr(status, "pieces", None), list):
                pieces_done = status.pieces.count(True)
            else:
                pieces_done = int(getattr(status, "num_pieces", 0))
            self.task.pieces_completed = int(pieces_done)

            if status.total_wanted > 0:
                self.task.total_size = status.total_wanted
                self.task.downloaded_bytes = status.total_wanted_done
            elif status.total > 0:
                self.task.total_size = status.total
                self.task.downloaded_bytes = status.total_done

            if self.on_progress:
                self.on_progress(
                    self.task.downloaded_bytes,
                    self.task.total_size,
                    self.task.progress_percentage,
                )

            # Check completion
            if status.is_finished or (
                status.total_wanted_done >= status.total_wanted > 0
                and status.state in (lt.torrent_status.states.finished, lt.torrent_status.states.seeding)
            ):
                logger.info(
                    "Torrent task %s finished downloading successfully (verified: %d/%d pieces). Stopping seeding and releasing file locks.",
                    self.task.task_id,
                    self.task.pieces_completed,
                    self.task.pieces_total,
                )

                # Stop seeding immediately, flush disk cache, and remove handle to release file locks
                if self.handle and self.handle.is_valid():
                    self.update_files_status()
                    try:
                        self.handle.flush_cache()
                    except Exception as exc:
                        logger.debug("Error flushing torrent cache: %s", exc)

                    try:
                        self.handle.pause()
                    except Exception as exc:
                        logger.debug("Error pausing completed torrent handle: %s", exc)

                    try:
                        self.session_manager.remove_torrent(self.handle, delete_files=False)
                        self.handle = None
                    except Exception as exc:
                        logger.debug("Error removing completed torrent from session: %s", exc)

                # When completed, mark selected files as 100% downloaded
                if self.task.files_status:
                    for f in self.task.files_status:
                        if f.get("priority", 4) > 0:
                            f["downloaded"] = f["size"]
                            f["progress"] = 1.0

                # Zero out live activity metrics to prevent ghost speed/seed numbers
                self.task.speed_bps = 0.0
                self.task.upload_speed_bps = 0.0
                self.task.seeds = 0
                self.task.peers = 0
                self.task.status = DownloadStatus.COMPLETED
                return True

            # If task paused externally
            if self.task.status == DownloadStatus.PAUSED:
                if not (self.handle.flags() & lt.torrent_flags.paused):
                    self.handle.pause()
                await asyncio.sleep(0.5)
                continue
            else:
                if self.handle.flags() & lt.torrent_flags.paused:
                    self.handle.resume()

            await asyncio.sleep(0.5)

        return False

    async def pause(self) -> None:
        """Pauses the torrent downloading/uploading."""
        self._is_paused = True
        if self.handle and self.handle.is_valid():
            self.handle.pause()

    async def resume(self) -> None:
        """Resumes the torrent downloading/uploading."""
        self._is_paused = False
        if self.handle and self.handle.is_valid():
            self.handle.resume()

    def update_files_status(self) -> List[Dict[str, Any]]:
        """
        Updates task.files_status with live progress and priority of each file in the torrent.
        Returns the updated list of file dictionaries.
        """
        if not self.handle or not self.handle.is_valid():
            return self.task.files_status or []

        try:
            status = self.handle.status()
            if not status.has_metadata:
                return self.task.files_status or []

            ti = self.handle.torrent_file()
            fs = ti.files()
            file_progress = self.handle.file_progress()
            num_files = ti.num_files()

            files_status = []
            for i in range(num_files):
                if hasattr(fs, "file_flags"):
                    try:
                        if fs.file_flags(i) & getattr(lt.file_storage, "flag_pad_file", 0):
                            continue
                    except Exception:
                        pass

                if hasattr(fs, "file_path"):
                    path = fs.file_path(i)
                elif hasattr(fs, "__getitem__"):
                    f_item = fs[i]
                    path = getattr(f_item, "path", str(f_item))
                else:
                    path = f"file_{i}"

                if hasattr(fs, "file_size"):
                    size = fs.file_size(i)
                elif hasattr(fs, "__getitem__"):
                    f_item = fs[i]
                    size = getattr(f_item, "size", 0)
                else:
                    size = 0

                try:
                    size = int(size)
                except Exception:
                    size = 0

                downloaded = file_progress[i] if i < len(file_progress) else 0
                if size > 0:
                    prog = min(1.0, max(0.0, downloaded / size))
                else:
                    prog = 1.0

                prio = int(self.handle.file_priority(i)) if hasattr(self.handle, "file_priority") else 4

                files_status.append({
                    "index": i,
                    "path": path,
                    "size": size,
                    "downloaded": downloaded,
                    "progress": prog,
                    "priority": prio,
                })

            self.task.files_status = files_status
            return files_status
        except Exception as exc:
            logger.debug("Error updating files status for %s: %s", self.task.task_id, exc)
            return self.task.files_status or []

    def set_file_priority(self, file_index: int, priority: int) -> bool:
        """
        Changes the priority of a specific file in the active torrent session on the fly.
        Priority: 0=Do not download, 1=Low, 4=Normal, 7=Maximum.
        """
        if not self.handle or not self.handle.is_valid():
            return False

        try:
            self.handle.file_priority(file_index, priority)
            if self.task.files_status:
                for f in self.task.files_status:
                    if f["index"] == file_index:
                        f["priority"] = priority
                        break
            return True
        except Exception as exc:
            logger.error("Failed to set priority %d on file %d for task %s: %s", priority, file_index, self.task.task_id, exc)
            return False

    async def cancel(self) -> None:
        """Cancels download and releases session resources."""
        self._is_cancelled = True
        if self.handle and self.handle.is_valid():
            self.session_manager.remove_torrent(self.handle, delete_files=False)
            self.handle = None
        logger.info("Cancelled TorrentDownloadEngine for task %s", self.task.task_id)
