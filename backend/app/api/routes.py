from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Form, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
from collections import defaultdict
from datetime import datetime, timedelta
import io
import uuid
import json

# Simple in-memory OTP rate limiter: max 3 requests per 10 minutes per identifier
_otp_rate: dict[str, list] = defaultdict(list)

def _otp_allowed(identifier: str) -> tuple[bool, int]:
    """Returns (allowed: bool, retry_after_seconds: int)."""
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=10)
    _otp_rate[identifier] = [t for t in _otp_rate[identifier] if t > cutoff]
    if len(_otp_rate[identifier]) >= 3:
        # Calculate remaining cooldown until oldest entry expires
        oldest = min(_otp_rate[identifier])
        retry_after = int((oldest + timedelta(minutes=10) - now).total_seconds())
        return False, retry_after
    _otp_rate[identifier].append(now)
    return True, 0

# Local modules
from app.services.speech_to_text import transcribe_audio
from app.services.ai_pipeline import run_ai_pipeline
from app.services.text_to_speech import synthesize_speech
from app.services.storage import upload_to_gcs, log_interaction
from app.services.user_db import (
    save_user, UserRecord, get_user, save_otp, verify_otp,
    validate_invite_code, mark_invite_code_used,
    request_user_deletion, cancel_user_deletion, purge_expired_deletions,
    save_feedback, get_all_feedback, get_dashboard_data, mark_email_verified,
    verify_session, update_session_id,
)
from app.api.dependencies import get_current_user

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
async def sync_user(user: UserRecord, current_user: dict = Depends(get_current_user)):
    """Saves user info to the SQL database and marks invite code as used."""
    if not user.get_uid():
        raise HTTPException(status_code=422, detail="firebase_id or uid is required")
    if current_user.get("uid") != user.get_uid():
        raise HTTPException(status_code=403, detail="Forbidden")
    await save_user(user)
    if user.invite_code:
        await mark_invite_code_used(user.invite_code.upper(), user.get_uid())
    return {"status": "success", "message": "User synchronized"}

