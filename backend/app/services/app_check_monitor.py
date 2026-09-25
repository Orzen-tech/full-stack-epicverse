"""Monitor-only Firebase App Check verification (Finding #4 remediation).

Verifies the X-Firebase-AppCheck header when present and logs the
outcome (valid / missing / invalid). Never rejects a request based on
the result — this is observation only, ahead of any future enforcement
decision. Never logs the token itself or any other credential value.
"""

import jwt
from fastapi import Request
from firebase_admin import app_check


def log_app_check_status(request: Request, endpoint: str) -> None:
    """Logs the App Check verification outcome for `endpoint`. Never raises,
    never blocks — failures here must not affect the calling request."""
    token = request.headers.get("X-Firebase-AppCheck")

    if not token:
        print(f"[APPCHECK] status=missing endpoint={endpoint}", flush=True)
        return

    try:
        app_check.verify_token(token)
        print(f"[APPCHECK] status=valid endpoint={endpoint}", flush=True)
    except (ValueError, jwt.PyJWTError) as e:
        # ValueError: firebase_admin's own wrapping (bad signature/claims).
        # jwt.PyJWTError: a malformed token can fail earlier, inside
        # PyJWKClient's key lookup, before firebase_admin's wrapping applies.
        # Both are "not a valid token" from our point of view.
        print(f"[APPCHECK] status=invalid endpoint={endpoint} reason={type(e).__name__}", flush=True)
    except Exception as e:
        # Any other unexpected error (e.g. transient JWKS fetch failure) is
        # logged and swallowed — this must never affect the real request.
        print(f"[APPCHECK] status=error endpoint={endpoint} reason={type(e).__name__}", flush=True)
