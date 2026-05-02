from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Form, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from collections import defaultdict
from datetime import datetime, timedelta
import io
import uuid
import json

# Simple in-memory OTP rate limiter: max 3 requests per 10 minutes per identifier
_otp_rate: dict[str, list] = defaultdict(list)

def _otp_allowed(identifier: str) -> bool:
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=10)
    _otp_rate[identifier] = [t for t in _otp_rate[identifier] if t > cutoff]
    if len(_otp_rate[identifier]) >= 3:
        return False
    _otp_rate[identifier].append(now)
    return True

# Local modules
from app.services.speech_to_text import transcribe_audio
from app.services.ai_pipeline import run_ai_pipeline
from app.services.text_to_speech import synthesize_speech
from app.services.storage import upload_to_gcs, log_interaction
from app.services.user_db import (
    save_user, UserRecord, get_user, save_otp, verify_otp,
    validate_invite_code, mark_invite_code_used,
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
async def sync_user(user: UserRecord):
    """Saves user info to the SQL database and marks invite code as used."""
    if not user.get_uid():
        raise HTTPException(status_code=422, detail="firebase_id or uid is required")
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

    if not _otp_allowed(identifier.lower()):
        raise HTTPException(status_code=429, detail="Too many OTP requests. Please wait 10 minutes.")

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
    """Deletes user from both Firebase and SQL database.

    Authorization: requires a valid Firebase ID token, and the caller's uid
    must match `firebase_id`. Users can only delete their own account.
    """
    caller_uid = current_user.get("uid")
    if caller_uid != firebase_id:
        print(f"[DELETE] Forbidden — caller={caller_uid!r} target={firebase_id!r}", flush=True)
        raise HTTPException(status_code=403, detail="You can only delete your own account.")

    print(f"[DELETE] Deleting user {firebase_id}", flush=True)

    # 1. Delete from Firebase
    try:
        from firebase_admin import auth
        auth.delete_user(firebase_id)
    except Exception as e:
        print(f"Firebase Deletion Error (Likely doesn't exist): {e}")
        # Continue to SQL deletion even if Firebase fails

    # 2. Delete from SQL database
    from app.services.user_db import delete_user_from_db
    success = await delete_user_from_db(firebase_id)

    if not success:
        raise HTTPException(status_code=500, detail="Failed to delete user from internal database.")

    return {"status": "success", "message": "User deleted successfully."}
