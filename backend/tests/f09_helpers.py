"""Small shared helpers for the F-09 tests (database access + request shortcuts)."""
from app.services.db_pool import get_pool

API = "/api/v1"


async def fetchrow(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(query, *args)


async def fetch(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(query, *args)


async def fetchval(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(query, *args)


async def execute(query: str, *args):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def is_verified(uid: str) -> bool:
    return bool(await fetchval("SELECT email_verified FROM users WHERE uid = $1", uid))


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def signup_proof(client, mailbox, email: str) -> str:
    """send-email-otp -> verify-otp for `email`; returns the raw proof."""
    sent = await client.post(f"{API}/auth/send-email-otp", data={"identifier": email})
    assert sent.status_code == 200, sent.text
    verified = await client.post(
        f"{API}/auth/verify-otp", data={"identifier": email, "otp": mailbox.last_otp(email)})
    assert verified.status_code == 200, verified.text
    proof = verified.json().get("email_verification_proof")
    assert proof, "verify-otp did not return a proof"
    return proof


async def mark_verified(client, token: str, proof: str | None = None, **extra_fields):
    data = dict(extra_fields)
    if proof is not None:
        data["proof"] = proof
    return await client.post(f"{API}/auth/mark-verified", headers=bearer(token), data=data or None)


async def expire_proofs():
    await execute("UPDATE email_verification_proofs_v2 SET "
                  "created_at = NOW() - INTERVAL '1 hour', expires_at = NOW() - INTERVAL '45 minutes'")
