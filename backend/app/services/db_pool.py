import os
import asyncio
import asyncpg
from dotenv import load_dotenv

load_dotenv()

_raw_url = os.getenv("DATABASE_URL", "")
DATABASE_URL = _raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)

_pool: asyncpg.Pool | None = None
_pool_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    return _pool_lock


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _get_lock():
        if _pool is None:
            # Sized for Cloud SQL db-custom-1-3840 (Postgres ~100 conn cap).
            # Fleet math: max_size (8) × Cloud Run max-instances (10) = 80
            # concurrent DB conns at peak, leaving ~20 for admin/proxies/monitoring.
            # command_timeout=15s fails slow queries fast so they don't block the
            # pool; max_inactive_connection_lifetime recycles stale conns that
            # Cloud SQL may have silently killed.
            _pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=8,
                command_timeout=15,
                max_inactive_connection_lifetime=300,
            )
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
