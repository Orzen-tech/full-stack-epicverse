"""OIDC verification for the Cloud Scheduler -> purge-expired-deletions
trigger (security finding F-03). Scoped to exactly one route — does not
affect authentication for any other endpoint.

Fails closed: any missing configuration, missing/malformed token, or
mismatched audience/issuer/identity rejects the request before the
destructive purge logic ever runs. Never logs the token, the
Authorization header, or any other credential material.
"""
from fastapi import Request, HTTPException
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_auth_requests

from app.core.config import settings

_TRUSTED_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


async def verify_scheduler_oidc(request: Request) -> None:
    if not settings.SCHEDULER_OIDC_AUDIENCE or not settings.SCHEDULER_SERVICE_ACCOUNT_ID:
        print(
            "[SCHEDULER-AUTH] Rejected: server missing "
            "SCHEDULER_OIDC_AUDIENCE/SCHEDULER_SERVICE_ACCOUNT_ID config",
            flush=True,
        )
        raise HTTPException(status_code=503, detail="Scheduler authentication is not configured.")

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        print("[SCHEDULER-AUTH] Rejected: missing or malformed Authorization header", flush=True)
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    token = auth_header[len("Bearer "):].strip()
    if not token:
        print("[SCHEDULER-AUTH] Rejected: empty bearer token", flush=True)
        raise HTTPException(status_code=401, detail="Missing bearer token.")

    try:
        claims = google_id_token.verify_oauth2_token(
            token, google_auth_requests.Request(), audience=settings.SCHEDULER_OIDC_AUDIENCE
        )
    except Exception as e:
        # Log only the exception type — never the token itself.
        print(f"[SCHEDULER-AUTH] Rejected: token verification failed ({type(e).__name__})", flush=True)
        raise HTTPException(status_code=403, detail="Invalid token.")

    issuer = claims.get("iss", "")
    if issuer not in _TRUSTED_ISSUERS:
        print("[SCHEDULER-AUTH] Rejected: unexpected issuer", flush=True)
        raise HTTPException(status_code=403, detail="Invalid token issuer.")

    caller_sub = claims.get("sub", "")
    if not caller_sub or caller_sub != settings.SCHEDULER_SERVICE_ACCOUNT_ID:
        print("[SCHEDULER-AUTH] Rejected: identity mismatch", flush=True)
        raise HTTPException(status_code=403, detail="Unauthorized identity.")

    print("[SCHEDULER-AUTH] Verified scheduler OIDC token — proceeding with purge.", flush=True)
