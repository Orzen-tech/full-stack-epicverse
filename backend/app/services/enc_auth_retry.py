"""Bounded retry for IDEMPOTENT identity reads and verifications only.

Rules (all enforced here, none left to the caller):
  * at most `max_retries` retries (default policy: 2);
  * the whole retry effort stays inside `budget_ms` counted from the start of the FIRST attempt: a retry is not started if its delay
    would push the elapsed time past the budget (so a slow, hanging attempt is never retried);
  * only failures the caller classifies as transient are retried; anything else (bad credentials, permission errors, other 4xx) is
    raised at once;
  * delays are short and jittered (100 ms, then 250 ms, each x0.8 to x1.2).
It is never applied to account creation, password updates, revocation, proof consumption or token minting.
Pure module (no application imports): the clock, the sleep and the random source are injectable for tests.
"""
import asyncio
import random
import time

BACKOFF_SECONDS = (0.1, 0.25)


async def retrying(call, *, stage, is_transient, max_retries, budget_ms, note=None, sleep=asyncio.sleep, clock=time.monotonic,
                   rand=random.random):
    started = clock()
    attempt = 0
    while True:
        try:
            result = await call()
        except Exception as exc:
            if attempt >= max_retries or not is_transient(exc):
                raise
            delay = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)] * (0.8 + 0.4 * rand())
            if (clock() - started + delay) * 1000.0 > budget_ms:
                raise
            if note:
                note("retry", stage, getattr(exc, "cause", "-"))
            attempt += 1
            await sleep(delay)
            continue
        if attempt and note:
            note("retry_success", stage, "-")
        return result
