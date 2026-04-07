import json
import asyncio
import base64
import websockets
import random
import re
from fastapi import WebSocketDisconnect
from app.core.config import settings
from app.services.retriever import query_postgres_database
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
    "Close! Valid, not executed yet.", "Almost there... valid, not executed.",
    "Good one... valid, not executed.", "Nearly there! Valid, not executed.",
    "On track... valid, not executed.", "So near... valid, not executed.",
    "You're close... valid, not executed.", "Almost right... valid, not executed.",
    "Getting there... valid, not executed.", "Not quite... valid, not executed.",
    "Close enough... valid, not executed."
]

TRANSLATIONS = {
    "ta": {"Valid": "காம்போ உறுதியானது.", "Invalid": "காம்போ தவறானது.", "Exclude": "உறுதியானது, ஆனால் இன்னும் இல்லை."},
    "te": {"Valid": "కాంబో చెల్లుబాటు అయ్యేది.", "Invalid": "కాంబో చెల్లదు.", "Exclude": "చెల్లుబాటు, కానీ అమలు కాలేదు."},
    "hi": {"Valid": "कॉम्बो वैध है।", "Invalid": "यह कॉम्बो अवैध है।", "Exclude": "वैध है, लेकिन निष्पादित नहीं।"},
    "es": {"Valid": "El combo es válido.", "Invalid": "El combo es inválido.", "Exclude": "Casi... válido, pero no ejecutado."},
    "ja": {"Valid": "有効なコンボです。", "Invalid": "無効なコンボです。", "Exclude": "有効ですが、まだ実行されていません。"},
    "fr": {"Valid": "Le combo est valide.", "Invalid": "Le combo est invalide.", "Exclude": "Presque... valide, pas encore exécuté."}
}

OPENAI_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"