async def _authorize_otp_request(invite_code: str | None, authorization: str | None) -> None:
    """Authorizes an OTP request via either a valid invite code (signup flow)
    or a valid Firebase ID token (login/resend flow). Raises 403 otherwise.
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
                fb_auth.verify_id_token(token)
                return
            except Exception as e:
                print(f"[OTP] Bearer token rejected: {e}", flush=True)

    raise HTTPException(
        status_code=403,
        detail="OTP requires a valid invite code or authenticated session.",
    )


@router.post("/auth/send-otp")
async def send_otp(
    identifier: str = Form(None),
    email: str = Form(None),
    invite_code: str = Form(None),
    authorization: str | None = Header(default=None),
):
    """Generates a 6-digit OTP, saves it to DB, and sends via SendGrid.

    Authorization: caller must supply either a valid `invite_code` (signup)
    or a valid Firebase ID token in the Authorization header (login/resend).
    """
    identifier = identifier or email
    if not identifier:
        raise HTTPException(status_code=422, detail="identifier or email is required")

    await _authorize_otp_request(invite_code, authorization)

    allowed, retry_after = _otp_allowed(identifier.lower())
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many OTP requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)}
        )

    import random
    from app.services.email_service import send_otp_email

    otp = str(random.randint(100000, 999999))
    db_success = await save_otp(identifier, otp)
    if not db_success:
        raise HTTPException(status_code=500, detail="Database error while saving OTP")

    if "@" in identifier:
        email_sent = await send_otp_email(identifier, otp)
        if not email_sent:
            raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    else:
        print(f"[OTP-SMS-LOG] OTP for {identifier}: {otp}")

    return {"status": "success", "message": "OTP sent successfully"}


@router.post("/auth/verify-otp")
async def verify_otp_route(identifier: str = Form(None), email: str = Form(None), otp: str = Form(...)):
    """Verifies an OTP for an existing Firebase user.

    NOTE: This endpoint never creates a Firebase user. The signup flow must
    create the Firebase user first (via `createUserWithEmailAndPassword` after
    invite validation). Refusing to auto-create here is the invite-bypass fix.
    """
    identifier = identifier or email
    if not identifier:
        raise HTTPException(status_code=422, detail="identifier or email is required")

    is_valid = await verify_otp(identifier, otp)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")

    # Mark email as verified in the users table
    if "@" in identifier:
        await mark_email_verified(identifier)

    uid = None
    custom_token = None

    if "@" in identifier:
        try:
            from firebase_admin import auth as fb_auth
            try:
                fb_user = fb_auth.get_user_by_email(identifier)
            except fb_auth.UserNotFoundError:
                # No account for this email — refuse to onboard via OTP alone.
                # Legitimate signups go through invite validation + createUserWithEmailAndPassword.
                print(f"[AUTH] OTP verify rejected — no Firebase user for {identifier}", flush=True)
                raise HTTPException(
                    status_code=404,
                    detail="Account not found. Please sign up with a valid invite code.",
                )
            uid = fb_user.uid

            # Upsert a minimal DB record so the user exists before profile completion
            await save_user(UserRecord(firebase_id=uid, email=identifier))

            # Custom token lets Flutter sign in via FirebaseAuth.signInWithCustomToken()
            token_bytes = fb_auth.create_custom_token(uid)
            custom_token = token_bytes.decode() if isinstance(token_bytes, bytes) else token_bytes
            print(f"[AUTH] OTP verified — Firebase uid={uid} email={identifier}", flush=True)
        except HTTPException:
            raise
        except Exception as e:
            print(f"[AUTH] Firebase user lookup error: {e}", flush=True)
            # OTP was valid; don't block the user, but token will be None

    return {
        "status": "success",
        "message": "OTP verified",
        "uid": uid,
        "custom_token": custom_token,
    }


@router.post("/auth/send-password-reset")
async def send_password_reset(identifier: str = Form(None), email: str = Form(None)):
    """Generates a Firebase password reset link and sends it via SendGrid.
    Uses SendGrid for reliable inbox delivery instead of Firebase's default sender.
    """
    from app.services.email_service import send_password_reset_email

    target = (identifier or email or "").strip()
    if not target or "@" not in target:
        raise HTTPException(status_code=422, detail="Valid email required")

    try:
        from firebase_admin import auth as fb_auth
        reset_link = fb_auth.generate_password_reset_link(target)
    except fb_auth.UserNotFoundError:
        # Return 200 to avoid leaking whether the email is registered
        print(f"[AUTH] Password reset requested for unknown email: {target}", flush=True)
        return {"status": "sent"}
    except Exception as e:
        print(f"[AUTH] generate_password_reset_link error: {e}", flush=True)
        raise HTTPException(status_code=500, detail="Failed to generate reset link")

    sent = await send_password_reset_email(target, reset_link)
    if not sent:
        raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    return {"status": "sent"}


@router.post("/auth/send-email-otp")
async def send_email_otp_preregistration(
    identifier: str = Form(None),
    email: str = Form(None),
):
    """Open endpoint for pre-registration email verify. No invite code or token needed.
    Rate-limited by the existing _otp_allowed limiter (3 per 10 min per email).
    """
    import random
    from app.services.email_service import send_otp_email

    target = (identifier or email or "").strip()
    if not target or "@" not in target:
        raise HTTPException(status_code=422, detail="Valid email required")

    allowed, retry_after = _otp_allowed(target.lower())
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please wait {retry_after // 60} minutes.",
            headers={"Retry-After": str(retry_after)},
        )

    otp = str(random.randint(100000, 999999))
    await save_otp(target.lower(), otp)
    sent = await send_otp_email(target, otp)
    if not sent:
        raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    return {"status": "sent"}


@router.post("/auth/mark-verified")
async def mark_email_verified_route(current_user: dict = Depends(get_current_user)):
    """Sets email_verified=TRUE for the authenticated user. Called after pre-registration
    email OTP is confirmed and Firebase account + sync-user have completed."""
    email = current_user.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="No email on token")
    await mark_email_verified(email)
    return {"status": "ok"}


@router.get("/auth/check-session")
async def check_session(session_id: str, current_user: dict = Depends(get_current_user)):
    """Returns whether the given session_id is still the active session for this user.
    If another device has logged in since, the stored session_id will differ."""
    uid = current_user.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="Unauthorized")
    valid = await verify_session(uid, session_id)
    return {"valid": valid}


@router.get("/user/{firebase_id}")
async def fetch_user(firebase_id: str):
    """Fetches user info from the SQL database."""
    user = await get_user(firebase_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

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
        print(f"[WS] Auth rejected — missing uid or token (uid={uid!r} source={token_source})", flush=True)
        await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
        await websocket.close(code=1008)
        return

    try:
        from firebase_admin import auth as fb_auth
        decoded = fb_auth.verify_id_token(resolved_token)
        if decoded.get("uid") != uid:
            print(f"[WS] Auth rejected — uid mismatch (claim={decoded.get('uid')!r}, query={uid!r})", flush=True)
            await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
            await websocket.close(code=1008)
            return
        if token_source == "query(legacy)":
            # Visibility for rollout: tells you when the last legacy client upgrades.
            print(f"[WS] LEGACY token in query string uid={uid} — client should upgrade to header auth", flush=True)
    except Exception as e:
        print(f"[WS] Auth failed uid={uid} source={token_source}: {e}", flush=True)
        await websocket.send_text(json.dumps({"type": "error", "message": "Unauthorized"}))
        await websocket.close(code=1008)
        return

    print(f"[WS] Realtime connection uid={uid} mode={mode} session={session_id}", flush=True)
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
    current_user: dict = Depends(get_current_user)
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
        ai_result = await run_ai_pipeline(recognized_text, game_mode=game_mode, session_id=current_user.get("uid", session_id))
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
    current_user: dict = Depends(get_current_user),
):
    """Soft-deletes a user account with a 30-day grace period.

    The account is marked `deletion_requested_at = NOW()` but remains in
    both Firebase and the SQL database. If the user signs back in within
    30 days, `get_user()` auto-cancels the deletion. After 30 days the
    scheduled `/admin/purge-expired-deletions` job permanently removes the
    account from both Firebase and the database.

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
    """Explicitly cancels a pending account deletion (belt-and-braces UI
    path). Normal sign-in already auto-cancels via `get_user()`; this
    endpoint exists for screens that want to restore without a full GET.
    """
    caller_uid = current_user.get("uid")
    if caller_uid != firebase_id:
        raise HTTPException(status_code=403, detail="You can only cancel deletion of your own account.")
    ok = await cancel_user_deletion(firebase_id)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to cancel deletion.")
    return {"status": "success", "message": "Pending deletion cancelled."}


