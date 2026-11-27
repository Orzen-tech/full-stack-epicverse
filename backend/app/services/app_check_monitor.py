"""Firebase App Check verification (Finding #4 remediation).

Verifies the X-Firebase-AppCheck header when present and logs the
outcome (valid / missing / invalid / error). Never logs the token itself
or any other credential value.

APP_CHECK_MODE (settings) decides what happens next:
  * "monitor" (default, and any unrecognised value): observation only,
    the request always continues;
  * "enforce": a missing or invalid token is rejected with 403. An
    unexpected verification error (e.g. a transient JWKS fetch failure)
    is logged and the request continues, so a Google-side outage cannot
    lock every user out; a client cannot cause that outcome with a token.
"""

import jwt
from fastapi import HTTPException, Request
from firebase_admin import app_check

from app.core.config import settings

APP_CHECK_REJECTED_DETAIL = "App verification failed. Please update the app and try again."


def log_app_check_status(request: Request, endpoint: str) -> str:
    """Logs the App Check verification outcome for `endpoint` and returns it
    ("valid" | "missing" | "invalid" | "error"). Never raises, never blocks."""
    token = request.headers.get("X-Firebase-AppCheck")

    if not token:
        print(f"[APPCHECK] status=missing endpoint={endpoint}", flush=True)
        return "missing"

    try:
        app_check.verify_token(token)
        print(f"[APPCHECK] status=valid endpoint={endpoint}", flush=True)
        return "valid"
    except (ValueError, jwt.PyJWTError) as e:
        # ValueError: firebase_admin's own wrapping (bad signature/claims).
        # jwt.PyJWTError: a malformed token can fail earlier, inside
        # PyJWKClient's key lookup, before firebase_admin's wrapping applies.
        # Both are "not a valid token" from our point of view.
        print(f"[APPCHECK] status=invalid endpoint={endpoint} reason={type(e).__name__}", flush=True)
        return "invalid"
    except Exception as e:
        # Any other unexpected error (e.g. transient JWKS fetch failure) is
        # logged and swallowed — this must never affect the real request.
        print(f"[APPCHECK] status=error endpoint={endpoint} reason={type(e).__name__}", flush=True)
        return "error"


def app_check_mode() -> str:
    return "enforce" if (settings.APP_CHECK_MODE or "").strip().lower() == "enforce" else "monitor"


def check_app_check(request: Request, endpoint: str) -> None:
    """Verifies and logs as above; in "enforce" mode also rejects a missing or
    invalid token with 403. Monitor mode (the default) never rejects."""
    status = log_app_check_status(request, endpoint)
    if status in ("missing", "invalid") and app_check_mode() == "enforce":
        print(f"[APPCHECK] rejected endpoint={endpoint} status={status}", flush=True)
        raise HTTPException(status_code=403, detail=APP_CHECK_REJECTED_DETAIL)
