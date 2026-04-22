import json
import asyncio
import base64
import websockets
import random
import re
from fastapi import WebSocketDisconnect
from app.core.config import settings
from app.services.retriever import query_postgres_database
from app.services.user_db import verify_session, is_session_active
from app.services.memory_store import session_store

# SCHOLARLY PREFERRED MESSAGES (LEGACY AI LOGIC)
VALID_MSGS = [
    "Ah... rightly placed. Valid.", "Yes... a true, valid combo.", "Proceed... this is valid.",
    "You learn well... this is valid.", "Wisely played... valid.", "Accepted... it is valid.",
    "Good... this holds valid.", "On point... this is valid.", "Good flow... valid.",
    "That fits... valid.", "Well aligned... valid."
]
INVALID_MSGS = [
    "Hmm... invalid combo.", "Not quite... invalid combo.", "Almost... invalid combo.",
    "That slipped... invalid combo.", "Off track... invalid combo.", "Doesn't align... invalid combo.",
    "Try again... invalid combo.", "Close, but... invalid combo.", "That didn't land... invalid combo.",
    "Bit off... invalid combo.", "Doesn't quite work... invalid combo."
]
EXCLUDE_MSGS = [
    "Close! Valid, but excluded yet.", "Almost there... valid, but excluded.",
    "Good one... valid, but excluded.", "Nearly there! Valid, but excluded.",
    "On track... valid, but excluded.", "So near... valid, but excluded.",
    "You're close... valid, but excluded.", "Almost right... valid, but excluded.",
    "Getting there... valid, but excluded.", "Not quite... valid, but excluded.",
    "Close enough... valid, but excluded."
]
MODE_FAILURE_MSGS = [
    "Wrong mode. This character has no part in this chapter. Big card, wrong room.",
    "This character is not part of this mode's story. Not their chapter, not their moment.",
    "This character sat this mode out entirely. No role, no lines, no score.",
    "Not their era. The plot moved on without them for this one.",
    "Lore-accurate no-show. This character simply doesn't exist in this mode."
]


OPENAI_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"

