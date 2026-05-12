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

session_store = SessionStore()
