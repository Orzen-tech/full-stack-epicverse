from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Form, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, StringConstraints
from typing import Annotated
import io
import uuid
import json

# Local modules
from app.services.speech_to_text import transcribe_audio
from app.services.ai_pipeline import run_ai_pipeline
from app.services.text_to_speech import synthesize_speech
from app.services.storage import upload_to_gcs, log_interaction
from app.services.user_db import (
    UserRecord, get_user, save_otp, verify_otp,
    validate_invite_code, sync_user_with_invite_check,
    request_user_deletion, cancel_user_deletion, purge_expired_deletions,
    save_feedback, get_all_feedback, get_dashboard_data, mark_email_verified_for_uid,
    verify_session, update_session_id,
    record_email_verification_proof, mark_email_verified_with_proof,
    norm_email, email_proof_available, issue_email_verification_proof,
    consume_email_verification_proof,
)
from app.core.config import settings
from app.services.mfa_challenge import (
    MfaConfigError, PURPOSE_LOGIN_MFA, PURPOSE_ENABLE_MFA, PURPOSE_VERIFY_EMAIL,
    SESSION_LIFETIME, CHALLENGE_LIFETIME,
    RESULT_SUCCESS, RESULT_TOO_MANY_ATTEMPTS,
    auth_time_from_claims, signed_in_recently, create_mfa_challenge, invalidate_mfa_challenge,
    verify_login_challenge, confirm_enable_mfa, confirm_email_verification, validate_mfa_session,
    revoke_mfa_session, disable_mfa_and_revoke_state,
)
from app.api.dependencies import (
    get_current_user, require_mfa_if_enabled, require_verified_mfa_user, mfa_security_error,
)
from app.services.app_check_monitor import log_app_check_status
from app.api.scheduler_auth import verify_scheduler_oidc
from app.api.admin_auth import verify_admin_user
from app.services.otp_rate_limiter import check_otp_send_allowed

router = APIRouter()

@router.get("/validate-invite/{code}")
async def validate_invite(code: str):
    """Checks if an invite code exists in the database and has not been used."""
    code = code.replace(" ", "").upper()
    is_valid = await validate_invite_code(code)
    if not is_valid:
        return {"valid": False, "message": "Invalid or expired invite code"}
    return {"valid": True, "message": "Invite code accepted"}


@router.post("/sync-user")
async def sync_user(user: UserRecord, current_user: dict = Depends(require_mfa_if_enabled)):
    """The sole path that may create a new SQL `users` row (F-06).

    Existing SQL user: normal profile sync, no invite code required or
    consumed. Genuinely new SQL user: a valid, unexpired, unexhausted
    invite_code is mandatory — validated, consumed (current_uses
    incremented, never deleted), and the row created, all atomically in
    one transaction. See `sync_user_with_invite_check()`.
    """
    if not user.get_uid():
        raise HTTPException(status_code=422, detail="firebase_id or uid is required")
    if current_user.get("uid") != user.get_uid():
        raise HTTPException(status_code=403, detail="Forbidden")

    # F-06: a newly created row takes its email from the verified token,
    # enforced inside the creation transaction.
    token_email = (current_user.get("email") or "").strip() or None
    result = await sync_user_with_invite_check(user, verified_email=token_email)
    if result["status"] == "rejected":
        reason = result.get("reason")
        detail = {
            "email_required": "A verified email is required to create an account.",
            "invite_required": "A valid invite code is required to create a new account.",
            "invalid_invite": "Invalid invite code.",
            "expired_invite": "Invite code has expired.",
            "exhausted_invite": "Invite code has already reached its usage limit.",
            "email_mismatch": "Email does not match the signed-in account.",
        }.get(reason, "Invite code could not be validated.")
        raise HTTPException(status_code=403, detail=detail)
    return {"status": "success", "message": "User synchronized"}

async def _authorize_otp_request(invite_code: str | None, authorization: str | None) -> dict | None:
    """Authorizes an OTP request via either a valid invite code (signup flow)
    or a valid Firebase ID token (login/resend flow). Raises 403 otherwise.

    Returns the verified token claims for the token path, so the caller can
    bind the OTP target to the signed-in account's email, and None for the
    invite path (no account exists yet to bind to).
    """
    # Path 1: signup flow — caller proves they have a valid invite.
    if invite_code:
        normalized = invite_code.strip().upper()
        if normalized and not normalized.startswith("EPIC-"):
            normalized = f"EPIC-{normalized}"
        if normalized and await validate_invite_code(normalized):
            return
        # Invite code provided but invalid → reject without falling through to token check
        raise HTTPException(status_code=403, detail="Invalid or expired invite code")

    # Path 2: login/resend flow — caller is already signed into Firebase.
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token:
            try:
                from firebase_admin import auth as fb_auth
                return fb_auth.verify_id_token(token)
            except Exception as e:
                # Type only: the library's message for a malformed token includes the token text.
                print(f"[OTP] Bearer token rejected: {type(e).__name__}", flush=True)

    raise HTTPException(
        status_code=403,
        detail="OTP requires a valid invite code or authenticated session.",
    )


@router.post("/auth/send-otp")
async def send_otp(
    request: Request,
    identifier: str = Form(None),
    email: str = Form(None),
    invite_code: str = Form(None),
    authorization: str | None = Header(default=None),
):
    """Generates a 6-digit OTP, saves it to DB, and sends via SendGrid.

    Authorization: caller must supply either a valid `invite_code` (signup)
    or a valid Firebase ID token in the Authorization header (login/resend).
    A signed-in caller can only request a code for the email address of the
    account in their own token, never for an arbitrary address.
    """
    log_app_check_status(request, "send-otp")  # Monitor-only, see Finding #4

    identifier = norm_email(identifier or email)
    if not identifier:
        raise HTTPException(status_code=422, detail="identifier or email is required")

    claims = await _authorize_otp_request(invite_code, authorization)
    if claims is not None and norm_email(claims.get("email")) != identifier:
        raise HTTPException(
            status_code=403,
            detail="A code can only be sent to the email address of the signed-in account.",
        )

    allowed, retry_after = await check_otp_send_allowed(identifier, request)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many OTP requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)}
        )

    import secrets
    from app.services.email_service import send_otp_email

    otp = str(secrets.randbelow(900000) + 100000)
    db_success = await save_otp(identifier, otp)
    if not db_success:
        raise HTTPException(status_code=500, detail="Database error while saving OTP")

    if "@" in identifier:
        email_sent = await send_otp_email(identifier, otp)
        if not email_sent:
            raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")

    return {"status": "success", "message": "OTP sent successfully"}


@router.post("/auth/verify-otp")
async def verify_otp_route(request: Request, identifier: str = Form(None), email: str = Form(None), otp: str = Form(...)):
    """Verifies an email OTP. Nothing else.

    F-06: never creates a Firebase user, never writes a SQL `users` row,
    and never issues a sign-in token. Signup verifies the email before the
    Firebase account exists, so a missing Firebase user is not an error
    here. `/sync-user` remains the sole path that may create the SQL row,
    and only with a server-validated invite.

    F-09 H1: this endpoint is unauthenticated, so it must never decide WHICH
    account becomes verified. A correct OTP for an email address only earns
    the caller a random one-time proof, returned once in the response body.
    The proof is later presented to the authenticated /auth/mark-verified,
    which takes UID and email from the Firebase token. Only with
    EMAIL_VERIFY_LEGACY_COMPAT (a temporary rollout switch for old app
    builds, off by default) does this route still mark the Firebase account
    that owns the email.
    """
    log_app_check_status(request, "verify-otp")  # Monitor-only, see Finding #4

    identifier = norm_email(identifier or email)
    if not identifier:
        raise HTTPException(status_code=422, detail="identifier or email is required")

    legacy_compat = settings.EMAIL_VERIFY_LEGACY_COMPAT
    is_email = "@" in identifier
    if is_email and not legacy_compat and not email_proof_available():
        # Fail closed BEFORE the OTP is consumed, so a misconfigured server
        # does not burn the user's code.
        raise HTTPException(status_code=503, detail="Email verification is temporarily unavailable.")

    result = await verify_otp(identifier, otp)
    if result == 'too_many_attempts':
        raise HTTPException(status_code=429, detail="Too many failed attempts. Please request a new verification code.")
    if result != 'success':
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    body = {"status": "success", "message": "OTP verified"}
    if is_email:
        proof = await issue_email_verification_proof(identifier)
        if proof:
            body["email_verification_proof"] = proof
        elif not legacy_compat:
            raise HTTPException(status_code=503, detail="Email verification is temporarily unavailable.")
        if legacy_compat:
            # Legacy rollout path (old app builds). Counted without any
            # identifying data so the switch can be retired safely.
            print("[EMAIL-VERIFY] legacy_compat path=verify_otp_mark", flush=True)
            if not await _mark_verified_for_firebase_email(identifier):
                await record_email_verification_proof(identifier)

    return JSONResponse(content=body, headers={"Cache-Control": "no-store"})


