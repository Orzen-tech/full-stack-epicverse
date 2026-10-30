"""In-container runner for the G6b lab: the built (hash-gated) backend plus TEST-ONLY helpers. Mounted into the lab container; never part
of the patch or of any deployment.

  GET  /__g6b/outbox                    captured e-mails (nothing is sent)
  POST /__g6b/issue_proof {"email"}     issues a real e-mail ownership proof through the production code path
  POST /__g6b/age_proof {"proof", "minutes"}   moves a proof's created_at into the past (to test the gateway-side maximum age)
  POST /__g6b/faults {...}              harness-side identity/store faults (see FAULT NAMES)
  POST /__g6b/resolve_email             the D6 helper on its own
  GET  /__g6b/diag                      the gateway's own counters/timings (enc_auth_diag) + harness call counters
  POST /__g6b/diag/reset
  POST /__g6b/hold_pool {"seconds": S}  takes every pooled database connection for S seconds (pool saturation)
FAULT NAMES (boolean unless noted): create_user, update_password (definite failure), update_then_fail (the update IS applied, then an
unknown outcome is reported), revoke_refresh_tokens, mint_custom_token, bind_claim (store unavailable),
uid_confusion {"uid": <other uid>, "email": <e-mail it should claim>} (the identity service answers with a different UID for these
credentials, and that UID's account claims the same e-mail), slow_create_user_s (number: sleep before the call).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.environ.get("PYTHONPATH", "/src/backend").split(":")[0])

import uvicorn  # noqa: E402
from fastapi import Depends, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import app.services.email_service as email_service  # noqa: E402
import app.services.enc_auth_diag as diag  # noqa: E402
import app.services.enc_auth_identity as ident  # noqa: E402
import app.services.enc_auth_store as store  # noqa: E402
from app.api.dependencies import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.services.db_pool import get_pool  # noqa: E402
from app.services.enc_auth_identity import resolve_token_email  # noqa: E402

OUTBOX = []
FAULTS = {}
CALLS = {"mint": 0, "create_user": 0, "update_password": 0, "revoke": 0, "verify_password": 0, "bind_claim": 0}
_held = []


async def capture_otp_email(email, otp, valid_minutes=1):
    OUTBOX.append({"to": email, "otp": otp})
    return True


email_service.send_otp_email = capture_otp_email


def _wrap(name, fault, counter, raises):
    original = getattr(ident, name)

    async def wrapper(*args, **kwargs):
        CALLS[counter] += 1
        if FAULTS.get(fault):
            raise raises()
        return await original(*args, **kwargs)

    setattr(ident, name, wrapper)


_wrap("create_user", "create_user", "create_user", lambda: ident.IdentityUnavailable("create_user", "sdk_error"))
_wrap("revoke_refresh_tokens", "revoke_refresh_tokens", "revoke", lambda: ident.IdentityUnavailable("revoke", "sdk_error"))
_wrap("mint_custom_token", "mint_custom_token", "mint", lambda: ident.IdentityUnavailable("mint", "sdk_error"))

_original_create = ident.create_user


async def create_user(email, password):
    if FAULTS.get("slow_create_user_s"):
        await asyncio.sleep(float(FAULTS["slow_create_user_s"]))
    return await _original_create(email, password)


ident.create_user = create_user

_original_update = ident.update_password


async def update_password(uid, new_password):
    CALLS["update_password"] += 1
    if FAULTS.get("update_password"):
        raise ident.IdentityUnavailable("update_password", "sdk_error")
    if FAULTS.get("update_then_fail"):
        await _original_update(uid, new_password)          # the password IS changed, but the caller is told the outcome is unknown
        raise ident.IdentityUnavailable("update_password", "sdk_error")
    return await _original_update(uid, new_password)


ident.update_password = update_password

_original_verify = ident.verify_password


async def verify_password(email, password):
    CALLS["verify_password"] += 1
    uid, code = await _original_verify(email, password)
    confusion = FAULTS.get("uid_confusion")
    if confusion and uid:
        return confusion["uid"], None
    return uid, code


ident.verify_password = verify_password

_original_get_account = ident.get_account


async def get_account(uid):
    confusion = FAULTS.get("uid_confusion")
    if confusion and uid == confusion["uid"]:
        return ident.Account(uid, confusion["email"], False)
    return await _original_get_account(uid)


ident.get_account = get_account

_original_bind = store.bind_claim


async def bind_claim(raw_proof, uid):
    CALLS["bind_claim"] += 1
    if FAULTS.get("bind_claim"):
        raise store.StoreUnavailable("db_unavailable")
    return await _original_bind(raw_proof, uid)


store.bind_claim = bind_claim


@app.get("/__g6b/outbox")
async def outbox():
    return JSONResponse(OUTBOX)


@app.post("/__g6b/faults")
async def set_faults(request: Request):
    FAULTS.clear()
    FAULTS.update((await request.json()))
    return JSONResponse({"ok": True})


@app.get("/__g6b/diag")
async def get_diag():
    return JSONResponse({"gateway": diag.snapshot(), "calls": CALLS})


@app.post("/__g6b/diag/reset")
async def reset_diag():
    diag.reset()
    for key in CALLS:
        CALLS[key] = 0
    return JSONResponse({"ok": True})


@app.post("/__g6b/resolve_email")
async def resolve_email(current_user: dict = Depends(get_current_user)):
    try:
        return JSONResponse({"email": await resolve_token_email(current_user)})
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.post("/__g6b/issue_proof")
async def issue_proof(request: Request):
    from app.services.user_db import issue_email_verification_proof          # HEAD's production proof issuer (what /auth/verify-otp calls)
    email = (await request.json())["email"]
    proof = await issue_email_verification_proof(email.lower())
    return JSONResponse({"result": "success" if proof else "failed", "proof": proof})


@app.post("/__g6b/age_proof")
async def age_proof(request: Request):
    from app.services import user_db as udb
    from app.core.config import settings as _settings
    data = await request.json()
    pool = await get_pool()
    async with pool.acquire() as conn:
        status = await conn.execute("UPDATE email_verification_proofs_v2 SET created_at = created_at - make_interval(mins => $2) WHERE proof_hash = $1",
                                    udb._proof_v2_hash(_settings.MFA_OTP_HASH_SECRET, data["proof"]), int(data["minutes"]))
    return JSONResponse({"updated": status})


@app.post("/__g6b/bind_race")
async def bind_race(request: Request):
    """Calls the REAL claim logic concurrently for several UIDs and one proof: exactly one UID may end up owning it."""
    data = await request.json()
    results = await asyncio.gather(*[_original_bind(data["proof"], uid) for uid in data["uids"] for _ in range(data.get("repeat", 1))])
    owner = await store.claim_owner(data["proof"])
    return JSONResponse({"results": results, "uids": [uid for uid in data["uids"] for _ in range(data.get("repeat", 1))], "owner": owner})


@app.post("/__g6b/hold_pool")
async def hold_pool(request: Request):
    seconds = float((await request.json())["seconds"])
    pool = await get_pool()
    for _ in range(pool.get_max_size()):
        _held.append(await pool.acquire())

    async def release():
        await asyncio.sleep(seconds)
        while _held:
            await pool.release(_held.pop())

    asyncio.get_running_loop().create_task(release())
    return JSONResponse({"held": len(_held)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["G6B_PORT"]), log_level="warning")
