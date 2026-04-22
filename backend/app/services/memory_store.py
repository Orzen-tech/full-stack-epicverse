import json
import asyncio
from typing import Any, Optional
from redis.asyncio import Redis
from app.core.config import settings

class SessionStore:
    def __init__(self):
        self.redis: Optional[Redis] = None
        self.prefix = "epicverse:session:"
        self._redis_enabled = True # Circuit Breaker

    async def connect(self):
        """Lazy-Connect with 500ms Circuit Breaker."""
        if not self._redis_enabled:
            return None
            
        if self.redis is None:
            try:
                # Use a float for sub-second timeout
                timeout = float(settings.REDIS_SOCKET_TIMEOUT_SECONDS)
                self.redis = Redis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_timeout=timeout,
                    socket_connect_timeout=timeout
                )
                # Heartbeat check
                await asyncio.wait_for(self.redis.ping(), timeout=timeout)
                print(f"MemoryStore: Redis CONNECTED on {settings.REDIS_URL}")
            except Exception as e:
                if self._redis_enabled:
                    print(f"\n[INFRA] Redis Offline: Degraded Mode Active (Stateless Session).")
                    print(f"       Persistence layer disabled due to: {e}\n")
                self._redis_enabled = False
                self.redis = None
        return self.redis

    async def set_session_data(self, user_uid: str, data: dict[str, Any], ttl: int = 3600):
        """Persist session-specific metadata (last_numbers, mode, etc.) with safety."""
        client = await self.connect()
        if not client or not self._redis_enabled:
            return

        try:
            key = f"{self.prefix}{user_uid}"
            await client.set(key, json.dumps(data), ex=ttl)
        except Exception:
            self._redis_enabled = False # Tripped after one failure

    async def get_session_data(self, user_uid: str) -> dict[str, Any]:
        """Retrieve last known session state for a user with safety."""
        client = await self.connect()
        if not client or not self._redis_enabled:
            return {}

        try:
            key = f"{self.prefix}{user_uid}"
            cached = await client.get(key)
            return json.loads(cached) if cached else {}
        except Exception:
            self._redis_enabled = False
            return {}

    async def update_last_numbers(self, user_uid: str, numbers: list[str]):
        """Specialized helper to update just the card numbers."""
        data = await self.get_session_data(user_uid)
        data["last_numbers"] = numbers
        await self.set_session_data(user_uid, data)

    async def check_otp_rate_limit(self, identifier: str) -> dict[str, Any]:
        """
        Implements Tiered Cooldown:
        - 3 tries allowed.
        - Then 15 min lock.
        - Then 3 more tries.
        - Finally 1 hour lock.
        """
        client = await self.connect()
        if not client or not self._redis_enabled:
            return {"allowed": True}

        try:
            k_count = f"otp_limit:count:{identifier}"
            k_block = f"otp_limit:block:{identifier}"
            k_strike = f"otp_limit:strike:{identifier}"

            # 1. Check if blocked
            ttl = await client.ttl(k_block)
            if ttl > 0:
                return {"allowed": False, "mins": (ttl // 60) + 1}

            # 2. Increment attempts
            count = await client.incr(k_count)
            if count == 1: await client.expire(k_count, 1800) # Half-hour window

            # 3. Check Threshold
            if count > 3:
                strike = await client.get(k_strike)
                strike = int(strike) if strike else 0
                
                if strike == 0:
                    # First persistent failure -> 15 mins
                    await client.set(k_block, "1", ex=900)
                    await client.set(k_strike, "1", ex=86400)
                    await client.delete(k_count)
                    return {"allowed": False, "mins": 15}
                else:
                    # Second persistent failure -> 1 hour
                    await client.set(k_block, "1", ex=3600)
                    await client.delete(k_count)
                    await client.delete(k_strike)
                    return {"allowed": False, "mins": 60}

            return {"allowed": True}
        except Exception:
            return {"allowed": True}

session_store = SessionStore()