async def _mark_verified_for_firebase_email(email: str) -> int:
    """Resolves the Firebase account for an OTP-verified email server-side
    and marks only that UID's profile. Returns rows marked. No account yet
    (new signup) is not an error; a failed lookup is logged without the
    email and never fails the already-successful OTP verification."""
    try:
        from firebase_admin import auth as fb_auth
        try:
            fb_user = fb_auth.get_user_by_email(email)
        except fb_auth.UserNotFoundError:
            return 0
    except Exception as e:
        print(f"[AUTH] verify-otp Firebase lookup failed: {type(e).__name__}", flush=True)
        return 0
    return await mark_email_verified_for_uid(fb_user.uid, email)


@router.post("/auth/send-password-reset")
async def send_password_reset(
    request: Request,
    identifier: str = Form(None),
    email: str = Form(None),
):
    """Generates a Firebase password reset link and sends it via SendGrid.
    Uses SendGrid for reliable inbox delivery instead of Firebase's default sender.
    """
    from app.services.email_service import send_password_reset_email

    target = (identifier or email or "").strip()
    if not target or "@" not in target:
        raise HTTPException(status_code=422, detail="Valid email required")

    # Distributed (Postgres) limiter shared with the OTP send routes; its own
    # namespace keeps reset requests from consuming OTP-send budget.
    allowed, retry_after = await check_otp_send_allowed(f"pwreset:{norm_email(target)}", request)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)},
        )

    try:
        from firebase_admin import auth as fb_auth
        reset_link = fb_auth.generate_password_reset_link(target)
    except fb_auth.UserNotFoundError:
        # Return 200 to avoid leaking whether the email is registered
        print("[AUTH] Password reset requested for an unknown account", flush=True)
        return {"status": "sent"}
    except Exception as e:
        print(f"[AUTH] generate_password_reset_link error: {type(e).__name__}", flush=True)
        raise HTTPException(status_code=500, detail="Failed to generate reset link")

    sent = await send_password_reset_email(target, reset_link)
    if not sent:
        raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    return {"status": "sent"}


@router.post("/auth/send-email-otp")
async def send_email_otp_preregistration(
    request: Request,
    identifier: str = Form(None),
    email: str = Form(None),
):
    """Open endpoint for pre-registration email verify. No invite code or token needed.
    F-07: rate-limited by the Redis-backed check_otp_send_allowed() (3 per
    10 min per email, plus a per-IP limit) instead of the in-memory limiter.
    """
    import secrets
    from app.services.email_service import send_otp_email

    target = norm_email(identifier or email)
    if not target or "@" not in target:
        raise HTTPException(status_code=422, detail="Valid email required")

    allowed, retry_after = await check_otp_send_allowed(target, request)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)},
        )

    otp = str(secrets.randbelow(900000) + 100000)
    if not await save_otp(target.lower(), otp):
        raise HTTPException(status_code=500, detail="Database error while saving OTP")
    sent = await send_otp_email(target, otp)
    if not sent:
        raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    return {"status": "sent"}


EMAIL_PROOF_INVALID = "EMAIL_PROOF_INVALID"


@router.post("/auth/mark-verified")
async def mark_email_verified_route(
    current_user: dict = Depends(get_current_user),
    proof: str = Form(None),
):
    """Sets email_verified=TRUE for the authenticated user, only with the
    one-time proof that /auth/verify-otp returned to this client.

    F-09 H1: UID and email come ONLY from the verified Firebase ID token;
    nothing in the request body can name another account, another email or
    set email_verified. The proof is consumed atomically with the update, so
    it works once, for the email it was issued for, within 15 minutes. Every
    failure returns the same 403 so callers cannot tell which check failed.
    Without a proof this endpoint verifies nothing, except during the
    temporary EMAIL_VERIFY_LEGACY_COMPAT rollout window for old app builds.
    """
    uid = current_user.get("uid")
    email = norm_email(current_user.get("email"))
    if not uid or not email:
        raise HTTPException(status_code=400, detail="No email on token")
    row = await get_user(uid)
    if row and row.get("email_verified"):
        return {"status": "ok"}
    if proof:
        verified = await consume_email_verification_proof(uid, email, proof)
    elif settings.EMAIL_VERIFY_LEGACY_COMPAT:
        print("[EMAIL-VERIFY] legacy_compat path=mark_verified_no_proof", flush=True)
        verified = await mark_email_verified_with_proof(uid, email) == 1
    else:
        verified = False
    if not verified:
        raise HTTPException(status_code=403, detail={
            "code": EMAIL_PROOF_INVALID, "message": "Email verification required"})
    return {"status": "ok"}


