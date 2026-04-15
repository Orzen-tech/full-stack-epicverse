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

# Global tracker for active WebSocket sessions to enforce Single Device Policy
active_sessions: dict[str, WebSocket] = {}

@router.websocket("/ws/realtime")
async def websocket_realtime(websocket: WebSocket):
    """
    ULTRA-FAST Realtime API endpoint with Database Truth injection.
    Enforces Single Device Policy by disconnecting previous sessions.
    """
    gameMode = websocket.query_params.get("gameMode", "Mode 1")
    idToken = websocket.query_params.get("token")
    
    await websocket.accept()
    
    # 1. Verify Identity
    user_uid = "guest"
    try:
        if idToken == "epic-stress-test-token":
            user_uid = f"simulated_user_{uuid.uuid4().hex[:6]}"
            print(f"[WS-RT] STRESS TEST BYPASS: User {user_uid}")
        elif idToken:
            decoded_token = await asyncio.to_thread(auth.verify_id_token, idToken)
            user_uid = decoded_token.get("uid")
            print(f"[WS-RT] AUTH SUCCESS: User {user_uid}")
    except Exception as e:
        print(f"[WS-RT] AUTH FAILED: {str(e)}")
        await websocket.send_text(json.dumps({"status": "Error", "message": "Auth failed"}))
        await websocket.close(code=4001)
        return

    # 2. Single Device Policy (Kill-switch)
    if user_uid != "guest":
        if user_uid in active_sessions:
            print(f"[WS-RT] Conflict: Logging out previous session for {user_uid}")
            old_socket = active_sessions[user_uid]
            try:
                # Custom message to trigger frontend logout
                await old_socket.send_text(json.dumps({
                    "type": "concurrent_logout",
                    "message": "Your profile is now active on another device."
                }))
                await old_socket.close(code=4002) 
            except:
                pass 
        
        active_sessions[user_uid] = websocket

    # 3. Launch Realtime Relay
    print(f"[WS-RT] Launching Realtime Bridge for {user_uid} in {gameMode}")
    service = RealtimeService(websocket, game_mode=gameMode, user_uid=user_uid)
    
    try:
        await service.run()
    finally:
        # Cleanup session tracker on disconnect
        if user_uid in active_sessions and active_sessions[user_uid] == websocket:
            del active_sessions[user_uid]
            print(f"[WS-RT] Cleaned up session for {user_uid}")
