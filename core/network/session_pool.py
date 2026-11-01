import asyncio
import itertools
import logging
import socket
from typing import Dict, List, Optional, Tuple
import aiohttp

from .models import NetworkAdapter
from .interface_detector import InterfaceDetector
from .socket_binder import SocketBinder

logger = logging.getLogger(__name__)


DEFAULT_SESSION_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
}


class MultiInterfaceSessionPool:
    """
    Manages a pool of aiohttp.ClientSession instances, each bound to a specific
    physical/virtual network adapter using IP binding and OS-level interface steering.
    """

    def __init__(
        self,
        adapters: Optional[List[NetworkAdapter]] = None,
        max_connections_per_host: int = 8,
        total_limit: int = 32,
        socket_timeout: float = 30.0,
        vpn_shield_enabled: bool = True,
    ):
        self._target_adapters = adapters
        self._max_connections_per_host = max_connections_per_host
        self._total_limit = total_limit
        self._timeout = aiohttp.ClientTimeout(total=socket_timeout, sock_connect=10.0)

        self._adapters: List[NetworkAdapter] = list(adapters) if adapters else []
        self._disabled_adapters: set[str] = set()
        self._sessions: Dict[str, aiohttp.ClientSession] = {}
        self._session_to_adapter: Dict[int, NetworkAdapter] = {}
        self._round_robin_iter: Optional[itertools.cycle] = None
        self._lock = asyncio.Lock()
        self._is_initialized = False
        self.vpn_shield_enabled: bool = vpn_shield_enabled

    @property
    def has_vpn(self) -> bool:
        """Returns True if at least one active VPN interface is detected."""
        return any(getattr(a, "is_vpn", False) for a in self._adapters)

    @property
    def all_adapters(self) -> List[NetworkAdapter]:
        """Returns all detected network adapters in the pool, enabled or disabled."""
        return list(self._adapters)

    @property
    def active_adapters(self) -> List[NetworkAdapter]:
        """
        Returns the list of active AND enabled network adapters in the pool.
        When VPN Privacy Shield (Kill-Switch) is active and a VPN adapter is present,
        traffic is strictly restricted to VPN adapters to prevent IP leakage.
        """
        enabled = [a for a in self._adapters if a.name not in self._disabled_adapters]
        if self.vpn_shield_enabled and self.has_vpn:
            vpn_adapters = [a for a in enabled if getattr(a, "is_vpn", False)]
            if vpn_adapters:
                return vpn_adapters
            # Kill-Switch: VPN adapter went down, block traffic to prevent leak!
            logger.warning("VPN Privacy Shield (Kill-Switch) active: VPN interface is DOWN. Blocking traffic!")
            return []
        return enabled

    @property
    def disabled_adapter_names(self) -> set[str]:
        """Returns the set of disabled adapter names."""
        return set(self._disabled_adapters)

    def disable_adapter(self, adapter_name: str) -> bool:
        """Temporarily disables an adapter from receiving new tasks/chunks."""
        active = [a for a in self._adapters if a.name not in self._disabled_adapters]
        # Ensure at least one adapter remains active
        if len(active) <= 1 and any(a.name == adapter_name for a in active):
            logger.warning("Cannot disable adapter '%s': at least one adapter must remain active", adapter_name)
            return False
        self._disabled_adapters.add(adapter_name)
        self._round_robin_iter = None
        logger.info("Disabled adapter '%s' in session pool", adapter_name)
        return True

    def enable_adapter(self, adapter_name: str) -> bool:
        """Re-enables a previously disabled adapter."""
        if adapter_name in self._disabled_adapters:
            self._disabled_adapters.remove(adapter_name)
            self._round_robin_iter = None
            logger.info("Enabled adapter '%s' in session pool", adapter_name)
            return True
        return False

    def is_adapter_enabled(self, adapter_name: str) -> bool:
        """Returns True if the adapter is currently enabled."""
        return adapter_name not in self._disabled_adapters

    @property
    def is_initialized(self) -> bool:
        return self._is_initialized

    def create_session(self, adapter: Optional[NetworkAdapter] = None) -> aiohttp.ClientSession:
        """
        Creates an aiohttp.ClientSession strictly configured with family=socket.AF_INET
        to force IPv4 resolution and prevent unroutable IPv6 queries on dual-stack CDN hosts (e.g. Cloudflare / testfile.org).
        """
        if adapter and adapter.ip and adapter.name != "default":
            socket_factory = SocketBinder.create_socket_factory(adapter)
            connector = aiohttp.TCPConnector(
                family=socket.AF_INET,
                local_addr=(adapter.ip, 0),
                socket_factory=socket_factory,
                force_close=False,
                keepalive_timeout=60.0,
                limit_per_host=self._max_connections_per_host,
                limit=self._total_limit,
            )
        else:
            connector = aiohttp.TCPConnector(
                family=socket.AF_INET,
                force_close=False,
                keepalive_timeout=60.0,
                limit_per_host=self._max_connections_per_host,
                limit=self._total_limit,
            )

        return aiohttp.ClientSession(
            connector=connector,
            timeout=self._timeout,
            headers=DEFAULT_SESSION_HEADERS,
        )

    async def initialize(self) -> None:
        """Discovers adapters and initializes ClientSession instances."""
        async with self._lock:
            if self._is_initialized:
                return

            if self._target_adapters:
                self._adapters = [a for a in self._target_adapters if a.is_up and a.ip]
            else:
                self._adapters = InterfaceDetector.get_active_internet_adapters()

            # If no adapters found, fallback to default (let OS decide)
            if not self._adapters:
                logger.warning(
                    "No dedicated active internet adapters detected. Creating default un-bound session."
                )
                default_adapter = NetworkAdapter(
                    name="default",
                    friendly_name="Default System Interface",
                    ip="0.0.0.0",
                )
                session = self.create_session(default_adapter)
                self._adapters = [default_adapter]
                self._sessions[default_adapter.name] = session
                self._session_to_adapter[id(session)] = default_adapter
            else:
                logger.info("Initializing multi-interface pool with %d adapters:", len(self._adapters))
                for ad in self._adapters:
                    logger.info("  * Binding adapter '%s' [%s] (IfIndex=%s)", ad.name, ad.ip, ad.if_index)
                    session = self.create_session(ad)
                    self._sessions[ad.name] = session
                    self._session_to_adapter[id(session)] = ad

            self._round_robin_iter = itertools.cycle(self._adapters)
            self._is_initialized = True

    def get_session_by_name(self, name: str) -> Optional[aiohttp.ClientSession]:
        """Returns the session bound to the specified adapter name."""
        return self._sessions.get(name)

    def get_adapter_for_session(self, session: aiohttp.ClientSession) -> Optional[NetworkAdapter]:
        """Returns the adapter associated with a given ClientSession."""
        return self._session_to_adapter.get(id(session))

    def get_primary_adapter_and_session(self) -> Tuple[NetworkAdapter, aiohttp.ClientSession]:
        """
        Returns the primary (default internet route with lowest metric) adapter
        and its associated ClientSession. Fallbacks to any active adapter if primary is disabled.
        """
        if not self._is_initialized or not self._adapters:
            raise RuntimeError("MultiInterfaceSessionPool is not initialized. Call await initialize() first.")

        active = self.active_adapters
        if not active:
            active = self._adapters  # Fallback if all were disabled

        primary = next((a for a in active if a.is_primary), active[0])
        session = self._sessions[primary.name]
        return primary, session

    def get_next_adapter_and_session(self) -> Tuple[NetworkAdapter, aiohttp.ClientSession]:
        """
        Returns the next (NetworkAdapter, ClientSession) pair in round-robin order.
        Thread-safe within asyncio event loop.
        """
        if not self._is_initialized:
            raise RuntimeError("MultiInterfaceSessionPool is not initialized. Call await initialize() first.")

        active = self.active_adapters
        if not active:
            active = self._adapters

        if self._round_robin_iter is None:
            self._round_robin_iter = itertools.cycle(active)

        adapter = next(self._round_robin_iter)
        session = self._sessions[adapter.name]
        return adapter, session

    async def close(self) -> None:
        """Closes all underlying ClientSession instances."""
        async with self._lock:
            for name, session in self._sessions.items():
                if not session.closed:
                    await session.close()
            self._sessions.clear()
            self._session_to_adapter.clear()
            self._adapters.clear()
            self._round_robin_iter = None
            self._is_initialized = False
            logger.info("Closed all sessions in MultiInterfaceSessionPool.")

    async def __aenter__(self) -> "MultiInterfaceSessionPool":
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()
