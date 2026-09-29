"""Admin authorization for the admin feedback/dashboard routes
(security finding F-04). Reuses the existing Firebase ID-token
verification (`get_current_user`) and adds a server-side UID
allowlist check on top of it — Firebase authentication always
happens first.

Fails closed: an unconfigured allowlist, or an authenticated uid
that isn't on it, is rejected. Never logs the Firebase token, the
ADMIN_UIDS values, or the caller's uid — log messages are generic.
"""
from fastapi import Depends, HTTPException

from app.api.dependencies import get_current_user
from app.core.config import settings


def verify_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    admin_uids = {u.strip() for u in settings.ADMIN_UIDS.split(",") if u.strip()}
    if not admin_uids:
        print("[ADMIN-AUTH] Rejected: admin authorization is not configured", flush=True)
        raise HTTPException(status_code=503, detail="Admin authorization is not configured.")

    uid = current_user.get("uid", "")
    if not uid or uid not in admin_uids:
        print("[ADMIN-AUTH] Rejected: authenticated user is not an authorized admin", flush=True)
        raise HTTPException(status_code=403, detail="Not authorized for admin access.")

    return current_user
