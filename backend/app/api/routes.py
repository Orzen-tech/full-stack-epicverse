import json
import uuid
import io
import asyncio
from fastapi import APIRouter, File, UploadFile, HTTPException, Depends, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from app.core.config import settings

# Local modules
from app.services.storage import upload_to_gcs, log_interaction
from app.services.user_db import save_user, UserRecord, get_user
from app.api.dependencies import get_current_user

router = APIRouter()

@router.get("/validate-invite/{code}")
async def validate_invite(code: str):
    """Checks if an invite code is valid."""
    from app.services.user_db import validate_invite_code
    result = await validate_invite_code(code)
    if not result["valid"]:
        raise HTTPException(status_code=403, detail=result["message"])
    return result

@router.post("/auth/send-otp")
async def send_otp(email: str = Form(...)):
    """Generates a 6-digit OTP, saves it to DB, and sends via SendGrid."""
    import random
    from app.services.user_db import save_otp
    from app.services.email_service import send_otp_email
    
    otp = str(random.randint(100000, 999999))
    
    db_success = await save_otp(email, otp)
    if not db_success:
        raise HTTPException(status_code=500, detail="Database error while saving OTP")
        
    email_success = await send_otp_email(email, otp)
    if not email_success:
        # We still return success if the DB worked but email failed (for dev testing)
        # but in production, you might want to raise an error
        if not settings.SENDGRID_API_KEY:
             return {"status": "partial", "message": f"OTP saved: {otp} (email skipped: no API key)", "otp": otp}
        raise HTTPException(status_code=500, detail="Failed to send OTP email")
        
    return {"status": "success", "message": "OTP sent successfully"}

@router.post("/auth/verify-otp")
async def verify_otp_route(email: str = Form(...), otp: str = Form(...)):
        
    # 2. Database Verification
    from app.services.user_db import verify_otp
    is_valid = await verify_otp(email, otp)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    return {"status": "success", "message": "OTP verified"}


@router.post("/sync-user")
async def sync_user(user: UserRecord):
    """Saves user info to the SQL database after verifying invite code."""
    print(f"[SYNC] Received data for {user.uid}: Name={user.display_name}, Photo={'set' if user.profile_picture else 'None'}")
    from app.services.user_db import validate_invite_code, consume_invite_code, save_user, get_user
    
    # Check if user already exists in our database
    existing_user = await get_user(user.uid)
    
    if not existing_user:
        # 1. Verification Step (Required ONLY for new users)
        if not user.invite_code:
            raise HTTPException(status_code=400, detail="Invite code is required for registration")
        
        is_valid = await validate_invite_code(user.invite_code)
        if not is_valid:
            raise HTTPException(status_code=400, detail="Invalid or expired invite code")
        
        # 2. Setup New User
        await save_user(user)
        await consume_invite_code(user.invite_code)
        return {"status": "success", "message": "New user registered and invite consumed"}
    else:
        # Existing user: Merge fields to avoid overwriting with None
        # Priority: Incoming Value > Existing Value
        merged_display_name = user.display_name if user.display_name else existing_user.get('display_name')
        merged_profile_picture = user.profile_picture if user.profile_picture else existing_user.get('profile_picture')
        merged_email = user.email if user.email else existing_user.get('email')
        merged_primary_language = user.primary_language if user.primary_language else existing_user.get('primary_language')
        
        # Create a clean record for saving
        from copy import copy
        updated_user = copy(user)
        updated_user.display_name = merged_display_name
        updated_user.profile_picture = merged_profile_picture
        updated_user.email = merged_email
        updated_user.primary_language = merged_primary_language
        
        await save_user(updated_user)
        return {"status": "success", "message": "User profile updated and merged"}

@router.get("/user/{firebase_id}")
async def fetch_user(firebase_id: str):
    """Fetches user info from the SQL database."""
    user = await get_user(firebase_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    print(f"[FETCH] Returning user {firebase_id}: Name={user.get('display_name')}, Photo={'YES' if user.get('profile_picture') else 'NO'}")
    return user

import firebase_admin.auth as auth
from app.services.realtime_service import RealtimeService

# Centralized connection tracking for Single Device Login policy
active_sessions = {}  # {uid: {"session_id": str, "ws": WebSocket}}

@router.websocket("/ws/realtime")
async def websocket_realtime(websocket: WebSocket):
    """
    ULTRA-FAST Realtime API endpoint with Single Device Session enforcement.
    """
    from app.services.user_db import verify_session
    
    await websocket.accept()
    
    # 1. Identify User and Session
    user_uid = websocket.query_params.get("uid", "guest")
    game_mode = websocket.query_params.get("mode", "Mode 1")
    current_session_id = websocket.query_params.get("session_id", "unknown")
    
    # 2. Single Device Enforcement (The "Kick" Logic)
    if user_uid != "guest":
        # Check against Database (Hard Truth)
        is_valid = await verify_session(user_uid, current_session_id)
        if not is_valid:
            await websocket.send_text(json.dumps({
                "type": "error",
                "code": "SESSION_INVALID",
                "message": "Logged in on another device."
            }))
            await websocket.close(code=1008)
            return

        # Handle Active Connections (Memory Path)
        if user_uid in active_sessions:
            old_session = active_sessions[user_uid]
            if old_session["session_id"] != current_session_id:
                try:
                    print(f"[AUTH] Kicking old session for {user_uid}")
                    await old_session["ws"].send_text(json.dumps({
                        "type": "error",
                        "code": "SESSION_KICKED",
                        "message": "You have been logged in on another device."
                    }))
                    await old_session["ws"].close(code=1008)
                except:
                    pass
        
        # Register current session
        active_sessions[user_uid] = {"session_id": current_session_id, "ws": websocket}

    # 3. Initialize and Run Service
    service = RealtimeService(websocket, game_mode, user_uid)
    await service.run()
