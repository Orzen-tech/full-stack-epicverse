from fastapi import Depends, Header, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from firebase_admin import auth

security = HTTPBearer()

MFA_SESSION_REQUIRED = "MFA_SESSION_REQUIRED"
EMAIL_VERIFICATION_REQUIRED = "EMAIL_VERIFICATION_REQUIRED"

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Extracts firebase ID token from Authorization header, validates it, and returns user dict."""
    token = credentials.credentials
    try:
        decoded_token = auth.verify_id_token(token)
        return decoded_token
    except Exception as e:
        # Type only: the library's message for a malformed token includes the token text.
        print(f"Firebase token error: {type(e).__name__}")
        raise HTTPException(status_code=401, detail="Invalid Firebase Auth token")


async def mfa_security_error(claims: dict, raw_session: str | None, require_verified: bool) -> str | None:
    """F-09 Phase 3: the single server-side check shared by HTTP and the
    WebSocket. `claims` must come from a verified Firebase ID token; the UID
    and auth_time are taken only from it. Returns an error code, or None.

    No profile row means MFA is off (F-06 new-user creation). With MFA on, the
    MFA session must match this UID and Firebase sign-in, be unexpired and
    not revoked. Database errors propagate, so the check fails closed."""
    from app.services.user_db import get_security_state
    from app.services.mfa_challenge import validate_mfa_session, auth_time_from_claims

    uid = claims.get("uid")
    state = await get_security_state(uid) if uid else None
    if require_verified and not (state and state["email_verified"]):
        return EMAIL_VERIFICATION_REQUIRED
    if state and state["mfa_enabled"]:
        if not await validate_mfa_session(raw_session, uid, auth_time_from_claims(claims)):
            return MFA_SESSION_REQUIRED
    return None


def _raise_for(code: str) -> None:
    if code == EMAIL_VERIFICATION_REQUIRED:
        raise HTTPException(status_code=403, detail={
            "code": EMAIL_VERIFICATION_REQUIRED, "message": "Email verification required"})
    raise HTTPException(status_code=401, detail={
        "code": MFA_SESSION_REQUIRED, "message": "MFA verification required"})


async def require_mfa_if_enabled(
    current_user: dict = Depends(get_current_user),
    x_mfa_session: str | None = Header(default=None, alias="X-MFA-Session"),
) -> dict:
    """Firebase auth always; a valid MFA session only when MFA is enabled."""
    code = await mfa_security_error(current_user, x_mfa_session, require_verified=False)
    if code:
        _raise_for(code)
    return current_user


async def require_verified_mfa_user(
    current_user: dict = Depends(get_current_user),
    x_mfa_session: str | None = Header(default=None, alias="X-MFA-Session"),
) -> dict:
    """As require_mfa_if_enabled, plus a server-side verified email."""
    code = await mfa_security_error(current_user, x_mfa_session, require_verified=True)
    if code:
        _raise_for(code)
    return current_user
