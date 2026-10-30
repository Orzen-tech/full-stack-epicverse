"""TEST-ONLY, in-memory, single-process nonce store for the offline encrypted-credential PoC.

A nonce is issued for ONE operation, is valid for a short time and can be consumed exactly once. Only a SHA-256 of the nonce is
kept. A real gateway must use a shared database table (unique table name, atomic consume) instead of this class: it is not
shared between processes and is lost on restart.
"""
import hashlib
import secrets
import threading
import time

from envelope import (
    EnvelopeError, EXPIRED, REPLAY, UNKNOWN_NONCE, WRONG_OPERATION, CAPACITY, OPERATIONS, b64e,
)

NONCE_BYTES = 32


class NonceStore:
    def __init__(self, ttl_seconds=120, max_outstanding=10_000, clock=time.time):
        self.ttl = int(ttl_seconds)
        self.max_outstanding = int(max_outstanding)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries = {}  # sha256(nonce) -> [operation, expires_at, used]

    def now(self):
        return int(self._clock())

    @staticmethod
    def _key(nonce):
        return hashlib.sha256(nonce.encode("ascii")).digest()

    def _purge(self, now):
        # Used or expired entries are kept for one extra TTL so a late replay is still reported as a replay or expiry.
        stale = [k for k, (_op, exp, _used) in self._entries.items() if exp + self.ttl < now]
        for k in stale:
            del self._entries[k]

    def issue(self, operation):
        if operation not in OPERATIONS:
            raise EnvelopeError(WRONG_OPERATION)
        now = self.now()
        with self._lock:
            self._purge(now)
            if len(self._entries) >= self.max_outstanding:
                raise EnvelopeError(CAPACITY)
            nonce = b64e(secrets.token_bytes(NONCE_BYTES))
            self._entries[self._key(nonce)] = [operation, now + self.ttl, False]
        return nonce

    def _check(self, entry, operation, now):
        if entry is None:
            raise EnvelopeError(UNKNOWN_NONCE)
        if entry[0] != operation:
            raise EnvelopeError(WRONG_OPERATION)
        if entry[2]:
            raise EnvelopeError(REPLAY)
        if now > entry[1]:
            raise EnvelopeError(EXPIRED)

    def inspect(self, nonce, operation):
        """Read-only check (does NOT consume). Raises EnvelopeError unless the nonce is usable for this operation."""
        now = self.now()
        with self._lock:
            self._check(self._entries.get(self._key(nonce)), operation, now)

    def consume(self, nonce, operation):
        """Atomically marks the nonce used. Exactly one caller can succeed; every other caller gets REPLAY."""
        now = self.now()
        with self._lock:
            entry = self._entries.get(self._key(nonce))
            self._check(entry, operation, now)
            entry[2] = True
