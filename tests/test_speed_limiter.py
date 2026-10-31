import asyncio
import time
import pytest
from core.engine.speed_limiter import SpeedLimiter
from config.settings import SPEED_LIMIT_1_MB, SPEED_LIMIT_UNLIMITED


@pytest.mark.asyncio
async def test_speed_limiter_singleton_and_configuration():
    limiter = SpeedLimiter.reset_instance(limit_bps=1000)
    assert limiter.get_limit() == 1000

    same_limiter = SpeedLimiter.get_instance()
    assert same_limiter is limiter

    limiter.set_limit(5000)
    assert limiter.get_limit() == 5000

    # Negative limit clamped to 0
    limiter.set_limit(-100)
    assert limiter.get_limit() == 0


@pytest.mark.asyncio
async def test_speed_limiter_unlimited():
    limiter = SpeedLimiter(limit_bps=0)
    t0 = time.monotonic()
    # Acquiring even large bytes when unlimited should be instant
    await limiter.acquire(10_000_000)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1


@pytest.mark.asyncio
async def test_speed_limiter_rate_limiting():
    # 50,000 bytes/sec limit
    limit_bps = 50_000
    limiter = SpeedLimiter(limit_bps=limit_bps)
    # Drain initial burst tokens
    await limiter.acquire(limit_bps)

    # Next acquire of 25,000 bytes should take approx 0.5s (allow margin for CI)
    t0 = time.monotonic()
    await limiter.acquire(25_000)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.25, f"Expected at least ~0.25-0.5s throttle, took {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_speed_limiter_concurrent_acquire():
    limit_bps = 100_000
    limiter = SpeedLimiter(limit_bps=limit_bps)
    # Drain tokens
    await limiter.acquire(limit_bps)

    async def worker(n_bytes: int):
        await limiter.acquire(n_bytes)

    t0 = time.monotonic()
    # 4 workers each asking for 25,000 bytes = 100,000 bytes total -> ~1.0s
    await asyncio.gather(
        worker(25_000),
        worker(25_000),
        worker(25_000),
        worker(25_000),
    )
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.5, f"Concurrent workers completed too fast: {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_coordinator_speed_limiter_integration(tmp_path):
    from ui.coordinator import UICoordinator
    from core.scheduler.queue_manager import QueueManager
    from core.scheduler.state_store import StateStore
    from core.network.session_pool import MultiInterfaceSessionPool

    pool = MultiInterfaceSessionPool(adapters=[])
    store = StateStore(tmp_path / "test.db")
    qm = QueueManager(session_pool=pool, state_store=store, default_download_dir=tmp_path)
    coord = UICoordinator(queue_manager=qm, session_pool=pool)

    coord.set_download_limit(SPEED_LIMIT_1_MB)
    assert coord.get_download_limit() == SPEED_LIMIT_1_MB
    assert SpeedLimiter.get_instance().get_limit() == SPEED_LIMIT_1_MB

    coord.set_download_limit(SPEED_LIMIT_UNLIMITED)
    assert coord.get_download_limit() == 0
    assert SpeedLimiter.get_instance().get_limit() == 0
