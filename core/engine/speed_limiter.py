import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SpeedLimiter:
    """
    Global asynchronous token bucket rate limiter to throttle maximum
    aggregate download throughput across all HTTP workers and connections.
    Thread-safe and coroutine-friendly.
    """

    _instance: Optional["SpeedLimiter"] = None

    def __init__(self, limit_bps: int = 0):
        """
        :param limit_bps: Maximum bytes per second allowed. 0 means unlimited.
        """
        self.limit_bps = max(0, limit_bps)
        self._tokens: float = float(self.limit_bps) if self.limit_bps > 0 else 0.0
        self._last_time: float = time.monotonic()
        self._lock = asyncio.Lock()

    @classmethod
    def get_instance(cls, limit_bps: int = 0) -> "SpeedLimiter":
        if cls._instance is None:
            cls._instance = cls(limit_bps=limit_bps)
        return cls._instance

    @classmethod
    def reset_instance(cls, limit_bps: int = 0) -> "SpeedLimiter":
        cls._instance = cls(limit_bps=limit_bps)
        return cls._instance

    def set_limit(self, limit_bps: int) -> None:
        """Sets the new throughput limit in bytes/sec (0 = unlimited)."""
        self.limit_bps = max(0, limit_bps)
        self._tokens = float(self.limit_bps)
        self._last_time = time.monotonic()
        logger.info("SpeedLimiter: configured limit to %d B/s (%s)", self.limit_bps, "unlimited" if self.limit_bps == 0 else f"{self.limit_bps / (1024*1024):.2f} MB/s")

    def get_limit(self) -> int:
        return self.limit_bps

    async def acquire(self, num_bytes: int) -> None:
        """
        Consumes tokens corresponding to num_bytes.
        If insufficient tokens exist, pauses execution proportionately
        to adhere to the configured rate limit.
        """
        if self.limit_bps <= 0 or num_bytes <= 0:
            return

        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = max(0.0, now - self._last_time)
                self._last_time = now

                # Refill tokens up to maximum 1.5 second burst buffer
                max_capacity = float(self.limit_bps) * 1.5
                self._tokens = min(max_capacity, self._tokens + elapsed * self.limit_bps)

                if self._tokens >= num_bytes:
                    self._tokens -= num_bytes
                    return

                # Calculate duration needed to accumulate missing tokens
                needed = num_bytes - self._tokens
                wait_time = needed / self.limit_bps

            # Sleep outside the lock so other coroutines are not blocked
            await asyncio.sleep(min(wait_time, 0.5))