class RealtimeService:
    def __init__(self, client_ws, game_mode="Mode 1", user_uid="guest", session_id="none"):
        self.client_ws = client_ws
        self.game_mode = game_mode
        self.user_uid = user_uid
        self.session_id = session_id
        self.openai_ws = None
        self.last_numbers = [] # PERSISTENT MEMORY FOR 'WHY?' QUESTIONS
        self.primary_language = "en" # Default
        self._relay_count = 0 # Diagnostic Counter
        self._current_transcript = "" # Accumulator for terminal logging

    async def connect(self):
        """Unified Connection: Restores session context and establishes OpenAI WebSocket."""
        clean_name = re.sub(r'(?i)Mode\s*\d+\s*-?\s*', '', self.game_mode).strip()
        try:
            print(f"[AUTH] UID: {self.user_uid} | Requesting connection for Journey: {clean_name}")
            
            # 1. Restore 'Zero-Amnesia' Context from Redis & Postgres
            from app.services.user_db import get_user
            user_profile = await get_user(self.user_uid)
            session_data = await session_store.get_session_data(self.user_uid)
            
            self.last_numbers = session_data.get("last_numbers", [])
            if user_profile:
                self.primary_language = user_profile.get("primary_language", "en")
            
            # 2. Establish OpenAI Handshake
            headers = {
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
            
            print(f"[REALTIME] Connecting to OpenAI for {self.user_uid}...")
            try:
                self.openai_ws = await asyncio.wait_for(
                    websockets.connect(OPENAI_URL, extra_headers=headers),
                    timeout=15.0
                )
            except websockets.exceptions.InvalidStatusCode as e:
                print(f"!!! [CONCURRENCY ERROR] OpenAI REJECTED connection for {self.user_uid}")
                print(f"!!! Status Code: {e.status_code}")
                if e.status_code == 429:
                    print("!!! REASON: Rate Limit or Concurrent Session Limit reached on your OpenAI Tier.")
                raise Exception(f"OpenAI connection rejected (Code: {e.status_code})")
            except asyncio.TimeoutError:
                print(f"!!! [CONCURRENCY ERROR] OpenAI Handshake TIMED OUT for {self.user_uid}")
                raise Exception("OpenAI handshake timed out (Check backend net speed)")
            
            # 3. Configure Scholarly Instruction Set
            await self._setup_session()
            
            # 4. Notify Client of Success
            await self.client_ws.send_text(json.dumps({
                "type": "connection_success",
                "status": "Connected",
                "message": "Unified Bridge Active",
                "mode": clean_name
            }))
            
            print(f"[REALTIME] Unified Connection Established for {self.user_uid}. Memory: {self.last_numbers}")
        except Exception as e:
            print(f"[REALTIME] Connection FAILED for {self.user_uid}: {e}")
            # Send error back to phone before closing
            try:
                await self.client_ws.send_text(json.dumps({
                    "type": "error",
                    "code": "CONNECTION_FAILED",
                    "message": str(e)
                }))
            except:
                pass
            raise

    async def _setup_session(self):
        """Configures tools and system prompt with Hierarchical Truth rules."""
        clean_name = re.sub(r'(?i)Mode\s*\d+\s*-?\s*', '', self.game_mode).strip()
        
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": f"""Role: Robotic Scribe. 
Rule 1: CALL 'query_postgres_database' for every number pair combo check.
Rule 2: Turn 1 (Fact Check) -> ONLY speak the text in 'MANDATORY_SPEAK_THIS' (translated to user's language). No extra words.
Rule 3: Turn 2 (Why/How) -> ONLY speak the 'revised_scholar_reason' raw text. NO modification. NO intro like "The reason is..." or "Here is why...". Speak the raw content immediately.
Rule 4: NO FLOWERY LANGUAGE. Never say: "tapestry", "celestial", "mystery", "harmony", "divine", "weaving", "unfold", "journey".
Rule 5: ABSOLUTE LANGUAGE MIRRORING. Detect user language and respond EXCLUSIVELY in that language. THIS IS A HARD CONSTRAINT.
Rule 6: NO CONVERSATIONAL FILLER. No "Sure!", "Okay!", "I understand", "Actually", or small talk.
Rule 7: SCOPE LOCK. ONLY respond to questions related to the EpicVerse game, its mechanics, or the Ramayana scriptures. If asked about unrelated topics (e.g., general news, math, music, or other apps), say: "I am the Guardian of the EpicVerse scriptures. I can only speak of the Ramayana journey. Let us return to the cards."
Journey: {clean_name}.
Primary Language Hint: {self.primary_language}""",
                "tools": [
                    {
                    "type": "function",
                    "name": "query_postgres_database",
                    "description": "Truth check for card combo IDs (1-120).",
                    "parameters": {
                            "type": "object",
                            "properties": {
                                 "card1": {"type": "string"},
                                 "card2": {"type": "string"}
                            },
                            "required": ["card1", "card2"]
                        }
                    }
                ],
                 "tool_choice": "auto",
                 "turn_detection": {
                     "type": "server_vad",
                     "threshold": 0.5,
                     "prefix_padding_ms": 300,
                     "silence_duration_ms": 650
                 },
                 "output_audio_format": "pcm16",
                 "input_audio_transcription": {
                     "model": "whisper-1"
                 }
            }
        }
        # Force 16kHz for mobile low-latency compatibility
        session_update["session"]["input_audio_format"] = "pcm16"
        # session_update["session"]["input_audio_sample_rate"] = 16000 # Default is often 24k, we force 16k
        await self.openai_ws.send(json.dumps(session_update))

    # Backend-only control messages that must NOT be forwarded to OpenAI
    _BACKEND_ONLY_TYPES = {"stop_wakeword", "start_wakeword", "ping"}

    async def relay_client_to_openai(self):
        """Relays audio/text bytes from Flutter app to OpenAI."""
        try:
            while True:
                message = await self.client_ws.receive()
                if "type" in message and message["type"] == "websocket.disconnect":
                    break
                if "bytes" in message:
                    self._relay_count += 1
                    if self._relay_count % 20 == 0:
                        # SECURITY: Check if this session has been 'Kicked' by a new device
                        if not await is_session_active(self.user_uid, self.session_id):
                            print(f"[AUTH] SESSION KICKED for {self.user_uid}. Another device logged in.")
                            await self.client_ws.send_text(json.dumps({
                                "type": "error",
                                "code": "SESSION_EXPIRED",
                                "message": "Logged in on another device."
                            }))
                            await self.client_ws.close()
                            return

                        print(f"[REALTIME] RELAYING AUDIO: {len(message['bytes'])} bytes (Chunk {self._relay_count})")
                    audio_b64 = base64.b64encode(message["bytes"]).decode("utf-8")
                    await self.openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": audio_b64
                    }))
                elif "text" in message:
                    client_msg = message["text"]
                    try:
                        data = json.loads(client_msg)
                        msg_type = data.get("type", "")

                        if msg_type == "end":
                            if self._relay_count > 2: # Only commit if we actually sent some audio chunks
                                print(f"[REALTIME] FORCE RESPONSE: Client signaled 'end' after {self._relay_count} chunks.")
                                await self.openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                                await self.openai_ws.send(json.dumps({"type": "response.create"}))
                            else:
                                print("[REALTIME] IGNORED 'end': Buffer too small (< 2 chunks).")

                        elif msg_type in self._BACKEND_ONLY_TYPES:
                            # BUG FIX: These are backend control signals only.
                            # Sending them to OpenAI caused it to close with 1005.
                            print(f"[REALTIME] Backend-only msg received: '{msg_type}' (not relayed to OpenAI)")

                        elif msg_type == "text_query":
                            # Convert text query to a proper OpenAI conversation item
                            text = data.get("text", "")
                            if text:
                                await self.openai_ws.send(json.dumps({
                                    "type": "conversation.item.create",
                                    "item": {
                                        "type": "message",
                                        "role": "user",
                                        "content": [{"type": "input_text", "text": text}]
                                    }
                                }))
                                await self.openai_ws.send(json.dumps({"type": "response.create"}))

                        else:
                            # Only relay valid OpenAI-compatible messages
                            await self.openai_ws.send(client_msg)

                    except json.JSONDecodeError:
                        pass  # Drop malformed non-JSON messages silently
        except WebSocketDisconnect:
            print(f"[REALTIME] Client Disconnected: {self.user_uid}")
        except Exception as e:
            print(f"[REALTIME] Relay Client Error: {e}")

    async def relay_openai_to_client(self):
        """Relays audio/text back and triggers Tool Calling for scripture truth."""
        try:
            async for raw_message in self.openai_ws:
                event = json.loads(raw_message)

                # 1. Audio Streaming Delta — send as binary bytes
                if event["type"] == "response.audio.delta":
                    audio_bytes = base64.b64decode(event["delta"])
                    if self._relay_count % 10 == 0:
                        print(f"[TTS DEBUG] Streaming Audio: {len(audio_bytes)} bytes")
                    try:
                        await self.client_ws.send_bytes(audio_bytes)
                    except Exception:
                        pass

                # 2. JSON Event Relay (skip raw audio buffer echoes)
                elif event["type"] != "input_audio_buffer.append":
                    # LOGGING: Print non-audio events to catch errors
                    if event["type"] == "input_audio_buffer.speech_started":
                        print("\n[STT INPUT] >>> User started speaking...")
                    elif event["type"] == "input_audio_buffer.speech_stopped":
                        print("\n[STT INPUT] <<< User stopped speaking. Transcribing...")
                    
                    if event["type"] == "error":
                        print(f"[REALTIME] CRITICAL OPENAI ERROR: {json.dumps(event.get('error', {}), indent=2)}")
                    
                    try:
                        await self.client_ws.send_text(raw_message)
                    except Exception:
                        pass  # Client disconnected, continue for tool calls

                # 3. Transcript Logging
                elif event["type"] == "response.audio_transcript.delta":
                    self._current_transcript += event["delta"]
                    # Print delta to terminal for real-time visibility
                    print(f"\r[AI]: {self._current_transcript}", end="", flush=True)

                elif event["type"] == "response.audio_transcript.done":
                    print(f"\n[AI FINAL]: {self._current_transcript}")
                    self._current_transcript = "" # Reset for next turn

                # 4. FAST-PATH: Intercept User Transcript for Pre-Fetch
                elif event["type"] == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    print(f"\n[STT DEBUG] User said: '{transcript}'")
                    nums = re.findall(r'\d+', transcript)
                    if len(nums) >= 2:
                        print(f"[FAST-PATH] Detected {nums[0]} & {nums[1]}. Triggering Pre-Fetch...")
                        asyncio.create_task(query_postgres_database(self.game_mode, nums[0], nums[1]))

                # 5. Intercept Tool Call — executes DB query and feeds result back
                if event["type"] == "response.done":
                    output_items = event.get("response", {}).get("output", [])
                    for item in output_items:
                        if item.get("type") == "function_call":
                            await self._handle_tool_call(item)

        except WebSocketDisconnect:
            print(f"[REALTIME] WebSocket Disconnect during OpenAI relay: {self.user_uid}")
        except Exception as e:
            print(f"[REALTIME] Relay OpenAI Error: {e}")

    async def _handle_tool_call(self, tool_call):
        """Executes Truth Check and update Redis session context."""
        call_id = tool_call["call_id"]
        args = json.loads(tool_call["arguments"])
        
        # Capture and Persist numbers for 'Why?' logic
        num1 = "".join(filter(str.isdigit, str(args.get('card1', ''))))
        num2 = "".join(filter(str.isdigit, str(args.get('card2', ''))))
        if num1 and num2:
            self.last_numbers = [num1, num2]
            await session_store.update_last_numbers(self.user_uid, self.last_numbers)

        print(f"[REALTIME] TOOL SYNC: Querying Truth Matrix for {num1} & {num2}")
        raw_result = await query_postgres_database(self.game_mode, num1, num2)
        
        try:
            data = json.loads(raw_result)
            status = data.get("status", "Invalid")
            
            # Select Random Preferred Message based on status
            # Important: Database uses 'Valid but Excluded', code was only checking 'Exclude'
            if status == "Valid":
                msg_list = VALID_MSGS
            elif "Excluded" in status or status == "Exclude":
                msg_list = EXCLUDE_MSGS
            else:
                msg_list = MODE_FAILURE_MSGS
            
            random_msg = random.choice(msg_list)
            
            # Explicitly stream text to client for immediate UI/Test feedback
            try:
                await self.client_ws.send_text(json.dumps({
                    "type": "response.audio_transcript.delta",
                    "delta": f"{random_msg} "
                }))
            except:
                pass
            
            # Enrichment for AI Brain - Hard Forced keys
            enriched_result = {
                "status": status,
                "MANDATORY_SPEAK_THIS": random_msg,
                "scripture_logic": data.get("final_segment"),
                "revised_scholar_reason": data.get("revised_scholar_reason"),
                "CONSTRAINTS": "You are a SCRIBE. READ MANDATORY_SPEAK_THIS word-for-word. DO NOT INTERPRET."
            }
            output_str = json.dumps(enriched_result)
            
            # ENHANCED TERMINAL LOG FOR TOOL RESULT
            print(f"\n[MATRIX RESULT]: Mode='{self.game_mode}' | Cards={num1}&{num2} | Status='{status}' | Sent_Msg='{random_msg}'")
            if status == "Valid":
                print(f"[MATRIX DATA]: {data.get('final_segment')[:100]}...")
        except Exception as e:
            print(f"[REALTIME] Tool Enrichment Error: {e}")
            output_str = raw_result

        # Relay Output to AI Brain
        await self.openai_ws.send(json.dumps({
            "type": "conversation.item.create",
            "item": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": output_str
            }
        }))
        await self.openai_ws.send(json.dumps({"type": "response.create"}))

    async def cleanup(self):
        if self.openai_ws:
            try:
                await self.openai_ws.close()
            except Exception:
                pass
        print(f"[REALTIME] Session Cleaned Up for: {self.user_uid}")

    async def run(self):
        """Two-Phase Bridge: client relay finishes first, then OpenAI drains fully."""
        await self.connect()
        t1 = asyncio.create_task(self.relay_client_to_openai())
        t2 = asyncio.create_task(self.relay_openai_to_client())

        # Phase 1: Wait for client relay to finish (client sends 'end' then disconnects)
        await asyncio.wait([t1], return_when=asyncio.FIRST_COMPLETED)
        print(f"[REALTIME] Client relay ended for {self.user_uid}. Waiting for AI response...")

        # Phase 2: BUG FIX — Keep the OpenAI relay alive after client disconnects.
        # FIRST_COMPLETED was cancelling t2 mid-tool-call, so OpenAI never got
        # the function result and closed the connection with 1005.
        # Now we give OpenAI up to 45 seconds to finish the response.
        try:
            await asyncio.wait_for(asyncio.shield(t2), timeout=45.0)
            # AI Finished Speaking
            print(f"[REALTIME] AI response finished for {self.user_uid}")
        except asyncio.TimeoutError:
            print(f"[REALTIME] AI response timeout (45s) for {self.user_uid}")
        except Exception:
            pass

        # Cleanup both tasks
        for task in [t1, t2]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

        if self.openai_ws:
            try:
                await self.openai_ws.close()
            except Exception:
                pass
        print(f"[REALTIME] Session Cleaned Up for: {self.user_uid}")
