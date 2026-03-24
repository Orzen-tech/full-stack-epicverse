from fastapi import APIRouter, File, UploadFile, Depends, HTTPException, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import io
import uuid
import json

# Local modules
from app.services.speech_to_text import transcribe_audio
from app.services.ai_pipeline import run_ai_pipeline
from app.services.text_to_speech import synthesize_speech
from app.services.storage import upload_to_gcs, log_interaction
from app.services.user_db import save_user, UserRecord, get_user
from app.api.dependencies import get_current_user
from app.services.wake_word import WakeWordDetector

router = APIRouter()

@router.post("/sync-user")
async def sync_user(user: UserRecord):
    """Saves user info to the SQL database."""
    await save_user(user)
    return {"status": "success", "message": "User synchronized"}

@router.get("/user/{firebase_id}")
async def fetch_user(firebase_id: str):
    """Fetches user info from the SQL database."""
    user = await get_user(firebase_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user

import firebase_admin.auth as auth

@router.websocket("/ws-audio")
async def websocket_audio(websocket: WebSocket):
    """Live streaming endpoint with strict Firebase UID verification."""
    gameMode = websocket.query_params.get("gameMode", "Mode 1")
    sessionId = websocket.query_params.get("sessionId", "default")
    idToken = websocket.query_params.get("token")
    initial_listening = websocket.query_params.get("listening", "false").lower() == "true"
    
    await websocket.accept()
    
    # 1. Verify Identity (No-Trust Policy)
    user_uid = None
    try:
        if not idToken:
             raise Exception("No authentication token provided.")
        
        decoded_token = auth.verify_id_token(idToken)
        user_uid = decoded_token.get("uid")
        print(f"[WS] AUTH SUCCESS: User {user_uid} connected.")
        
    except Exception as e:
        print(f"[WS] AUTH FAILED: {str(e)}")
        await websocket.send_text(json.dumps({
            "status": "Error", 
            "type": "auth_failed",
            "message": "Authentication failed. Please log in again."
        }))
        await websocket.close(code=4001)
        return

    print(f"\n[WS] New Connection - Mode: {gameMode}, Session: {sessionId}, Profile UID: {user_uid}", flush=True)
    
    # Send an initial message to let the client know we are ready
    await websocket.send_text(json.dumps({"status": "Connected", "message": "Established connection to EpicVerse AI."}))
    audio_bytes = bytearray()
    listening_for_wakeword = initial_listening
    ww_detector = WakeWordDetector()
    
    chunks_received = 0
    try:
        while True:
            # We can receive either binary audio chunks or JSON text commands
            message = await websocket.receive()
            
            if message.get("type") == "websocket.disconnect":
                break
                
            if "bytes" in message:
                chunk = message["bytes"]
                chunks_received += 1
                
                if listening_for_wakeword:
                    # Instant detection on the stream!
                    if ww_detector.detect(chunk):
                        print(f"[WS] Wake Word Detected!", flush=True)
                        await websocket.send_text(json.dumps({"type": "wakeword_detected"}))
                        listening_for_wakeword = False # Reset after detection
                        chunks_received = 0
                else:
                    audio_bytes.extend(chunk)
                    # Occasional log for active turns
                    if chunks_received % 50 == 0:
                        print(f"[WS] Receiving Voice Data... ({len(audio_bytes)} bytes accumulated)", flush=True)

            elif "text" in message:
                data = json.loads(message["text"])
                command_type = data.get("type")
                
                if command_type == "start_wakeword":
                    listening_for_wakeword = True
                    audio_bytes.clear()
                    chunks_received = 0
                    print(f"[WS] COMMAND: Start Wake Word Listening", flush=True)
                    
                elif command_type == "stop_wakeword":
                    listening_for_wakeword = False
                    print(f"[WS] COMMAND: Stop Wake Word Listening", flush=True)

                elif command_type == "end":
                    print(f"[WS] Processing turn - Audio buffer size: {len(audio_bytes)} bytes", flush=True)
                    if len(audio_bytes) < 500: # Less than ~15ms of audio
                        print(f"[WS] Audio too short, skipping.", flush=True)
                        audio_bytes.clear()
                        continue

                    stt_result = await transcribe_audio(bytes(audio_bytes))
                    recognized_text = stt_result.get("text")
                    user_lang = stt_result.get("language")
                    
                    # Clear early to prevent issues if processing fails later
                    audio_bytes.clear()

                    if not recognized_text or len(recognized_text.strip()) == 0:
                        print(f"[WS] STT Failed - No text recognized", flush=True)
                        await websocket.send_text(json.dumps({"status": "Error", "message": "Could not recognize speech. Please try again."}))
                        continue
                    
                    print(f"[WS] Recognized: \"{recognized_text}\" (Lang: {user_lang})", flush=True)
                        
                    # Run the newly optimized 1-shot AI pipeline with Session ID and Verified UID and Language Sync
                    ai_result = await run_ai_pipeline(recognized_text, game_mode=gameMode, session_id=sessionId, uid=user_uid, user_lang=user_lang)
                    final_text = ai_result.get("final_response")
                    
                    await websocket.send_text(json.dumps({"aiResponse": final_text}))
                    
                    # Generate TTS using the AI-verified language (Accurate detection)
                    real_lang = ai_result.get("detected_lang", user_lang)
                    output_audio_bytes = await synthesize_speech(final_text, language_code=real_lang)
                    
                    # Feed binary audio bytes cleanly right back down the websocket!
                    await websocket.send_bytes(output_audio_bytes)
                    
                    if ai_result.get("action") == "change_mode":
                        # Tell frontend to switch modes
                        await websocket.send_text(json.dumps({
                            "type": "mode_change", 
                            "newMode": ai_result.get("newMode")
                        }))
                    
                    # AUTO-RESUME Wake Word detection for the next turn!
                    listening_for_wakeword = True
                    audio_bytes.clear()
                    chunks_received = 0
                    print(f"[WS] Turn complete. Automatically resumed Wake Word detection.", flush=True)


                elif data.get("type") == "text_query":
                    query_text = data.get("text")
                    print(f"[WS] Querying AI: {query_text}", flush=True)
                    await websocket.send_text(json.dumps({"status": "Processing", "message": "Thinking..."}))
                    
                    # Use the same AI pipeline with UID verification
                    ai_result = await run_ai_pipeline(query_text, game_mode=gameMode, session_id=sessionId, uid=user_uid)
                    final_text = ai_result.get("final_response")
                    
                    await websocket.send_text(json.dumps({"aiResponse": final_text}))
                    
                    # Generate TTS for text queries with auto-detection
                    real_lang = ai_result.get("detected_lang", "en")
                    output_audio_bytes = await synthesize_speech(final_text, language_code=real_lang)
                    await websocket.send_bytes(output_audio_bytes)
                    
                    if ai_result.get("action") == "change_mode":
                        await websocket.send_text(json.dumps({
                            "type": "mode_change",
                            "newMode": ai_result.get("newMode")
                        }))
    except WebSocketDisconnect:
        pass
        pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            await websocket.send_text(json.dumps({"status": "Error", "message": str(e)}))
        except:
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
            
        # Use the AI-detected language for TTS and logging
        real_lang = ai_result.get("detected_lang", user_lang)
        
        # Step 6: Text-To-Speech Synthesis
        output_audio_bytes = await synthesize_speech(final_text, language_code=real_lang)
        
        # Step 8 & 9: Data Logging and monitoring
        await log_interaction(
            session_id=session_id,
            user_id=current_user.get("uid"),
            query=recognized_text,
            response=final_text,
            user_language=real_lang
        )
        
        import urllib.parse
        # Step 7: Stream audio back to Flutter mobile app
        return StreamingResponse(
            io.BytesIO(output_audio_bytes), 
            media_type="audio/wav", 
            headers={
                "X-Detected-Language": urllib.parse.quote(str(real_lang)),
                "X-Transcript": urllib.parse.quote(str(recognized_text)),
                "X-Response-Text": urllib.parse.quote(str(final_text))
            }
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Pipeline error: {str(e)}")
