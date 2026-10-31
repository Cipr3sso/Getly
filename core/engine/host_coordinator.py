import asyncio
import logging
import time
import urllib.parse
from contextlib import asynccontextmanager
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Sensitive domains with strict concurrency limits
SENSITIVE_HOST_LIMITS = {
    "proof.ovh.net": 2,
    "ovh.net": 2,
}


class HostCoordinator:
    """
    Coordinates per-host concurrency, rate limiting, and HTTP 429 circuit breaking
    across all download workers and network adapters targeting the same domain.
    """

    _instance: Optional["HostCoordinator"] = None

    def __init__(self, default_concurrency: int = 8):
        self.default_concurrency = default_concurrency
        self._blocked_until: Dict[str, float] = {}
        self._semaphores: Dict[str, asyncio.Semaphore] = {}
        self._active_connections: Dict[str, int] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls) -> "HostCoordinator":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def extract_host(url: str) -> str:
        """Extracts normalized hostname without port from URL."""
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if ":" in netloc:
            netloc = netloc.split(":")[0]
        return netloc or "default"

    def get_concurrency_limit(self, host: str) -> int:
        """Returns max concurrent connections allowed for this host."""
        host_clean = host.lower()
        for sensitive, limit in SENSITIVE_HOST_LIMITS.items():
            if host_clean == sensitive or host_clean.endswith("." + sensitive):
                return limit
        return self.default_concurrency

    def get_semaphore(self, host: str) -> asyncio.Semaphore:
        """Gets or creates the concurrency semaphore for the given host."""
        if host not in self._semaphores:
            limit = self.get_concurrency_limit(host)
            self._semaphores[host] = asyncio.Semaphore(limit)
            logger.info("Configured host '%s' concurrency semaphore: max %d active connections", host, limit)
        return self._semaphores[host]

    def is_blocked(self, host: str) -> bool:
        """Returns True if the host is currently in a 429 circuit-breaker cooldown."""
        return time.time() < self._blocked_until.get(host, 0.0)

    def get_blocked_until(self, host: str) -> float:
        return self._blocked_until.get(host, 0.0)

    def trip_circuit_breaker(self, host: str, delay: float) -> float:
        """
        Trips the circuit breaker for all workers targeting this host.
        Returns the timestamp until which the host is blocked.
        """
        now = time.time()
        current_block = self._blocked_until.get(host, 0.0)
        new_block = max(current_block, now + delay)
        self._blocked_until[host] = new_block
        logger.warning(
            "CIRCUIT BREAKER: Host '%s' rate limited (HTTP 429). "
            "Suspending ALL requests to this host for %.2fs (until %.2f)",
            host,
            delay,
            new_block,
        )
        return new_block

    async def wait_if_blocked(self, host: str) -> None:
        """
        Suspends the calling coroutine as long as the host is blocked by the circuit breaker.
        Eliminates busy-waiting: computes remaining cooldown and performs a single sleep
        with a 0.05s safety margin.
        """
        while True:
            blocked_until = self._blocked_until.get(host, 0.0)
            now = time.time()
            if blocked_until > now:
                remaining = (blocked_until - now) + 0.05
                logger.info(
                    "Host '%s' is blocked by circuit breaker (%.2fs remaining). Worker waiting...",
                    host,
                    blocked_until - now,
                )
                await asyncio.sleep(remaining)
            else:
                break

    @asynccontextmanager
    async def connection_slot(self, host: str):
        """
        Context manager to acquire a concurrency slot for the given host,
        enforcing both rate-limit backoff pause and max concurrent connections.
        """
        # 1. Wait before competing for semaphore if host is blocked
        await self.wait_if_blocked(host)

        sem = self.get_semaphore(host)
        async with sem:
            # 2. Check again once acquired in case another worker tripped 429
            await self.wait_if_blocked(host)
            self._active_connections[host] = self._active_connections.get(host, 0) + 1
            try:
                yield
            finally:
                self._active_connections[host] = max(0, self._active_connections.get(host, 1) - 1)

    def get_active_connection_count(self, host: str) -> int:
        return self._active_connections.get(host, 0)

    def reset(self, host: Optional[str] = None) -> None:
        """Resets blocked states and semaphores (primarily for testing)."""
        if host:
            self._blocked_until.pop(host, None)
            self._semaphores.pop(host, None)
            self._active_connections.pop(host, None)
        else:
            self._blocked_until.clear()
            self._semaphores.clear()
            self._active_connections.clear()
