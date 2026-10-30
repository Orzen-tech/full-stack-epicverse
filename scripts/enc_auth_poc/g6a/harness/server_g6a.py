"""In-container runner for the G6a lab: the built backend plus TEST-ONLY helpers. It is mounted into the lab container and is never
part of the patch or of any deployment.

  * e-mails are captured (nothing is sent): GET /__g6a/outbox
  * POST /__g6a/issue_proof {"email": ...}      issues a real e-mail ownership proof through the production code path
                                                (save_otp + verify_otp_and_issue_proof), bypassing only the OTP send limiter
  * POST /__g6a/faults {"update_password": true, ...}   makes selected identity calls fail, to test partial failures
Faults: create_user, update_password (definite failure), update_then_fail (the update IS applied, then the call reports an unknown
outcome), revoke_refresh_tokens, mint_custom_token.
"""
import os
import sys

sys.path.insert(0, os.environ.get("PYTHONPATH", "/src/backend").split(":")[0])

import uvicorn  # noqa: E402
from fastapi import Depends, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import app.services.email_service as email_service  # noqa: E402
import app.services.enc_auth_identity as ident  # noqa: E402
from app.api.dependencies import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.services.enc_auth_identity import resolve_token_email  # noqa: E402

OUTBOX = []
FAULTS = {}


async def capture_otp_email(email, otp, valid_minutes=1):
    OUTBOX.append({"to": email, "otp": otp})
    return True


email_service.send_otp_email = capture_otp_email  # routes import it at call time, so this takes effect


def _wrap(name, fault, raises):
    original = getattr(ident, name)

    async def wrapper(*args, **kwargs):
        if FAULTS.get(fault):
            raise raises()
        return await original(*args, **kwargs)

    setattr(ident, name, wrapper)


_wrap("create_user", "create_user", ident.IdentityUnavailable)
_wrap("update_password", "update_password", ident.IdentityUnavailable)
_wrap("revoke_refresh_tokens", "revoke_refresh_tokens", ident.IdentityUnavailable)
_wrap("mint_custom_token", "mint_custom_token", ident.IdentityUnavailable)

_with_fault = ident.update_password  # already wraps the plain "update_password" fault


async def update_password(uid, new_password):
    if FAULTS.get("update_then_fail"):
        # the password IS changed, but the caller is told the outcome is unknown (a lost response)
        await _with_fault(uid, new_password)
        raise ident.IdentityUnavailable()
    return await _with_fault(uid, new_password)


ident.update_password = update_password


@app.get("/__g6a/outbox")
async def outbox():
    return JSONResponse(OUTBOX)


@app.post("/__g6a/faults")
async def set_faults(request: Request):
    FAULTS.clear()
    FAULTS.update({k: bool(v) for k, v in (await request.json()).items()})
    return JSONResponse(FAULTS)


@app.post("/__g6a/resolve_email")
async def resolve_email(current_user: dict = Depends(get_current_user)):
    """Exposes the D6 helper on its own: what e-mail would /sync-user and /auth/mark-verified use for this verified token?"""
    try:
        return JSONResponse({"email": await resolve_token_email(current_user)})
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.post("/__g6a/issue_proof")
async def issue_proof(request: Request):
    from app.services.email_verification import verify_otp_and_issue_proof
    from app.services.user_db import save_otp
    email = (await request.json())["email"]
    otp = "123456"
    await save_otp(email.lower(), otp)
    result, proof = await verify_otp_and_issue_proof(email, otp)
    return JSONResponse({"result": result, "proof": proof})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ["G6A_PORT"]), log_level="warning")
