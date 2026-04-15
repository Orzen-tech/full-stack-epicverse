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

@router.post("/sync-user")
async def sync_user(user: UserRecord):
    """Saves user info to the SQL database after verifying invite code."""
    from app.services.user_db import validate_invite_code, consume_invite_code, save_user, get_user
    
    # Check if user already exists in our database
    existing_user = await get_user(user.uid)
    
    if not existing_user:
        # 1. Verification Step (Required ONLY for new users)
        if not user.invite_code:
            raise HTTPException(status_code=400, detail="Invite code is required for registration")
        
        validation = await validate_invite_code(user.invite_code)
        if not validation["valid"]:
            raise HTTPException(status_code=403, detail=validation["message"])
            
        # 2. Save User
        await save_user(user)
        
        # 3. Consume Code (Burn it)
        await consume_invite_code(user.invite_code)
        return {"status": "success", "message": "New user registered and invite consumed"}
    else:
        # Existing user: Just update their profile info
        await save_user(user)
        return {"status": "success", "message": "User profile updated"}

@router.get("/user/{firebase_id}")
async def fetch_user(firebase_id: str):
    """Fetches user info from the SQL database."""
    user = await get_user(firebase_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
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

    # 3. Initialize Service
    service = RealtimeService(websocket, game_mode, user_uid)
    try:
            user_uid = decoded_token.get("uid")
            print(f"[WS-RT] AUTH SUCCESS: User {user_uid}")
    except Exception as e:
        print(f"[WS-RT] AUTH FAILED: {str(e)}")
        await websocket.send_text(json.dumps({"status": "Error", "message": "Auth failed"}))
        await websocket.close(code=4001)
        return

    # 2. Launch Realtime Relay
    print(f"[WS-RT] Launching Realtime Bridge for {user_uid} in {gameMode}")
    service = RealtimeService(websocket, game_mode=gameMode, user_uid=user_uid)
    await service.run()