class RealtimeService:
    def __init__(self, client_ws, game_mode="Mode 1", user_uid="guest"):
        self.client_ws = client_ws
        self.game_mode = game_mode
        self.user_uid = user_uid
        self.openai_ws = None
        self.last_numbers = [] # PERSISTENT MEMORY FOR 'WHY?' QUESTIONS
        self._relay_count = 0 # Diagnostic Counter

    async def connect(self):
        """Unified Connection: Restores session context and establishes OpenAI WebSocket."""
        clean_name = re.sub(r'(?i)Mode\s*\d+\s*-?\s*', '', self.game_mode).strip()
        try:
            # 1. Restore 'Zero-Amnesia' Context from Redis
            session_data = await session_store.get_session_data(self.user_uid)
            self.last_numbers = session_data.get("last_numbers", [])
            
            # 2. Establish OpenAI Handshake
            headers = {
                "Authorization": f"Bearer {settings.OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
            self.openai_ws = await asyncio.wait_for(
                websockets.connect(OPENAI_URL, extra_headers=headers),
                timeout=15.0
            )
            
            # 3. Configure Scholarly Instruction Set
            await self._setup_session()
            
            # 4. Notify Client of Success (Crucial for Lazy-Connection Completer)
            await self.client_ws.send_text(json.dumps({
                "type": "connection_success",
                "status": "Connected",
                "message": "Unified Bridge Active",
                "mode": clean_name
            }))
            
            print(f"[REALTIME] Unified Connection Established for {self.user_uid}. Memory: {self.last_numbers}")
        except Exception as e:
            print(f"[REALTIME] Connection FAILED for {self.user_uid}: {e}")
            raise

    async def _setup_session(self):
        """Configures tools and system prompt with Hierarchical Truth rules."""
        clean_name = re.sub(r'(?i)Mode\s*\d+\s*-?\s*', '', self.game_mode).strip()
        
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "instructions": f"""You are the EpicVerse AI game master.
                Current Mode: {clean_name}.
                HIERARCHICAL TRUTH RULES:
                1. PRIMARY COMBO CHECK (First Question):
                   - When a user calls 'query_database_for_combo', you MUST respond with ONLY ONE of these rhythmic messages, TRANSLATED into the user's language:
                     "Ah... rightly placed.", "Good flow... valid.", "Yes... a true, valid combo."
                   - DO NOT provide any scripture details or segments in the first response.
                2. DEEP-DIVE (WHY/HOW):
                   - If the user follows up with "Why?", "How?", or "Explain", you MUST translate the 'final_segment' and 'revised_scholar_reason' into the user's language.
                   - Provide the full scholarly context with reverence, maintaining the user's language throughout.
                3. GLOBAL POLYGLOT FIDELITY: You MUST respond in the EXACT language the user spoke. You are a global master of 100+ world languages. Identify the seeker's language and mirror it with 100% accuracy for the rhythmic intro, the scripture truth, and the scholarly reason. NEVER leak English into a non-English conversation.
                4. SMART SPLIT NUMERICAL RULES: Seekers provide two card numbers between 1-120. If you hear a concatenated number, you MUST split it:
                   - 3 digits (e.g., "129") -> card1: "1", card2: "29".
                   - 4 digits (e.g., "1256") -> card1: "12", card2: "56".
                   - Ensure both cards are within the 1-120 range before calling the tool.
                Context Memory (Previously discussed): {self.last_numbers if self.last_numbers else 'No combos yet'}.
                If they ask 'Why?' immediately, refer to these numbers.
""",
                "tools": [
                    {
                    "type": "function",
                    "name": "query_postgres_database",
                    "description": "Call this whenever the user mentions two card numbers or a 'combo' (e.g., '1 and 29', '56 and 88', 'Check numbers 12 and 13'). Return the result to the user with a scholarly, divine tone.",
                    "parameters": {
                            "type": "object",
                            "properties": {
                                 "card1": {"type": "string", "description": "ID of first card (1-120). If merged (e.g. 129), split it into 1 & 29."},
                                 "card2": {"type": "string", "description": "ID of second card (1-120). If merged (e.g. 1256), split it into 12 & 56."}
                            },
                            "required": ["card1", "card2"]
                        }
                    }
                ],
                 "tool_choice": "auto",
                 "turn_detection": {
                     "type": "server_vad",
                     "threshold": 0.5,
                     "prefix_padding_ms": 500,
                     "silence_duration_ms": 1200
                 },
                 "input_audio_format": "pcm16",
                 "input_audio_transcription": {"model": "whisper-1"}
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
                            print("[REALTIME] FORCE RESPONSE: Client signaled 'end'.")
                            await self.openai_ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
                            await self.openai_ws.send(json.dumps({"type": "response.create"}))

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
                    try:
                        await self.client_ws.send_bytes(audio_bytes)
                    except Exception:
                        # BUG FIX: Client may have disconnected, but we must NOT stop here.
                        # We keep draining OpenAI so tool calls complete cleanly.
                        pass

                # 2. JSON Event Relay (skip raw audio buffer echoes)
                elif event["type"] != "input_audio_buffer.append":
                    try:
                        await self.client_ws.send_text(raw_message)
                    except Exception:
                        pass  # Client disconnected, continue for tool calls

                # 3. Intercept Tool Call — executes DB query and feeds result back
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
            msg_list = VALID_MSGS if status == "Valid" else INVALID_MSGS
            if status == "Exclude": msg_list = EXCLUDE_MSGS
            random_msg = random.choice(msg_list)
            
            # Enrichment for AI Brain
            enriched_result = {
                "status": status,
                "preferred_message": random_msg,
                "final_segment": data.get("final_segment"),
                "revised_scholar_reason": data.get("revised_scholar_reason"),
                "instruction": "Prepend the translated 'preferred_message' to your response. For the first answer, use ONLY 'final_segment'. If they ask 'Why?', reveal 'revised_scholar_reason'."
            }
            output_str = json.dumps(enriched_result)
        except Exception:
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