@router.get("/admin/feedback")
async def admin_get_feedback(key: str = ""):
    if key != "kriyora-admin-2026":
        raise HTTPException(status_code=403, detail="Invalid admin key")
    rows = await get_all_feedback()
    return {"total": len(rows), "feedback": rows}


@router.post("/admin/purge-expired-deletions")
async def purge_expired_deletions_route():
    """Hard-deletes all accounts whose 30-day grace period has elapsed.

    Intended to be called by a scheduled job (e.g. Cloud Scheduler hitting
    this endpoint daily). Removes purged uids from Firebase Auth in
    addition to the SQL database.
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


class FeedbackRequest(BaseModel):
    message: str


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
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


@router.get("/admin/dashboard-data")
async def admin_dashboard_data(key: str = ""):
    if key != "kriyora-admin-2026":
        raise HTTPException(status_code=403, detail="Invalid admin key")
    return await get_dashboard_data()


@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(key: str = ""):
    if key != "kriyora-admin-2026":
        raise HTTPException(status_code=403, detail="Invalid admin key")
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
  </style>
</head>
<body>
  <h1>EpicVerse Admin</h1>
  <div class="subtitle">Owner Dashboard &mdash; Kriyora</div>

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

<script>
const KEY = new URLSearchParams(location.search).get('key') || '';
const fmt = s => s ? new Date(s).toLocaleString('en-IN',{timeZone:'Asia/Kolkata',day:'2-digit',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'}) : '—';

async function load() {
  try {
    const r = await fetch(`/api/v1/admin/dashboard-data?key=${KEY}`);
    if (!r.ok) { document.body.innerHTML = '<p style="color:#F87171;padding:40px">Access denied.</p>'; return; }
    const d = await r.json();

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
    else ub.innerHTML = d.users.map((u,i) => `<tr>
      <td class="time">${i+1}</td>
      <td>${u.display_name || '—'}</td>
      <td>${u.email || '—'}</td>
      <td>${u.invite_code ? '<span class="invite">'+u.invite_code+'</span>' : '<span style="color:#4A2D7A">—</span>'}</td>
      <td class="time">${fmt(u.created_at)}</td>
    </tr>`).join('');

    // Feedback table
    const fb = document.getElementById('feedback-body');
    if (!d.feedback.length) { fb.innerHTML = '<tr><td colspan="5" class="empty">No feedback yet</td></tr>'; }
    else fb.innerHTML = d.feedback.map((f,i) => `<tr>
      <td class="time">${i+1}</td>
      <td>${f.display_name || '—'}</td>
      <td>${f.email || '—'}</td>
      <td class="msg">${f.message}</td>
      <td class="time">${fmt(f.created_at)}</td>
    </tr>`).join('');

    // Deletions table
    const purgeDate = s => { const d = new Date(s); d.setDate(d.getDate()+30); return fmt(d.toISOString()); };
    const db = document.getElementById('deletions-body');
    if (!d.deletions || !d.deletions.length) { db.innerHTML = '<tr><td colspan="5" class="empty">No pending deletions</td></tr>'; }
    else db.innerHTML = d.deletions.map((u,i) => `<tr>
      <td class="time">${i+1}</td>
      <td style="color:#F87171">${u.display_name || '—'}</td>
      <td style="color:#F87171">${u.email || '—'}</td>
      <td class="time" style="color:#F87171">${fmt(u.deletion_requested_at)}</td>
      <td class="time" style="color:#FCA5A5">${purgeDate(u.deletion_requested_at)}</td>
    </tr>`).join('');
  } catch(e) { console.error(e); }
}

load();
setInterval(load, 30000);
</script>
</body>
</html>"""
