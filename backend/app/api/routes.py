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
async def send_otp(identifier: str = Form(None), email: str = Form(None)):
    """Generates a 6-digit OTP, saves it to DB, and sends via Email (or logs if Phone)."""
    identifier = identifier or email
    if not identifier:
        raise HTTPException(status_code=422, detail="identifier or email is required")
    
    import random
    from app.services.user_db import save_otp
    from app.services.email_service import send_otp_email
    from app.services.memory_store import session_store
    
    # identifier could be email or phone
    is_email = "@" in identifier

    # 1. Rate Limit Check
    limit_check = await session_store.check_otp_rate_limit(identifier)
    if not limit_check["allowed"]:
        raise HTTPException(
            status_code=429, 
            detail=f"Too many attempts. Cooldown: {limit_check['mins']} mins remaining."
        )

    otp = str(random.randint(100000, 999999))
    
    db_success = await save_otp(identifier, otp)
    if not db_success:
        raise HTTPException(status_code=500, detail="Database error while saving OTP")
        
    if is_email:
        email_success = await send_otp_email(identifier, otp)
        if not email_success:
            print(f"⚠️ [OTP-FAILOVER] Email failed for {identifier}")
            raise HTTPException(status_code=503, detail="Email delivery failed. Please try again.")
    else:
        # Placeholder for SMS Service (e.g. Twilio/Firebase)
        print(f"📱 [OTP-SMS-LOG] OTP queued for {identifier}")
        return {"status": "success", "message": "OTP sent via SMS"}
        
    return {"status": "success", "message": "OTP sent successfully"}


@router.post("/auth/verify-otp")
async def verify_otp_route(identifier: str = Form(None), email: str = Form(None), otp: str = Form(...)):
    identifier = identifier or email
    if not identifier:
        raise HTTPException(status_code=422, detail="identifier or email is required")
    
    from app.services.user_db import verify_otp
    is_valid = await verify_otp(identifier, otp)
    if not is_valid:
        raise HTTPException(status_code=400, detail="Invalid or expired OTP")
    return {"status": "success", "message": "OTP verified"}


@router.post("/sync-user")
async def sync_user(user: UserRecord):
    """Saves user info to the SQL database after verifying invite code."""
    print(f"[SYNC] Received data for {user.uid}: Name={user.display_name}, Phone={user.phone_number}")
    from app.services.user_db import validate_invite_code, consume_invite_code, save_user, get_user
    
    # Check if user already exists
    existing_user = await get_user(user.uid)
    
    if not existing_user:
        # New Registration
        if not user.invite_code:
            raise HTTPException(status_code=400, detail="Invite code is required for registration")
        
        val_res = await validate_invite_code(user.invite_code)
        if not val_res["valid"]:
            raise HTTPException(status_code=400, detail=val_res["message"])
        
        await save_user(user)
        await consume_invite_code(user.invite_code)
        return {"status": "success", "message": "New user registered"}
    else:
        # Merge Existing User
        from copy import copy
        updated_user = copy(user)
        updated_user.display_name = user.display_name or existing_user.get('display_name')
        updated_user.profile_picture = user.profile_picture or existing_user.get('profile_picture')
        updated_user.email = user.email or existing_user.get('email')
        updated_user.phone_number = user.phone_number or existing_user.get('phone_number')
        updated_user.primary_language = user.primary_language or existing_user.get('primary_language')
        
        await save_user(updated_user)
        return {"status": "success", "message": "User profile updated"}

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
            print(f"!!! [AUTH REJECTED] UID: {user_uid} failed session check.")
            print(f"!!! Expected: Current DB Session | Got: {current_session_id}")
            await websocket.send_text(json.dumps({
                "type": "error",
                "code": "SESSION_INVALID",
                "message": "Logged in on another device or invalid session ID."
            }))
            await websocket.close(code=1008)
            return
        
        print(f"[AUTH] Handshake passed for {user_uid} (Session: {current_session_id})")


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
    service = RealtimeService(websocket, game_mode, user_uid, current_session_id)
    try:
        await service.run()
    finally:
        # Clean up stale session tracking entry so reconnects aren't blocked
        if user_uid in active_sessions and active_sessions[user_uid].get("session_id") == current_session_id:
            del active_sessions[user_uid]

@router.get("/db-health")
async def db_health():
    """Detailed health check for the PostgreSQL database."""
    from app.services.retriever import init_db_pool, db_pool, _db_pool_failed
    import asyncpg
    
    status = {
        "pool_initialized": db_pool is not None,
        "pool_failed_previously": _db_pool_failed,
        "connection_test": "pending"
    }
    
    try:
        pool = await init_db_pool()
        if not pool:
            status["connection_test"] = "failed - could not create pool"
            return status
            
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
            status["connection_test"] = "success"
            
            # Check table counts
            users_count = await conn.fetchval("SELECT count(*) FROM users")
            combos_count = await conn.fetchval("SELECT count(*) FROM card_combos")
            status["tables"] = {
                "users": users_count,
                "card_combos": combos_count
            }
    except Exception as e:
        status["connection_test"] = f"failed - {str(e)}"
    
    return status