@router.post("/auth/email/verify-request")
async def email_verify_request(request: Request, current_user: dict = Depends(get_current_user)):
    """F-09 H1: starts email verification for the signed-in user.

    UID and email come only from the Firebase token. The code is sent to the
    email stored on this user's row, and only if that matches the token's
    email, so a code can never be steered to a different address. Needs no
    MFA session: an unverified account cannot have MFA enabled.
    """
    uid = current_user.get("uid")
    token_email = norm_email(current_user.get("email"))
    if not uid or "@" not in token_email:
        raise HTTPException(status_code=400, detail="No email on token")
    row = await get_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if row.get("email_verified"):
        return {"status": "already_verified"}
    db_email = norm_email(row.get("email"))
    if db_email != token_email:
        raise HTTPException(status_code=409, detail={
            "code": "EMAIL_MISMATCH", "message": "Email does not match the signed-in account."})

    allowed, retry_after = await check_otp_send_allowed(db_email, request)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)},
        )
    from app.services.email_service import send_otp_email
    try:
        challenge_id, otp = await create_mfa_challenge(uid, db_email, PURPOSE_VERIFY_EMAIL)
    except MfaConfigError:
        raise HTTPException(status_code=503, detail="Email verification is temporarily unavailable.")
    if not await send_otp_email(db_email, otp, valid_minutes=int(CHALLENGE_LIFETIME.total_seconds() // 60)):
        await invalidate_mfa_challenge(challenge_id, uid)
        raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    return {"status": "sent", "challenge_id": challenge_id,
            "expires_in": int(CHALLENGE_LIFETIME.total_seconds())}


@router.post("/auth/email/verify-confirm")
async def email_verify_confirm(
    challenge_id: str = Form(...),
    otp: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    """F-09 H1: completes email verification for the signed-in user. Only the
    authenticated UID can be verified, only with a `verify_email` challenge
    that was created for that UID and email."""
    if not _mfa_otp_valid(otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    uid = current_user.get("uid")
    token_email = norm_email(current_user.get("email"))
    if not uid or "@" not in token_email:
        raise HTTPException(status_code=400, detail="No email on token")
    try:
        result = await confirm_email_verification(uid, challenge_id, otp, token_email)
    except MfaConfigError:
        raise HTTPException(status_code=503, detail="Email verification is temporarily unavailable.")
    if result != RESULT_SUCCESS:
        _raise_for_result(result)
    return {"status": "verified"}


@router.post("/auth/update-session")
async def update_session(session_id: str = Form(...), current_user: dict = Depends(require_mfa_if_enabled)):
    """Called on every login. Writes this device's session_id to DB.
    Any other device holding a different session_id will be force-logged out."""
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    await update_session_id(uid, session_id)
    return {"status": "ok"}


@router.get("/auth/check-session")
async def check_session(session_id: str, current_user: dict = Depends(require_mfa_if_enabled)):
    """Returns whether the given session_id is still the active session for this user.
    If another device has logged in since, the stored session_id will differ."""
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    valid = await verify_session(uid, session_id)
    return {"valid": valid}


@router.get("/user/{firebase_id}")
async def fetch_user(firebase_id: str, current_user: dict = Depends(get_current_user)):
    """Fetches user info from the SQL database.

    Authorization (F-05): requires a valid Firebase ID token, and the
    caller's uid must match the requested `firebase_id`. A user can only
    ever fetch their own record.
    """
    caller_uid = current_user.get("uid")
    if caller_uid != firebase_id:
        raise HTTPException(status_code=403, detail="You can only view your own account.")
    user = await get_user(firebase_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/user/update-mfa")
async def update_user_mfa(
    mfa_enabled: bool = Form(...),
    current_user: dict = Depends(get_current_user),
    x_mfa_session: str | None = Header(default=None, alias="X-MFA-Session"),
):
    """Legacy toggle kept for released apps. It can no longer change MFA by
    itself: enabling requires the OTP flow (/user/mfa/enable-*), disabling
    requires the same MFA-session + recent-sign-in checks as /user/mfa/disable.
    A request matching the current state is a harmless no-op."""
    uid = current_user.get("uid")
    row = await get_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if bool(row.get("mfa_enabled")) == mfa_enabled:
        return {"status": "success", "message": f"MFA status updated to {mfa_enabled}"}
    if mfa_enabled:
        raise HTTPException(status_code=403, detail={
            "code": "MFA_ENABLE_FLOW_REQUIRED",
            "message": "Please update the app to enable MFA.",
        })
    await _require_mfa_change_authorization(current_user, x_mfa_session)
    await disable_mfa_and_revoke_state(uid)
    return {"status": "success", "message": "MFA status updated to False"}


# ---------------------------------------------------------------------------
# F-09: server-side MFA. Phase 1 provides these capabilities only; no other
# endpoint requires an MFA session yet.
# ---------------------------------------------------------------------------

_MFA_SESSION_REQUIRED = {"code": "MFA_SESSION_REQUIRED", "message": "MFA verification required"}


async def _require_mfa_change_authorization(current_user: dict, raw_session: str | None) -> None:
    """Valid MFA session for this UID + Firebase sign-in, and a sign-in
    within the last 15 minutes (Firebase-native re-auth; no password here)."""
    auth_time = auth_time_from_claims(current_user)
    if not await validate_mfa_session(raw_session, current_user.get("uid"), auth_time):
        raise HTTPException(status_code=401, detail=_MFA_SESSION_REQUIRED)
    if not signed_in_recently(auth_time):
        raise HTTPException(status_code=401, detail={
            "code": "RECENT_SIGN_IN_REQUIRED",
            "message": "Please sign in again to continue.",
        })


async def _send_mfa_challenge(request: Request, uid: str, purpose: str) -> dict:
    row = await get_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    email = (row.get("email") or "").strip().lower()
    if not row.get("email_verified") or "@" not in email:
        raise HTTPException(status_code=403, detail="Email verification required")
    if purpose == PURPOSE_LOGIN_MFA and not row.get("mfa_enabled"):
        raise HTTPException(status_code=409, detail="MFA is not enabled for this account")
    if purpose == PURPOSE_ENABLE_MFA and row.get("mfa_enabled"):
        return {"status": "already_enabled", "mfa_enabled": True}

    allowed, retry_after = await check_otp_send_allowed(email, request)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)},
        )
    from app.services.email_service import send_otp_email
    try:
        challenge_id, otp = await create_mfa_challenge(uid, email, purpose)
    except MfaConfigError:
        raise HTTPException(status_code=503, detail="MFA is temporarily unavailable")
    if not await send_otp_email(email, otp, valid_minutes=int(CHALLENGE_LIFETIME.total_seconds() // 60)):
        await invalidate_mfa_challenge(challenge_id, uid)
        raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    return {"status": "sent", "challenge_id": challenge_id,
            "expires_in": int(CHALLENGE_LIFETIME.total_seconds())}


def _mfa_otp_valid(otp: str) -> bool:
    return isinstance(otp, str) and len(otp) == 6 and otp.isdigit()


def _session_response(raw_token: str, **extra) -> dict:
    return {"status": "success", **extra, "mfa_session_token": raw_token,
            "expires_in": int(SESSION_LIFETIME.total_seconds())}


def _raise_for_result(result: str) -> None:
    if result == RESULT_TOO_MANY_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Please request a new code.")
    raise HTTPException(status_code=400, detail="Invalid or expired code")


@router.get("/auth/session-status")
async def auth_session_status(
    current_user: dict = Depends(get_current_user),
    x_mfa_session: str | None = Header(default=None, alias="X-MFA-Session"),
):
    """Server-authoritative state for the signed-in user. Informational in
    Phase 1; nothing is enforced from here."""
    uid = current_user.get("uid")
    row = await get_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    session_valid = await validate_mfa_session(x_mfa_session, uid, auth_time_from_claims(current_user))
    return {
        "email_verified": bool(row.get("email_verified")),
        "mfa_enabled": bool(row.get("mfa_enabled")),
        "mfa_session_valid": session_valid,
    }


@router.post("/auth/mfa/challenge")
async def mfa_login_challenge(request: Request, current_user: dict = Depends(get_current_user)):
    return await _send_mfa_challenge(request, current_user.get("uid"), PURPOSE_LOGIN_MFA)


@router.post("/auth/mfa/verify")
async def mfa_login_verify(
    challenge_id: str = Form(...),
    otp: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    if not _mfa_otp_valid(otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    auth_time = auth_time_from_claims(current_user)
    if auth_time is None:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    try:
        result, raw_token = await verify_login_challenge(current_user.get("uid"), challenge_id, otp, auth_time)
    except MfaConfigError:
        raise HTTPException(status_code=503, detail="MFA is temporarily unavailable")
    if result != RESULT_SUCCESS:
        _raise_for_result(result)
    return _session_response(raw_token)


@router.post("/auth/mfa/logout")
async def mfa_logout(
    current_user: dict = Depends(get_current_user),
    x_mfa_session: str | None = Header(default=None, alias="X-MFA-Session"),
):
    await revoke_mfa_session(x_mfa_session, current_user.get("uid"))
    return {"status": "ok"}


@router.post("/user/mfa/enable-request")
async def mfa_enable_request(request: Request, current_user: dict = Depends(get_current_user)):
    return await _send_mfa_challenge(request, current_user.get("uid"), PURPOSE_ENABLE_MFA)


@router.post("/user/mfa/enable-confirm")
async def mfa_enable_confirm(
    challenge_id: str = Form(...),
    otp: str = Form(...),
    current_user: dict = Depends(get_current_user),
):
    if not _mfa_otp_valid(otp):
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    auth_time = auth_time_from_claims(current_user)
    if auth_time is None:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    try:
        result, raw_token = await confirm_enable_mfa(current_user.get("uid"), challenge_id, otp, auth_time)
    except MfaConfigError:
        raise HTTPException(status_code=503, detail="MFA is temporarily unavailable")
    if result != RESULT_SUCCESS:
        _raise_for_result(result)
    return _session_response(raw_token, mfa_enabled=True)


@router.post("/user/mfa/disable")
async def mfa_disable(
    current_user: dict = Depends(get_current_user),
    x_mfa_session: str | None = Header(default=None, alias="X-MFA-Session"),
):
    uid = current_user.get("uid")
    row = await get_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if not row.get("mfa_enabled"):
        return {"status": "success", "mfa_enabled": False}
    await _require_mfa_change_authorization(current_user, x_mfa_session)
    await disable_mfa_and_revoke_state(uid)
    return {"status": "success", "mfa_enabled": False}


def _ws_safe(value, tail: int = 8) -> str:
    """Client-supplied handshake values for logs: repr() escapes CR/LF and
    control characters (no forged log lines), and only the last `tail`
    characters are kept (bounded size, identifiers never logged in full)."""
    s = "" if value is None else str(value)
    return ("…" if len(s) > tail else "") + repr(s[-tail:])


@router.websocket("/ws/realtime")
async def websocket_realtime(
    websocket: WebSocket,
    uid: str = "anonymous",
    mode: str = "Mode 1",
    session_id: str = "default",
    token: str = "",
):
    """OpenAI Realtime API proxy — bidirectional audio bridge.

    Token source: Prefer the `Authorization: Bearer <id_token>` handshake header
    (keeps the ID token out of Cloud Run / LB / proxy access logs). The `token`
    query-string parameter is retained only as a transitional fallback for
    older app builds and will be removed after all clients have upgraded.
    """
    from app.services.realtime_service import RealtimeSession
    await websocket.accept()

    # Prefer Authorization header; fall back to ?token= for older clients.
    auth_header = websocket.headers.get("authorization", "")
    header_token = ""
    if auth_header.lower().startswith("bearer "):
        header_token = auth_header[7:].strip()

    resolved_token = header_token or token
    if header_token:
        token_source = "header"
    elif token:
        token_source = "query(legacy)"
    else:
        token_source = "none"

    # Mandatory Firebase auth — reject unauthenticated/anonymous connections.
    if not uid or uid == "anonymous" or not resolved_token:
        print(f"[WS] Auth rejected — missing uid or token (uid={_ws_safe(uid)} source={token_source})", flush=True)
        await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
        await websocket.close(code=1008)
        return

    try:
        from firebase_admin import auth as fb_auth
        decoded = fb_auth.verify_id_token(resolved_token)
        if decoded.get("uid") != uid:
            print(f"[WS] Auth rejected — uid mismatch (claim={_ws_safe(decoded.get('uid'))}, query={_ws_safe(uid)})", flush=True)
            await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
            await websocket.close(code=1008)
            return
        if token_source == "query(legacy)":
            # Visibility for rollout: tells you when the last legacy client upgrades.
            print(f"[WS] LEGACY token in query string uid={_ws_safe(uid)} — client should upgrade to header auth", flush=True)
    except Exception as e:
        # Type only: the library's message for a malformed token includes the token text.
        print(f"[WS] Auth failed uid={_ws_safe(uid)} source={token_source}: {type(e).__name__}", flush=True)
        await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
        await websocket.close(code=1008)
        return

    # F-09 Phase 3: verified email and, when MFA is on, a valid MFA session —
    # read only from the handshake header, never from the query string.
    security_code = await mfa_security_error(
        decoded, websocket.headers.get("x-mfa-session"), require_verified=True)
    if security_code:
        print(f"[WS] Rejected: {security_code}", flush=True)
        await websocket.send_text(json.dumps({"type": "error", "code": security_code, "message": "Unauthorized"}))
        await websocket.close(code=1008)
        return

    print(f"[WS] Realtime connection uid={_ws_safe(uid)} mode={_ws_safe(mode, 24)} session={_ws_safe(session_id, 16)}", flush=True)
    session = RealtimeSession(
        client_ws=websocket,
        uid=uid,
        mode=mode,
        session_id=session_id,
    )
    try:
        await session.run()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_text(json.dumps({"type": "error", "message": str(e)}))
        except Exception:
            pass

@router.post("/process-audio")
async def process_voice_audio(
    audio_file: UploadFile = File(...),
    game_mode: str | None = Form(None),
    current_user: dict = Depends(require_verified_mfa_user)
):
    """
    Main pipeline entry for the Multilingual AI Voice Agent:
    1. STT (Google)
    2. Lang detection & translation to EN (OpenAI)
    3. Generate response with Knowledge Base (OpenAI)
    4. Translate EN -> User Lang (OpenAI)
    5. TTS (Google)
    6. Log data & Return Audio (GCS + FastAPI StreamingResponse)
    """
    try:
        # Step 1: Read audio bytes from user
        audio_bytes = await audio_file.read()
        session_id = str(uuid.uuid4())
        
        # Log to Storage
        await upload_to_gcs(f"inputs/{session_id}.wav", audio_bytes)
        
        # Step 2: Speech Recognition
        stt_result = await transcribe_audio(audio_bytes)
        recognized_text = stt_result.get("text")
        user_lang = stt_result.get("language")
        
        if not recognized_text:
            raise HTTPException(status_code=400, detail="Could not recognize speech.")
            
        # Steps 3, 4, 5: AI Processing Pipeline (use uid as session key so each user has own history)
        ai_result = await run_ai_pipeline(recognized_text, game_mode=game_mode, session_id=current_user.get("uid", session_id), detected_language=user_lang)
        final_text = ai_result.get("final_response")
        
        # Step 6: Text-To-Speech Synthesis (OpenAI TTS)
        output_audio_bytes = await synthesize_speech(final_text, language_code=user_lang)
        
        # Step 8 & 9: Data Logging and monitoring
        await log_interaction(
            session_id=session_id,
            user_id=current_user.get("uid"),
            query=recognized_text,
            response=final_text,
            user_language=user_lang
        )
        
        import urllib.parse
        # Step 7: Stream audio back to Flutter mobile app
        return StreamingResponse(
            io.BytesIO(output_audio_bytes), 
            media_type="audio/wav", 
            headers={
                "X-Detected-Language": urllib.parse.quote(str(user_lang)),
                "X-Transcript": urllib.parse.quote(str(recognized_text)),
                "X-Response-Text": urllib.parse.quote(str(final_text))
            }
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")

@router.delete("/user/{firebase_id}")
async def delete_user(
    firebase_id: str,
    current_user: dict = Depends(require_mfa_if_enabled),
):
    """Soft-deletes a user account with a 30-day grace period.

    The account is marked `deletion_requested_at = NOW()` but remains in
    both Firebase and the SQL database. If the user signs back in within
    30 days, the mobile login/splash flow detects the pending deletion
    from the profile GET response and explicitly calls the authenticated
    `POST /user/{firebase_id}/cancel-deletion` below to cancel it (F-05:
    `GET /user/{firebase_id}` is a pure read and never cancels deletion
    itself). After 30 days the scheduled `/admin/purge-expired-deletions`
    job permanently removes the account from both Firebase and the
    database.

    Authorization: requires a valid Firebase ID token, and the caller's
    uid must match `firebase_id`. Users can only schedule deletion of
    their own account.
    """
    caller_uid = current_user.get("uid")
    if caller_uid != firebase_id:
        print(f"[DELETE] Forbidden — caller={caller_uid!r} target={firebase_id!r}", flush=True)
        raise HTTPException(status_code=403, detail="You can only delete your own account.")

    print(f"[DELETE] Scheduling deletion (30-day grace) for uid={firebase_id}", flush=True)

    ok = await request_user_deletion(firebase_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to schedule account deletion.")

    return {
        "status": "success",
        "message": "Account scheduled for deletion in 30 days. Sign in before then to cancel.",
        "grace_period_days": 30,
    }


@router.post("/user/{firebase_id}/cancel-deletion")
async def cancel_deletion(
    firebase_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Explicitly cancels a pending account deletion. This is the sole
    mechanism that cancels a pending deletion (F-05: `get_user()`/
    `GET /user/{firebase_id}` are pure reads and perform no cancellation).
    The mobile login/splash flow calls this endpoint automatically when
    it detects a non-null `deletion_requested_at` on the profile GET
    response, preserving the "sign back in within 30 days" promise.
    """
    caller_uid = current_user.get("uid")
    if caller_uid != firebase_id:
        raise HTTPException(status_code=403, detail="You can only cancel deletion of your own account.")
    ok = await cancel_user_deletion(firebase_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to cancel deletion.")
    return {"status": "success", "message": "Pending deletion cancelled."}


@router.get("/admin/feedback")
async def admin_get_feedback(_: dict = Depends(verify_admin_user)):
    rows = await get_all_feedback()
    return {"total": len(rows), "feedback": rows}


@router.post("/admin/purge-expired-deletions")
async def purge_expired_deletions_route(_: None = Depends(verify_scheduler_oidc)):
    """Hard-deletes all accounts whose 30-day grace period has elapsed.

    Intended to be called by a scheduled job (Cloud Scheduler hitting this
    endpoint daily). Removes purged uids from Firebase Auth in addition to
    the SQL database.

    Authorization (F-03): requires a Google-signed OIDC bearer token,
    verified by `verify_scheduler_oidc`, whose audience and service-account
    identity match the dedicated scheduler configuration. See
    `app/api/scheduler_auth.py`.
    """
    purged_uids = await purge_expired_deletions()
    fb_results: list[dict] = []
    if purged_uids:
        try:
            from firebase_admin import auth as fb_auth
        except Exception as e:
            print(f"[PURGE] firebase_admin import failed: {e}", flush=True)
            fb_auth = None  # type: ignore
        for uid in purged_uids:
            if fb_auth is None:
                fb_results.append({"uid": uid, "firebase": "skipped"})
                continue
            try:
                fb_auth.delete_user(uid)
                fb_results.append({"uid": uid, "firebase": "deleted"})
            except Exception as e:
                print(f"[PURGE] Firebase delete failed uid={uid}: {e}", flush=True)
                fb_results.append({"uid": uid, "firebase": f"error:{e}"})
    return {
        "status": "success",
        "purged_count": len(purged_uids),
        "results": fb_results,
    }


# Legal content endpoints - content can be updated without app release
@router.get("/legal/privacy")
async def get_privacy_policy():
    """Returns the Privacy Policy content. Update this text to change the policy site-wide."""
    return {
        "title": "Privacy Policy",
        "content": """EpicVerse Privacy Policy



1. Introduction
Welcome to EpicVerse, developed by Kriyora ("we", "our", or "us"). This Privacy Policy describes how we collect, use, disclose, and protect your personal information when you use the EpicVerse mobile application (the "App"), available on the Google Play Store and Apple App Store.
By downloading or using EpicVerse, you agree to the terms of this Privacy Policy. If you do not agree, please do not use the App.

2. Information We Collect
2.1 Information You Provide
•	Account information: email address, display name, and profile picture when you register or sign in via Google or other third-party authentication providers.
•	User-generated content: text, voice recordings, images, or other content you create or upload within the App.
•	Communications: messages or feedback you send to us.
2.2 Information Collected Automatically
•	Device information: device model, operating system version, unique device identifiers, and mobile network information.
•	Usage data: features you use, interaction logs, session duration, and in-app actions.
•	Log data: IP address, app crash reports, and diagnostic information.
•	Bluetooth data: device scanning and connection metadata when using Bluetooth-enabled features (e.g., companion device pairing).
2.3 Permissions We Request
EpicVerse requests the following device permissions to provide its core features:
•	Camera: To capture images for use within the App.
•	Microphone / Audio Recording: To enable voice input, voice commands, or audio-based interactions within EpicVerse.
•	Storage / Media: To read and save images or media files on your device.
•	Bluetooth: To discover and connect to supported companion devices.
•	Internet & Network State: To sync content, authenticate users, and communicate with our servers.
You may manage these permissions at any time in your device settings. Denying certain permissions may limit App functionality.

3. How We Use Your Information
We use the information we collect to:
•	Create and manage your account and authenticate your identity via Firebase Authentication.
•	Deliver and personalize the EpicVerse experience, including AI-driven companion interactions and story modes.
•	Process voice input and other media to power in-app features.
•	Analyse usage patterns to improve App performance, content, and features.
•	Communicate with you about updates, support, or promotional offers (with your consent where required).
•	Ensure the security and integrity of the App and detect fraudulent or abusive activity.
•	Comply with applicable legal obligations.

4. How We Share Your Information
We do not sell your personal information. We may share your information with:
•	Service Providers: Third-party vendors who assist us in operating the App, including Firebase (Google LLC) for authentication and backend services.
•	Analytics Providers: Tools that help us understand App usage and performance. Data shared is aggregated or anonymised where possible.
•	Legal Requirements: If required by law, court order, or governmental authority, or to protect the rights and safety of Kriyora, our users, or the public.
•	Business Transfers: In connection with a merger, acquisition, or sale of all or part of our assets, your information may be transferred to the successor entity.
All third-party service providers are contractually required to protect your data and use it only for the purposes we specify.

5. Data Retention
We retain your personal information for as long as your account is active or as necessary to provide our services. You may request deletion of your account and associated data at any time by contacting us at the address below. We will respond to deletion requests within 30 days, subject to legal retention obligations.

6. Data Security
We implement industry-standard technical and organisational measures to protect your information against unauthorised access, alteration, disclosure, or destruction. These include encrypted data transmission (HTTPS/TLS), Firebase security rules, and access controls.
However, no method of transmission over the internet or electronic storage is 100% secure. We cannot guarantee absolute security and encourage you to use strong, unique passwords and to log out of your account when not in use.

7. Children’s Privacy
EpicVerse is not directed to children under the age of 13 (or the applicable age of digital consent in your jurisdiction). We do not knowingly collect personal information from children. If you believe a child has provided us with personal information, please contact us immediately and we will take steps to delete such information.
Users between 13 and 17 should use the App only with parental consent.

8. Your Rights and Choices
Depending on your location, you may have the following rights regarding your personal information:
•	Access: Request a copy of the personal information we hold about you.
•	Correction: Request that we correct inaccurate or incomplete information.
•	Deletion: Request that we delete your personal information, subject to certain exceptions.
•	Opt-out: Opt out of marketing communications at any time by following the unsubscribe instructions in our emails or contacting us directly.
•	Data Portability: Request that we provide your data in a portable, machine-readable format.
To exercise any of these rights, please contact us at the address listed in Section 11. We will respond within the timeframe required by applicable law.

9. Third-Party Links and Services
The App may integrate with or link to third-party services (e.g., Google Sign-In). These services have their own privacy policies and we are not responsible for their data practices. We encourage you to review the privacy policies of any third-party services you use in connection with EpicVerse.

10. International Data Transfers
Your information may be transferred to and processed in countries other than your own, including the United States, where our service providers (such as Google Firebase) operate. These countries may have different data protection laws. Where required, we ensure appropriate safeguards are in place for such transfers, including standard contractual clauses.

11. Changes to This Privacy Policy
We may update this Privacy Policy from time to time. We will notify you of material changes by updating the "Effective Date" at the top of this policy and, where appropriate, by providing notice within the App or via email. Your continued use of EpicVerse after any changes constitutes your acceptance of the updated policy.

12. Contact Us
If you have any questions, concerns, or requests regarding this Privacy Policy, please contact us:
Kriyora
Email: support@kriyora.com
Website: https://kriyora.com
Address: Unit 101 Oxford Towers, HAL Old Airport Rd, H.A.L II Stage, Bangalore North, Bangalore- 560008, Karnataka
"""
    }


@router.get("/legal/terms")
async def get_terms_of_service():
    """Returns the Terms of Service content. Update this text to change terms site-wide."""
    return {
        "title": "Terms of Service",
        "content": """EpicVerse Terms of Service

1. Acceptance of Terms
These Terms of Service ("Terms") constitute a legally binding agreement between you and Kriyora ("we", "us", or "our"), the developer of the EpicVerse mobile application ("App"). By downloading, installing, or using the App — available on the Google Play Store and Apple App Store — you agree to be bound by these Terms and our Privacy Policy.
If you do not agree to these Terms, do not download, install, or use EpicVerse. If you are under 18 years of age, you must have the consent of a parent or legal guardian to use the App.

2. Eligibility
You must be at least 13 years of age (or the applicable minimum age in your jurisdiction) to use EpicVerse. By using the App, you represent and warrant that:
•	You meet the minimum age requirement described above.
•	You have the legal authority to enter into these Terms.
•	Your use of the App does not violate any applicable law or regulation.
Users between 13 and 17 may only use the App with the consent and supervision of a parent or legal guardian who agrees to these Terms on their behalf.

3. Account Registration and Security
To access certain features of EpicVerse, you must create an account. You may register using a supported third-party authentication service (e.g., Google Sign-In). By creating an account, you agree to:
•	Provide accurate, current, and complete information.
•	Maintain the security of your account credentials.
•	Notify us immediately of any unauthorised access to or use of your account.
•	Accept responsibility for all activity that occurs under your account.
We reserve the right to suspend or terminate accounts that violate these Terms or are used for fraudulent or abusive purposes.

4. Licence to Use the App
Subject to your compliance with these Terms, Kriyora grants you a limited, non-exclusive, non-transferable, revocable licence to download and use EpicVerse on a device you own or control, solely for your personal, non-commercial purposes.
This licence does not include the right to:
•	Reproduce, distribute, modify, or create derivative works of the App or its content.
•	Reverse engineer, decompile, or disassemble any part of the App.
•	Remove or alter any proprietary notices or labels on the App.
•	Use the App for any commercial purpose or on behalf of any third party without our express written consent.

5. User-Generated Content
5.1 Your Content
EpicVerse may allow you to create, upload, record, or share content including text, voice recordings, images, and other materials ("User Content"). You retain ownership of your User Content. By submitting User Content, you grant Kriyora a worldwide, royalty-free, non-exclusive licence to use, store, process, and display your User Content solely to operate and improve the App.
5.2 Content Standards
You agree not to create or upload User Content that:
•	Is unlawful, harmful, harassing, defamatory, obscene, or otherwise objectionable.
•	Infringes the intellectual property rights of any third party.
•	Contains viruses, malware, or other harmful code.
•	Violates the privacy or personal rights of any individual.
•	Impersonates any person or entity.
We reserve the right to remove User Content that violates these standards without notice.
5.3 Feedback
If you submit feedback, suggestions, or ideas about EpicVerse, you grant Kriyora a perpetual, irrevocable, royalty-free licence to use such feedback for any purpose without any obligation to you.

6. App Permissions and Device Access
EpicVerse requests access to certain device functions. Your use of these features constitutes your consent to the following:
•	Camera: Used to capture and upload images within the App.
•	Microphone / Audio Recording: Used for voice input, commands, or audio-based interactions.
•	Storage / Media: Used to read and save media files on your device.
•	Bluetooth: Used to discover and connect to companion devices.
•	Internet & Network Access: Required for all online features of the App.
You may revoke permissions at any time in your device settings. Revoking certain permissions will limit or disable related features.

7. In-App Purchases and Payments
EpicVerse may offer optional in-app purchases or premium features. All purchases are processed by the applicable app store platform (Google Play or Apple App Store) in accordance with their respective terms and policies. Kriyora does not directly handle payment information.
All purchases are final and non-refundable, except as required by applicable law or as provided by the relevant app store’s refund policy. If you believe you were charged incorrectly, please contact the app store platform directly.

8. AI-Powered Features
EpicVerse incorporates artificial intelligence features including, but not limited to, AI companion characters, narrative generation, and voice interaction. By using these features, you acknowledge that:
•	AI-generated content is produced algorithmically and may not always be accurate, appropriate, or complete.
•	You should not rely on AI-generated content for any critical decisions.
•	We do not guarantee the accuracy, completeness, or suitability of AI-generated responses.
•	Interactions with AI companions are for entertainment and personal use only and do not constitute professional advice of any kind.

9. Prohibited Conduct
You agree not to:
•	Use the App in any manner that could damage, disable, overburden, or impair our servers or networks.
•	Attempt to gain unauthorised access to any part of the App or its related systems.
•	Use automated tools (bots, scrapers, etc.) to access the App.
•	Circumvent or attempt to circumvent any technological protection measures.
•	Use the App to send spam, unsolicited messages, or harmful content.
•	Engage in any activity that violates applicable local, national, or international laws or regulations.

10. Intellectual Property
All content, designs, graphics, logos, software, and other materials in EpicVerse (excluding User Content) are owned by or licensed to Kriyora and are protected by applicable intellectual property laws. The name "EpicVerse" and all associated branding are trademarks of Kriyora.
Nothing in these Terms grants you any rights in or to Kriyora’s intellectual property except as expressly set forth herein. Unauthorised use of our intellectual property is strictly prohibited.

11. Third-Party Services
The App integrates with third-party services, including Firebase (Google LLC) for authentication and backend services. Your use of such third-party services is subject to their respective terms and privacy policies. We are not responsible for the practices or content of any third-party services.

12. Additional Terms for App Store Users
12.1 Google Play Store
If you downloaded EpicVerse from the Google Play Store, your use is also subject to Google Play’s Terms of Service. In the event of any conflict between these Terms and Google Play’s terms, Google Play’s terms shall govern solely with respect to your use of the Play Store platform.
12.2 Apple App Store
If you downloaded EpicVerse from the Apple App Store, the following terms apply:
•	These Terms are between you and Kriyora only, not Apple Inc. ("Apple"). Apple is not responsible for EpicVerse or its content.
•	Apple has no obligation whatsoever to furnish any maintenance or support services for EpicVerse.
•	In the event of any product liability claim, Apple is not responsible for investigating, defending, or settling such claim.
•	Apple and Apple’s subsidiaries are third-party beneficiaries of these Terms and, upon your acceptance, Apple will have the right to enforce these Terms against you as a third-party beneficiary.
•	You represent that you are not located in a country subject to a U.S. Government embargo, or listed on any U.S. Government list of prohibited or restricted parties.
Your use of EpicVerse on an Apple device is also subject to the Apple Media Services Terms and Conditions and the App Store Review Guidelines.

13. Disclaimers
THE APP IS PROVIDED ON AN "AS IS" AND "AS AVAILABLE" BASIS WITHOUT WARRANTIES OF ANY KIND, WHETHER EXPRESS OR IMPLIED. TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW, KRIYORA DISCLAIMS ALL WARRANTIES, INCLUDING IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT.
WE DO NOT WARRANT THAT THE APP WILL BE UNINTERRUPTED, ERROR-FREE, SECURE, OR FREE OF VIRUSES OR OTHER HARMFUL COMPONENTS.

14. Limitation of Liability
TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, KRIYORA AND ITS OFFICERS, DIRECTORS, EMPLOYEES, AGENTS, AND LICENSORS SHALL NOT BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES ARISING OUT OF OR RELATING TO YOUR USE OF OR INABILITY TO USE THE APP, EVEN IF WE HAVE BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES.
IN NO EVENT SHALL KRIYORA’S TOTAL LIABILITY TO YOU FOR ALL CLAIMS EXCEED THE GREATER OF (A) THE AMOUNT YOU PAID FOR THE APP IN THE TWELVE (12) MONTHS PRECEDING THE CLAIM, OR (B) USD $100.
Some jurisdictions do not allow the exclusion of certain warranties or the limitation of liability, so the above limitations may not apply to you.

15. Indemnification
You agree to indemnify, defend, and hold harmless Kriyora, its affiliates, officers, directors, employees, and agents from and against any claims, liabilities, damages, losses, and expenses (including reasonable legal fees) arising out of or relating to: (a) your use or misuse of the App; (b) your User Content; (c) your violation of these Terms; or (d) your violation of any third-party right.

16. Termination
We reserve the right to suspend or terminate your access to EpicVerse at any time, with or without cause or notice, including if we believe you have violated these Terms.
Upon termination, your licence to use the App will immediately cease and you must delete the App from your devices. Sections 5.3, 10, 13, 14, 15, and 18 shall survive termination.
You may also terminate these Terms at any time by deleting the App and your account.

17. Changes to These Terms
We may update these Terms from time to time. We will notify you of material changes by posting the new Terms within the App and updating the "Effective Date" above. Your continued use of EpicVerse after the effective date of revised Terms constitutes your acceptance of the changes.
If you do not agree to the updated Terms, you must stop using the App and delete your account.

18. Governing Law and Dispute Resolution
These Terms are governed by and construed in accordance with the laws of [Insert Jurisdiction, e.g., India / State of Delaware, USA], without regard to its conflict of law provisions.
Any dispute arising out of or relating to these Terms or the App that cannot be resolved informally shall be submitted to binding arbitration in accordance with [applicable arbitration rules], except that either party may seek injunctive or other equitable relief in any court of competent jurisdiction.
Nothing in this section shall limit either party’s right to seek emergency or injunctive relief from a court of competent jurisdiction.

19. General Provisions
•	Entire Agreement: These Terms, together with our Privacy Policy, constitute the entire agreement between you and Kriyora with respect to EpicVerse.
•	Severability: If any provision of these Terms is found invalid or unenforceable, the remaining provisions shall remain in full force and effect.
•	No Waiver: Our failure to enforce any right or provision of these Terms will not be considered a waiver of those rights.
•	Assignment: You may not assign or transfer your rights under these Terms without our prior written consent. We may assign our rights without restriction.

20. Contact Us
If you have questions about these Terms, please contact us:
Kriyora
Email: support@kriyora.com
Website: https://kriyora.com
Address: Unit 101 Oxford Towers, HAL Old Airport Rd, H.A.L II Stage, Bangalore North, Bangalore- 560008, Karnataka
"""
 }


_FAQ_ITEMS = [
    {
        "question": "What is EpicVerse?",
        "answer": "EpicVerse is an AI-powered voice companion app that lets you have real-time voice conversations with intelligent AI characters across different game modes and universes."
    },
    {
        "question": "How do I start a conversation?",
        "answer": "Go to the Dashboard, select a mode, tap the companion card, and press the microphone button to start speaking. The AI will respond in real time."
    },
    {
        "question": "What is an invite code and how do I get one?",
        "answer": "EpicVerse is currently invite-only. You need a valid EPIC-XXXXXX invite code to create an account. The invite code will be given to you along with the EPicVerse kit. If any isssues logging in, please contact us at support@kriyora.com."
    },
    {
        "question": "Is my voice data stored?",
        "answer": "No. Voice recordings are processed in real time via OpenAI and are not stored permanently on our servers. Only transcribed text interactions may be logged for quality improvements."
    },
    {
        "question": "Which languages are supported?",
        "answer": "EpicVerse supports multiple languages including English, Hindi, Tamil, Telugu, Kannada, Malayalam, Bengali, and more. The app auto-detects your spoken language."
    },
    {
        "question": "Why does the app need microphone permission?",
        "answer": "The microphone is required for voice interaction — it is the core feature of EpicVerse. Audio is only recorded while you actively hold the mic button."
    },
    {
        "question": "How do I delete my account?",
        "answer": "Go to Settings → Delete Account. Your account will be scheduled for deletion in 30 days. If you sign back in within 30 days, the deletion is automatically cancelled and your account is fully restored."
    },
    {
        "question": "Can I change my display name or profile photo?",
        "answer": "Yes. In Settings, tap the edit icon next to your name to change your display name, or tap your profile photo to update it from your camera or gallery."
    },
    {
        "question": "The voice response is slow — what can I do?",
        "answer": "Response speed depends on your internet connection and server load. Make sure you have a stable Wi-Fi or mobile data connection. If slowness persists, please send us feedback."
    },
    {
        "question": "How do I contact support?",
        "answer": "Use the Send Feedback option in Settings, or email us directly at support@kriyora.com. We typically respond within 1-2 business days."
    },
]


@router.get("/faq")
async def get_faq():
    return {"items": _FAQ_ITEMS}


FEEDBACK_MAX_CHARS = 5000


class FeedbackRequest(BaseModel):
    # Surrounding whitespace is trimmed first, then the length (in characters)
    # is checked, so an oversized message is rejected by request validation
    # (422) before the route stores it or queues the notification email.
    message: Annotated[str, StringConstraints(strip_whitespace=True, max_length=FEEDBACK_MAX_CHARS)]


class _NoEchoValidationRoute(APIRoute):
    """Used only by the feedback route. FastAPI's default 422 body repeats the
    rejected input, which would reflect a user's (possibly very large) message
    back to them. Same 422 and error details, minus the echoed `input`; nothing
    from the request is logged. Other routes keep FastAPI's default handling."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [{k: v for k, v in err.items() if k != "input"} for err in exc.errors()]
                return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})

        return sanitized


_feedback_router = APIRouter(route_class=_NoEchoValidationRoute)


@_feedback_router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    current_user: dict = Depends(require_verified_mfa_user),
):
    from app.services.email_service import send_feedback_notification
    uid = current_user.get("uid")
    if not body.message.strip():
        raise HTTPException(status_code=422, detail="Feedback message cannot be empty")
    await save_feedback(uid, body.message.strip())
    # Notify owner — fire and forget, don't block the response
    try:
        user = await get_user(uid)
        display_name = (user or {}).get("display_name", "Unknown")
        user_email   = (user or {}).get("email", "")
        import asyncio
        asyncio.create_task(send_feedback_notification(display_name, user_email, body.message.strip()))
    except Exception:
        pass
    return {"status": "success", "message": "Thank you for your feedback!"}


router.include_router(_feedback_router)


@router.get("/admin/dashboard-data")
async def admin_dashboard_data(_: dict = Depends(verify_admin_user)):
    return await get_dashboard_data()


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard():
    """F-04: public HTML shell only — contains no user/feedback/deletion
    data server-side. The browser signs in with Firebase, then fetches
    /admin/dashboard-data with an Authorization: Bearer <ID token> header;
    that endpoint (and /admin/feedback) remain protected by
    Depends(verify_admin_user) and are unchanged by this route."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>EpicVerse Admin Dashboard</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#3C1740;color:#E8E0F0;min-height:100vh;padding:24px}
    h1{font-size:22px;font-weight:700;color:#C084FC;margin-bottom:4px}
    .subtitle{font-size:13px;color:#6B4FA0;margin-bottom:28px}
    .stats{display:flex;gap:16px;margin-bottom:32px;flex-wrap:wrap}
    .stat{background:#1B0C2D;border:1px solid #3D1E6B;border-radius:12px;padding:20px 28px;min-width:140px}
    .stat-val{font-size:32px;font-weight:700;color:#C084FC}
    .stat-label{font-size:12px;color:#6B4FA0;margin-top:4px;text-transform:uppercase;letter-spacing:.5px}
    .section{margin-bottom:40px}
    .section-title{font-size:14px;font-weight:600;color:#C084FC;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px;display:flex;align-items:center;gap:8px}
    .badge{background:#3D1E6B;color:#C084FC;font-size:11px;padding:2px 8px;border-radius:20px}
    table{width:100%;border-collapse:collapse;background:#1B0C2D;border-radius:12px;overflow:hidden}
    th{background:#2A1245;color:#9B7DC4;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.4px;padding:12px 16px;text-align:left}
    td{padding:12px 16px;font-size:13px;color:#D4C5E8;border-top:1px solid #2A1245;vertical-align:top}
    tr:hover td{background:#1F0E35}
    .empty{text-align:center;color:#4A2D7A;padding:32px;font-size:13px}
    .msg{max-width:360px;word-break:break-word;line-height:1.5}
    .refresh{font-size:12px;color:#4A2D7A;margin-bottom:20px}
    .dot{width:7px;height:7px;border-radius:50%;background:#22C55E;display:inline-block;margin-right:6px;animation:pulse 2s infinite}
    @keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
    .invite{font-family:monospace;font-size:12px;background:#2A1245;padding:2px 8px;border-radius:4px;color:#A78BFA}
    .time{color:#4A2D7A;font-size:12px;white-space:nowrap}
    .login-box{max-width:340px;margin:80px auto;background:#1B0C2D;border:1px solid #3D1E6B;border-radius:12px;padding:32px}
    .login-box h2{font-size:16px;color:#C084FC;margin-bottom:20px;text-align:center}
    .login-box label{display:block;font-size:12px;color:#9B7DC4;margin-bottom:6px;margin-top:14px}
    .login-box input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #3D1E6B;background:#2A1245;color:#E8E0F0;font-size:14px}
    .login-box button{width:100%;margin-top:20px;padding:11px;border:0;border-radius:8px;background:#8B5CF6;color:#fff;font-size:14px;font-weight:600;cursor:pointer}
    .login-box button:hover{background:#7C3AED}
    .login-error{color:#F87171;font-size:12px;margin-top:12px;min-height:14px;text-align:center}
    .denied-box{max-width:400px;margin:120px auto;text-align:center;color:#F87171;font-size:14px}
    .topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
    .signout-btn{background:#2A1245;border:1px solid #3D1E6B;color:#9B7DC4;font-size:12px;padding:6px 14px;border-radius:8px;cursor:pointer}
    .signout-btn:hover{color:#C084FC;border-color:#8B5CF6}
  </style>
</head>
<body>

  <div id="login-view">
    <div class="login-box">
      <h2>EpicVerse Admin Sign-In</h2>
      <form id="login-form">
        <label for="login-email">Email</label>
        <input type="email" id="login-email" autocomplete="username" required/>
        <label for="login-password">Password</label>
        <input type="password" id="login-password" autocomplete="current-password" required/>
        <button type="submit">Sign In</button>
        <div class="login-error" id="login-error"></div>
      </form>
    </div>
  </div>

  <div id="denied-view" style="display:none">
    <div class="denied-box">Access denied.</div>
  </div>

  <div id="dashboard-view" style="display:none">
    <div class="topbar">
      <div>
        <h1>EpicVerse Admin</h1>
        <div class="subtitle">Owner Dashboard &mdash; Kriyora</div>
      </div>
      <button class="signout-btn" id="signout-btn">Sign out</button>
    </div>

    <div class="stats">
      <div class="stat"><div class="stat-val" id="total-users">—</div><div class="stat-label">Total Users</div></div>
      <div class="stat"><div class="stat-val" id="total-feedback">—</div><div class="stat-label">Feedback</div></div>
      <div class="stat"><div class="stat-val" id="total-deletions" style="color:#F87171">—</div><div class="stat-label">Pending Deletion</div></div>
    </div>

    <div class="refresh"><span class="dot"></span>Auto-refreshes every 30 seconds &nbsp;|&nbsp; Last updated: <span id="last-updated">—</span></div>

    <div class="section">
      <div class="section-title">Users <span class="badge" id="users-badge">0</span></div>
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Invite Code</th><th>Joined</th></tr></thead>
        <tbody id="users-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
      </table>
    </div>

    <div class="section">
      <div class="section-title">Feedback <span class="badge" id="feedback-badge">0</span></div>
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Message</th><th>Date</th></tr></thead>
        <tbody id="feedback-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
      </table>
    </div>

    <div class="section">
      <div class="section-title" style="color:#F87171">Pending Deletion <span class="badge" style="background:#4B1C1C;color:#F87171" id="deletions-badge">0</span></div>
      <table>
        <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Requested At</th><th>Purge Date</th></tr></thead>
        <tbody id="deletions-body"><tr><td colspan="5" class="empty">Loading...</td></tr></tbody>
      </table>
    </div>
  </div>

<script type="module">
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.2/firebase-app.js";
import {
  getAuth, signInWithEmailAndPassword, onAuthStateChanged, signOut
} from "https://www.gstatic.com/firebasejs/10.13.2/firebase-auth.js";

// Client-visible Firebase Web config (not a secret) — identifies the
// project to Firebase, same class of value as the existing Android
// google-services.json entries already committed in this repo.
const firebaseConfig = {
  apiKey: "AIzaSyAJTzVljY9FcQfSqT3dxPKTKnYv0N8BFu4",
  authDomain: "perfect-age-491106-p3.firebaseapp.com",
  projectId: "perfect-age-491106-p3",
  storageBucket: "perfect-age-491106-p3.firebasestorage.app",
  messagingSenderId: "721191424605",
  appId: "1:721191424605:web:d8a3fd3072e4ccddf933b4",
  measurementId: "G-ZP3HJ6818T"
};
const firebaseApp = initializeApp(firebaseConfig);
const auth = getAuth(firebaseApp);

const loginView = document.getElementById('login-view');
const deniedView = document.getElementById('denied-view');
const dashboardView = document.getElementById('dashboard-view');
const loginError = document.getElementById('login-error');

function show(view) {
  loginView.style.display = view === 'login' ? 'block' : 'none';
  deniedView.style.display = view === 'denied' ? 'block' : 'none';
  dashboardView.style.display = view === 'dashboard' ? 'block' : 'none';
}

let refreshTimer = null;
function stopRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null; }
}

const fmt = s => s ? new Date(s).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';

// F-08 (dashboard rendering portion): every database/API-controlled value
// (display_name, email, invite_code, feedback message, dates derived from
// them) is set via .textContent below, never innerHTML/template-string
// interpolation — so a stored value can never be interpreted as HTML/JS
// when an authenticated admin views this page. Only the two small,
// structural CSS classes/colors applied here are ever attacker-influenced
// in shape, never the text itself.
function textCell(value, className, color) {
  const cell = document.createElement('td');
  if (className) cell.className = className;
  if (color) cell.style.color = color;
  cell.textContent = value;
  return cell;
}

function inviteCodeCell(code) {
  const cell = document.createElement('td');
  const span = document.createElement('span');
  if (code) {
    span.className = 'invite';
    span.textContent = code;
  } else {
    span.style.color = '#4A2D7A';
    span.textContent = '—';
  }
  cell.appendChild(span);
  return cell;
}

async function load() {
  const user = auth.currentUser;
  if (!user) { stopRefresh(); show('login'); return; }

  let token;
  try {
    token = await user.getIdToken();
  } catch (e) {
    // Never log the raw auth error — it can carry token/session internals.
    stopRefresh();
    show('login');
    return;
  }

  let r;
  try {
    r = await fetch('/api/v1/admin/dashboard-data', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
  } catch (e) {
    // Transient network error — keep the current view, next tick retries.
    return;
  }

  if (r.status === 401) { stopRefresh(); show('login'); return; }
  if (r.status === 403) { stopRefresh(); show('denied'); return; }
  if (!r.ok) { return; }

  show('dashboard');
  if (!refreshTimer) refreshTimer = setInterval(load, 30000);

  let d;
  try {
    d = await r.json();
  } catch (e) {
    return;
  }

  document.getElementById('total-users').textContent = d.total_users;
  document.getElementById('total-feedback').textContent = d.total_feedback;
  document.getElementById('total-deletions').textContent = d.total_deletions || 0;
  document.getElementById('users-badge').textContent = d.total_users;
  document.getElementById('feedback-badge').textContent = d.total_feedback;
  document.getElementById('deletions-badge').textContent = d.total_deletions || 0;
  document.getElementById('last-updated').textContent = fmt(new Date().toISOString());

  // Users table
  const ub = document.getElementById('users-body');
  if (!d.users.length) { ub.innerHTML = '<tr><td colspan="5" class="empty">No users yet</td></tr>'; }
  else {
    ub.innerHTML = '';
    d.users.forEach((u, i) => {
      const row = document.createElement('tr');
      row.appendChild(textCell(String(i + 1), 'time'));
      row.appendChild(textCell(u.display_name || '—'));
      row.appendChild(textCell(u.email || '—'));
      row.appendChild(inviteCodeCell(u.invite_code));
      row.appendChild(textCell(fmt(u.created_at), 'time'));
      ub.appendChild(row);
    });
  }

  // Feedback table
  const fb = document.getElementById('feedback-body');
  if (!d.feedback.length) { fb.innerHTML = '<tr><td colspan="5" class="empty">No feedback yet</td></tr>'; }
  else {
    fb.innerHTML = '';
    d.feedback.forEach((f, i) => {
      const row = document.createElement('tr');
      row.appendChild(textCell(String(i + 1), 'time'));
      row.appendChild(textCell(f.display_name || '—'));
      row.appendChild(textCell(f.email || '—'));
      row.appendChild(textCell(f.message, 'msg'));
      row.appendChild(textCell(fmt(f.created_at), 'time'));
      fb.appendChild(row);
    });
  }

  // Deletions table
  const purgeDate = s => { const dd = new Date(s); dd.setDate(dd.getDate()+30); return fmt(dd.toISOString()); };
  const db = document.getElementById('deletions-body');
  if (!d.deletions || !d.deletions.length) { db.innerHTML = '<tr><td colspan="5" class="empty">No pending deletions</td></tr>'; }
  else {
    db.innerHTML = '';
    d.deletions.forEach((u, i) => {
      const row = document.createElement('tr');
      row.appendChild(textCell(String(i + 1), 'time'));
      row.appendChild(textCell(u.display_name || '—', null, '#F87171'));
      row.appendChild(textCell(u.email || '—', null, '#F87171'));
      row.appendChild(textCell(fmt(u.deletion_requested_at), 'time', '#F87171'));
      row.appendChild(textCell(purgeDate(u.deletion_requested_at), 'time', '#FCA5A5'));
      db.appendChild(row);
    });
  }
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  loginError.textContent = '';
  const email = document.getElementById('login-email').value.trim();
  const password = document.getElementById('login-password').value;
  try {
    await signInWithEmailAndPassword(auth, email, password);
    // onAuthStateChanged below drives the rest.
  } catch (e) {
    // Generic message only — never log or display the raw Firebase
    // auth error object (it can reveal account-enumeration/internal detail).
    loginError.textContent = 'Sign-in failed. Check your email and password and try again.';
  }
});

document.getElementById('signout-btn').addEventListener('click', async () => {
  stopRefresh();
  try {
    await signOut(auth);
  } catch (e) {
    // Ignore — onAuthStateChanged still reflects the real session state.
  }
});

onAuthStateChanged(auth, (user) => {
  if (!user) { stopRefresh(); show('login'); return; }
  load();
});
</script>
</body>
</html>"""
