import asyncio
import base64
import datetime as _dt
import json
import time
from typing import Optional

import websockets
import websockets.exceptions
from websockets.legacy.client import connect as ws_connect
from fastapi import WebSocket

from app.core.config import settings
from app.services.retriever import query_postgres_database

# URL is built from settings.OPENAI_REALTIME_MODEL (env-overridable) so we
# don't hard-code a dated snapshot that OpenAI may retire without warning.
OPENAI_REALTIME_URL = (
    f"wss://api.openai.com/v1/realtime?model={settings.OPENAI_REALTIME_MODEL}"
)

# Seconds to suppress VAD after TTS finishes — prevents mic from picking up speaker echo
_TTS_ECHO_COOLDOWN = 1.5

# ── Combo response message pools ──────────────────────────────────────────────

import random as _random

_VALID_MSGS = [
    "Ah... rightly placed. Valid.",
    "Yes... a true, valid combo.",
    "Proceed... this is valid.",
    "You learn well... this is valid.",
    "Wisely played... valid.",
    "Accepted... it is valid.",
    "Good... this holds valid.",
    "On point... this is valid.",
    "Good flow... valid.",
    "That fits... valid.",
    "Well aligned... valid.",
]

_INVALID_MSGS = [
    "Hmm... invalid combo.",
    "Not quite... invalid combo.",
    "Almost... invalid combo.",
    "That slipped... invalid combo.",
    "Off track... invalid combo.",
    "Doesn't align... invalid combo.",
    "Try again... invalid combo.",
    "Close, but... invalid combo.",
    "That didn't land... invalid combo.",
    "Bit off... invalid combo.",
    "Doesn't quite work... invalid combo.",
]

_EXCLUDE_MSGS = [
    "Close! Valid, but excluded yet.",
    "Almost there... valid, but excluded.",
    "Good one... valid, but excluded.",
    "Nearly there! Valid, but excluded.",
    "On track... valid, but excluded.",
    "So near... valid, but excluded.",
    "You're close... valid, but excluded.",
    "Almost right... valid, but excluded.",
    "Getting there... valid, but excluded.",
    "Not quite... valid, but excluded.",
    "Close enough... valid, but excluded.",
]

_MODE_FAILURE_MSGS = [
    "Wrong mode. This character has no part in this chapter. Big card, wrong room.",
    "This character is not part of this mode's story. Not their chapter, not their moment.",
    "This character sat this mode out entirely. No role, no lines, no score.",
    "Not their era. The plot moved on without them for this one.",
    "Lore-accurate no-show. This character simply doesn't exist in this mode.",
]


def _pick_combo_message(final_status: str | None, is_error: bool) -> str:
    if is_error:
        return _random.choice(_MODE_FAILURE_MSGS)
    s = (final_status or "").strip()
    if s == "Valid":
        return _random.choice(_VALID_MSGS)
    if s == "Valid but Excluded":
        return _random.choice(_EXCLUDE_MSGS)
    if s == "Invalid":
        return _random.choice(_INVALID_MSGS)
    # Fallback for unexpected values — treat as invalid
    return _random.choice(_INVALID_MSGS)

# Maps Flutter display names (lowercased) → exact gameplay_mode values in DB
_MODE_MAP: dict[str, str] = {
    "originarc (balakanda)":      "OriginArc (Balakanda)",
    "crownshift (ayodhyakanda)":  "CrownShift (AyodhyaKanda)",
    "wildrun (aranyakanda)":      "WildRun (AranyaKanda)",
    "glowline (kishkindhakanda)": "GlowLine (KishkindhaKanda)",
    "lankaleap (sundarakanda)":   "lankaLeap (SundaraKanda)",
    "warroom (yuddhakanda)":      "WarRoom (YuddhaKanda)",
    "afterlight (uttarakanda)":   "AfterLight (UttaraKanda)",
}


def _resolve_db_mode(mode: str) -> str:
    stripped = mode.strip()
    return _MODE_MAP.get(stripped.lower(), stripped)


# ── Structured logger ─────────────────────────────────────────────────────────
#  Format: [HH:MM:SS.mmm] [STEP              ] uid=XXXXXXXX → detail

def _log(step: str, uid: str, data: str = "") -> None:
    ts  = _dt.datetime.utcnow().strftime("%H:%M:%S.%f")[:-3]
    tag = f"[{step:<22}]"
    suffix = f" → {data}" if data else ""
    print(f"[{ts}] {tag} uid={uid[-8:]}{suffix}", flush=True)


# Separator printed once per session for readability
def _log_sep(_uid: str, label: str = "") -> None:
    bar = "─" * 55
    print(f"  {bar} {label}", flush=True)


# ── Multilingual number normalizer ────────────────────────────────────────────

_NUMBER_WORDS: dict[str, int] = {
    # ── English ──
    "zero":0,"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,
    "eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,
    "fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,
    "nineteen":19,"twenty":20,"twenty one":21,"twenty-one":21,"twentyone":21,
    "twenty two":22,"twenty-two":22,"twentytwo":22,"twenty three":23,
    "twenty-three":23,"twentythree":23,"twenty four":24,"twenty-four":24,
    "twentyfour":24,"twenty five":25,"twenty-five":25,"twentyfive":25,
    "twenty six":26,"twenty-six":26,"twentysix":26,"twenty seven":27,
    "twenty-seven":27,"twentyseven":27,"twenty eight":28,"twenty-eight":28,
    "twentyeight":28,"twenty nine":29,"twenty-nine":29,"twentynine":29,
    "thirty":30,"thirty one":31,"thirty-one":31,"thirty two":32,"thirty-two":32,
    "thirty three":33,"thirty-three":33,"thirty four":34,"thirty-four":34,
    "thirty five":35,"thirty-five":35,"thirty six":36,"thirty-six":36,
    "thirty seven":37,"thirty-seven":37,"thirty eight":38,"thirty-eight":38,
    "thirty nine":39,"thirty-nine":39,"forty":40,"forty one":41,"forty-one":41,
    "forty two":42,"forty-two":42,"forty three":43,"forty-three":43,
    "forty four":44,"forty-four":44,"forty five":45,"forty-five":45,
    "forty six":46,"forty-six":46,"forty seven":47,"forty-seven":47,
    "forty eight":48,"forty-eight":48,"forty nine":49,"forty-nine":49,
    "fifty":50,
    # English ordinals
    "first":1,"second":2,"third":3,"fourth":4,"fifth":5,"sixth":6,"seventh":7,
    "eighth":8,"ninth":9,"tenth":10,"eleventh":11,"twelfth":12,
    # ── Tamil ──
    "ஒன்று":1,"ஒண்ணு":1,"ஒன்னு":1,"ஒன்":1,
    "இரண்டு":2,"ரெண்டு":2,"இரண்":2,
    "மூன்று":3,"மூணு":3,"மூன்":3,
    "நான்கு":4,"நாலு":4,"நான்":4,
    "ஐந்து":5,"அஞ்சு":5,"ஐந்":5,
    "ஆறு":6,"ஆற்":6,
    "ஏழு":7,"ஏழ்":7,
    "எட்டு":8,"எட்":8,
    "ஒன்பது":9,"ஒம்பது":9,"ஒன்பத்":9,
    "பத்து":10,"பத்":10,
    "பதினொன்று":11,"பதினொண்ணு":11,
    "பன்னிரண்டு":12,"பன்னெண்டு":12,
    "பதின்மூன்று":13,"பதிமூணு":13,
    "பதினான்கு":14,"பதினாலு":14,
    "பதினைந்து":15,"பதினஞ்சு":15,
    "பதினாறு":16,"பதினேழு":17,"பதினெட்டு":18,
    "பத்தொன்பது":19,"பத்தொம்பது":19,
    "இருபது":20,
    "இருபத்தொன்று":21,"இருபத்தொண்ணு":21,"இருபத்தொன்னு":21,
    "இருபத்திரண்டு":22,"இருபத்ரெண்டு":22,
    "இருபத்திமூன்று":23,"இருபத்திமூணு":23,
    "இருபத்திநான்கு":24,"இருபத்திநாலு":24,
    "இருபத்தைந்து":25,"இருபத்தஞ்சு":25,
    "இருபத்தாறு":26,"இருபத்தேழு":27,"இருபத்தெட்டு":28,
    "இருபத்தொன்பது":29,"இருபத்தொம்பது":29,
    "முப்பது":30,
    "முப்பத்தொன்று":31,"முப்பத்திரண்டு":32,"முப்பத்திமூன்று":33,
    "முப்பத்திநான்கு":34,"முப்பத்தைந்து":35,"முப்பத்தாறு":36,
    "முப்பத்தேழு":37,"முப்பத்தெட்டு":38,"முப்பத்தொன்பது":39,
    "நாற்பது":40,"நாப்பது":40,
    "ஐம்பது":50,
    # ── Hindi ──
    "ek":1,"एक":1,"do":2,"दो":2,"teen":3,"तीन":3,"char":4,"चार":4,
    "paanch":5,"पाँच":5,"paach":5,"chhah":6,"छह":6,"chha":6,
    "saat":7,"सात":7,"aath":8,"आठ":8,"nau":9,"नौ":9,"das":10,"दस":10,
    "gyarah":11,"ग्यारह":11,"barah":12,"बारह":12,"terah":13,"तेरह":13,
    "chaudah":14,"चौदह":14,"pandrah":15,"पंद्रह":15,"solah":16,"सोलह":16,
    "satrah":17,"सत्रह":17,"atharah":18,"अठारह":18,"unnees":19,"उन्नीस":19,
    "bees":20,"बीस":20,"ikkees":21,"इक्कीस":21,"baaees":22,"बाईस":22,
    "teyees":23,"तेईस":23,"chaubees":24,"चौबीस":24,"pachchees":25,"पच्चीस":25,
    "chhabees":26,"छब्बीस":26,"sattaees":27,"सत्ताईस":27,
    "atthaees":28,"अट्ठाईस":28,"untees":29,"उनतीस":29,"tees":30,"तीस":30,
    # ── Malayalam ──
    "ഒന്ന്":1,"ഒന്നു":1,"ഒന്":1,"രണ്ട്":2,"രണ്ടു":2,"മൂന്ന്":3,"മൂന്നു":3,
    "നാല്":4,"നാലു":4,"അഞ്ച്":5,"അഞ്ചു":5,"ആറ്":6,"ആറു":6,
    "ഏഴ്":7,"ഏഴു":7,"എട്ട്":8,"എട്ടു":8,"ഒൻപത്":9,"ഒൻപതു":9,
    "പത്ത്":10,"പത്തു":10,"പതിനൊന്ന്":11,"പന്ത്രണ്ട്":12,
    "പതിമൂന്ന്":13,"പതിനാല്":14,"പതിനഞ്ച്":15,"പതിനാറ്":16,
    "പതിനേഴ്":17,"പതിനെട്ട്":18,"പത്തൊൻപത്":19,"ഇരുപത്":20,
    "ഇരുപത്തൊന്ന്":21,"ഇരുപത്തിരണ്ട്":22,"ഇരുപത്തിമൂന്ന്":23,
    "ഇരുപത്തിനാല്":24,"ഇരുപത്തഞ്ച്":25,"ഇരുപത്താറ്":26,
    "ഇരുപത്തേഴ്":27,"ഇരുപത്തെട്ട്":28,"ഇരുപത്തൊൻപത്":29,
    "മുപ്പത്":30,"നാല്പത്":40,"അമ്പത്":50,
}


def _normalize_number(value: str) -> int | None:
    """Convert spoken/written number in any language to int. Returns None if unrecognisable."""
    if not value:
        return None
    stripped = value.strip()

    # Already an integer string
    try:
        return int(stripped)
    except ValueError:
        pass

    # Strip common prefix words: "number 3", "card 5", "no. 7"
    lower = stripped.lower()
    for prefix in ("number ", "card ", "no. ", "no ", "# ", "#", "num "):
        if lower.startswith(prefix):
            rest = lower[len(prefix):].strip()
            try:
                return int(rest)
            except ValueError:
                if rest in _NUMBER_WORDS:
                    return _NUMBER_WORDS[rest]

    # Direct word lookup (case-insensitive)
    if lower in _NUMBER_WORDS:
        return _NUMBER_WORDS[lower]

    # Unicode script lookup (exact match for non-Latin scripts)
    if stripped in _NUMBER_WORDS:
        return _NUMBER_WORDS[stripped]

    # Try word2number library (English compound words like "twenty nine")
    try:
        from word2number import w2n  # type: ignore
        result = w2n.word_to_num(stripped)
        if isinstance(result, int):
            return result
    except Exception:
        pass

    return None


# ── Language detector ─────────────────────────────────────────────────────────

def _detect_language(text: str) -> str:
    for ch in text:
        cp = ord(ch)
        if 0x0B80 <= cp <= 0x0BFF: return "Tamil"
        if 0x0D00 <= cp <= 0x0D7F: return "Malayalam"
        if 0x0900 <= cp <= 0x097F: return "Hindi"
        if 0x0600 <= cp <= 0x06FF: return "Arabic"
        if 0x4E00 <= cp <= 0x9FFF: return "Chinese"
        if 0x3040 <= cp <= 0x30FF: return "Japanese"
    return "English"


# ── Constants ─────────────────────────────────────────────────────────────────

_BACKEND_ONLY_TYPES = {"stop_wakeword", "start_wakeword", "ping", "end"}

SYSTEM_INSTRUCTIONS = """You are a strict rule-based response engine for a card combo validation game. You have two response modes only.

━━━ MODE 1 — COMBO CHECK (user asks if X and Y is a combo) ━━━
1. Extract TWO card numbers from what the user said.
   The user may speak in ANY language — English, Tamil, Hindi, Malayalam, Arabic, French, Japanese, Korean, Spanish, German, or any other language.
   Numbers may be spoken as digits, words, or mixed in any language.
   YOU must convert them to digit strings. Never pass word-form numbers to the tool.
   Examples: "ek aur untees" → "1","29" | "vingt-neuf et un" → "29","1" | "一 と 二十九" → "1","29"

2. CRITICAL — TWO NUMBERS REQUIRED:
   If the user provides only ONE number (e.g. "29 combo", "is 5 valid", "check 10"):
   - DO NOT call the tool.
   - DO NOT say valid or invalid.
   - Ask the user for the second card number in the SAME language they spoke.

3. Only when you have BOTH numbers: call query_database_for_combo.
   Pass character and attribute as digit strings only (e.g. character="1", attribute="29").

4. If the tool result contains "ask_to_repeat": true — the card numbers could not be understood.
   Ask the user to clearly say both card numbers again in the SAME language they spoke.

5. Otherwise read the "avatar_response" field from the tool result.
   - If the user spoke English: speak the avatar_response word for word. Nothing added. Nothing removed.
   - If the user spoke ANY other language: translate the avatar_response into that exact language, then speak only the translation. No English. No mixing.

━━━ MODE 2 — REASON (user asks "why" or "how" after a combo check) ━━━
1. Do NOT call the tool again.
2. Find the "revised_scholar_reason" field from the MOST RECENT tool result in the conversation.
3. Detect the language the user just spoke in.
4. If the user spoke English: output the revised_scholar_reason exactly as stored. No changes.
5. If the user spoke ANY other language: translate the revised_scholar_reason into that exact language. Output only the translation. Nothing else.
6. Do not summarize, shorten, add context, or add any extra sentences.

━━━ LANGUAGE RULE ━━━
- Automatically detect the language of EVERY user message.
- Always respond in that exact same language. No exceptions.
- Never mix languages in a single response.
- Never switch languages unless the user switches first.
- The avatar_response and revised_scholar_reason fields are always stored in English.
  If the user spoke any non-English language, translate those fields before speaking — never output them in English to a non-English user.

━━━ CRITICAL — NO HALLUCINATION ━━━
- NEVER say "valid" or "invalid" without first calling query_database_for_combo. No exceptions.
- NEVER answer a combo check from memory, prior context, or conversation history.
- NEVER skip calling query_database_for_combo when you have two card numbers.
- NEVER generate your own explanation or description. Only speak what the database returns.
- NEVER add greetings, qualifiers, or extra sentences.
- If only ONE number is given, ask for the second. Do not guess. Do not call the tool with one number.
- If the question is unrelated to the game, say nothing.
- Every combo check = two numbers + one fresh tool call. No exceptions."""


class _SafeJsonEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (_dt.datetime, _dt.date)):
            return obj.isoformat()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        return str(obj)


_ACTIVE_SESSIONS: dict[str, "RealtimeSession"] = {}


# ── Session ───────────────────────────────────────────────────────────────────

class RealtimeSession:
    def __init__(self, client_ws: WebSocket, uid: str, mode: str, session_id: str):
        self.client_ws  = client_ws
        self.uid        = uid
        self.mode       = mode
        self.db_mode    = _resolve_db_mode(mode)
        self.session_id = session_id
        self.openai_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._active    = True

        # ── State tracking ────────────────────────────────────────────────────
        self._mic_streaming             = False   # True once first audio chunk arrives
        self._tts_streaming             = False   # True while audio is flowing to client
        self._audio_chunks_sent         = 0       # total chunks this turn
        self._audio_bytes_sent          = 0       # total bytes this turn
        self._audio_appended_since_commit = False # guard against empty commits
        self._turn_count                = 0       # conversation turns
        self._response_start_ts: Optional[float] = None  # latency tracking
        self._db_query_start_ts: Optional[float] = None
        self._tts_chunk_count           = 0       # audio chunks streamed to client
        self._partial_transcript        = ""      # accumulated STT partial
        self._tts_done_ts: Optional[float] = None  # when TTS last finished (echo cooldown)

    # ─────────────────────────────────────────────────────────────────────────
    # OpenAI connection
    # ─────────────────────────────────────────────────────────────────────────

    async def _connect_to_openai(self) -> bool:
        _log("OPENAI CONNECTING", self.uid,
             f"model={settings.OPENAI_REALTIME_MODEL} timeout=15s")
        t0 = time.monotonic()
        extra_headers = {
            "Authorization": f"Bearer {settings.OPENAI_API_KEY.strip()}",
            "OpenAI-Beta":   "realtime=v1",
        }
        try:
            self.openai_ws = await asyncio.wait_for(
                ws_connect(
                    OPENAI_REALTIME_URL,
                    extra_headers=extra_headers,
                    compression=None,
                    ping_interval=None,
                ),
                timeout=15.0,
            )
            elapsed = round((time.monotonic() - t0) * 1000)
            _log("OPENAI CONNECTED", self.uid,
                 f"websocket handshake OK in {elapsed}ms")
            return True
        except asyncio.TimeoutError:
            _log("OPENAI ERROR", self.uid, "connection timed out after 15s")
            return False
        except websockets.exceptions.InvalidStatusCode as e:
            _log("OPENAI ERROR", self.uid, f"HTTP {e.status_code} — check API key")
            return False
        except Exception as e:
            _log("OPENAI ERROR", self.uid, f"{type(e).__name__}: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Session setup
    # ─────────────────────────────────────────────────────────────────────────

    async def _setup_session(self):
        _log("SESSION SETUP", self.uid,
             f"mode={self.db_mode} | voice=alloy | vad=server_vad | "
             f"stt=whisper-1 | silence=400ms | threshold=0.5")
        payload = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": (
                    SYSTEM_INSTRUCTIONS
                    + f"\n\nCURRENT SESSION MODE: {self.db_mode}\n"
                      f"You MUST always pass exactly '{self.db_mode}' as the mode parameter "
                      f"when calling query_database_for_combo. Do not translate, shorten, or modify this value."
                ),
                "voice": "alloy",
                "input_audio_format":  "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type":                "server_vad",
                    "threshold":           0.5,
                    "prefix_padding_ms":   200,
                    "silence_duration_ms": 400,
                },
                "tools": [{
                    "type":        "function",
                    "name":        "query_database_for_combo",
                    "description": "Queries the card_combos table for combo status and reason.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "mode":      {"type": "string", "description": "Gameplay mode e.g. 'OriginArc (Balakanda)'"},
                            "character": {"type": "string", "description": "Character name or card number"},
                            "attribute": {"type": "string", "description": "Attribute card number (25+)"},
                        },
                        "required": ["mode", "character", "attribute"],
                    },
                }],
                "tool_choice": "auto",
            },
        }
        await self.openai_ws.send(json.dumps(payload))
        _log("SESSION CONFIGURED", self.uid,
             f"session.update sent — waiting for OpenAI confirmation")

    # ─────────────────────────────────────────────────────────────────────────
    # Audio resampling
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _resample_16k_to_24k(pcm16_bytes: bytes) -> bytes:
        try:
            import numpy as np
            samples = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32)
            if len(samples) == 0:
                return pcm16_bytes
            new_len  = int(len(samples) * 24000 / 16000)
            resampled = np.interp(
                np.linspace(0, len(samples) - 1, new_len),
                np.arange(len(samples)),
                samples,
            ).astype(np.int16)
            return resampled.tobytes()
        except Exception:
            return pcm16_bytes

    # ─────────────────────────────────────────────────────────────────────────
    # Tool call → DB query
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_tool_call(self, call_id: str, name: str, arguments: dict):
        if name != "query_database_for_combo":
            _log("TOOL UNKNOWN", self.uid, f"unknown tool '{name}' — ignoring")
            return

        ai_mode    = arguments.get("mode", "")
        target_mode = _resolve_db_mode(ai_mode) if ai_mode else self.db_mode
        char        = str(arguments.get("character", "?"))
        attribute   = str(arguments.get("attribute", "?"))

        _log_sep(self.uid, "DATABASE LOOKUP")
        _log("TOOL CALL RECEIVED",  self.uid,
             f"fn={name} | call_id={call_id[:12]}")
        _log("TOOL ARGS RAW",       self.uid,
             f"mode='{target_mode}' | char='{char}' | attribute='{attribute}'")

        # Normalise multilingual number words → digits
        char_int  = _normalize_number(char)
        attr_int  = _normalize_number(attribute)
        _log("TOOL ARGS NORM",      self.uid,
             f"char_norm={char_int} | attr_norm={attr_int}")

        # If either number is unrecognisable, ask the user to repeat
        if char_int is None or attr_int is None:
            _log("NUMBER PARSE FAIL", self.uid,
                 f"could not extract card numbers from char='{char}' attribute='{attribute}' — asking repeat")
            unclear = json.dumps({"ask_to_repeat": True})
            try:
                await self.openai_ws.send(json.dumps({
                    "type": "conversation.item.create",
                    "item": {
                        "type":    "function_call_output",
                        "call_id": call_id,
                        "output":  unclear,
                    },
                }))
                await self.openai_ws.send(json.dumps({"type": "response.create"}))
            except Exception as e:
                _log("TOOL SEND ERROR", self.uid, f"{e}")
            return

        char  = str(char_int)
        attribute = str(attr_int)

        _log("DB QUERY START",      self.uid,
             f"SELECT * FROM card_combos WHERE mode='{target_mode}' AND char='{char}' AND attribute='{attribute}'")

        self._db_query_start_ts = time.monotonic()
        output_str = ""
        try:
            result = await query_postgres_database(target_mode, char, attribute)
            elapsed_ms = round((time.monotonic() - self._db_query_start_ts) * 1000)

            # query_postgres_database returns a JSON string — parse to dict
            # so we can inspect final_status and inject avatar_response
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    pass

            if isinstance(result, dict):
                is_error = "error" in result or result.get("character_not_in_mode") is True
                status = result.get("final_status") or result.get("status", "")
                if is_error:
                    _log("DB NO MATCH", self.uid,
                         f"combo NOT found | query took {elapsed_ms}ms | {result.get('error')}")
                else:
                    reason_preview = str(result.get("revised_scholar_reason", ""))[:80]
                    _log("DB MATCH FOUND", self.uid,
                         f"status={status} | took={elapsed_ms}ms")
                    _log("DB REASON",     self.uid,
                         f"\"{reason_preview}{'...' if len(reason_preview) == 80 else ''}\"")

                # Inject avatar_response so LLM speaks it exactly (not its own words)
                avatar_msg = _pick_combo_message(
                    status if not is_error else None,
                    is_error=is_error,
                )
                result["avatar_response"] = avatar_msg
                _log("AVATAR RESPONSE", self.uid, f"\"{avatar_msg}\"")

            output_str = (
                json.dumps(result, cls=_SafeJsonEncoder)
                if isinstance(result, (dict, list)) else str(result)
            )
            _log("DB RESULT SENT",   self.uid,
                 f"result payload {len(output_str)}B → sending to LLM")
        except Exception as e:
            elapsed_ms = round((time.monotonic() - self._db_query_start_ts) * 1000)
            _log("DB ERROR", self.uid,
                 f"{type(e).__name__}: {e} | took={elapsed_ms}ms")
            output_str = json.dumps({"error": str(e)})

        try:
            await self.openai_ws.send(json.dumps({
                "type": "conversation.item.create",
                "item": {
                    "type":    "function_call_output",
                    "call_id": call_id,
                    "output":  output_str,
                },
            }))
            _log("TOOL RESULT SENT",  self.uid,
                 "function_call_output delivered to OpenAI")
            await self.openai_ws.send(json.dumps({"type": "response.create"}))
            _log("LLM GENERATE REQ",  self.uid,
                 "response.create sent — LLM will now formulate answer")
            self._response_start_ts = time.monotonic()
        except Exception as e:
            _log("TOOL SEND ERROR",   self.uid,
                 f"failed to deliver tool result: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Client → OpenAI relay
    # ─────────────────────────────────────────────────────────────────────────

    async def _relay_from_client(self):
        try:
            while self._active:
                message = await self.client_ws.receive()

                # ── Disconnect ─────────────────────────────────────────────
                if message.get("type") == "websocket.disconnect":
                    _log("CLIENT DISCONNECT", self.uid,
                         f"websocket closed | total_chunks={self._audio_chunks_sent} "
                         f"total_bytes={self._audio_bytes_sent} turns={self._turn_count}")
                    break

                # ── Audio bytes (mic stream) ────────────────────────────────
                if "bytes" in message:
                    raw = message["bytes"]

                    # Drop mic audio while AI is speaking — prevents mic from
                    # capturing speaker output and feeding it back as user input.
                    in_cooldown = (
                        self._tts_done_ts is not None
                        and (time.monotonic() - self._tts_done_ts) < _TTS_ECHO_COOLDOWN
                    )
                    if self._tts_streaming or in_cooldown:
                        continue

                    if not self._mic_streaming:
                        self._mic_streaming = True
                        self._turn_count   += 1
                        _log_sep(self.uid, f"TURN {self._turn_count}")
                        _log("MIC BUTTON ON",    self.uid,
                             "user opened mic — audio stream started")
                        _log("AUDIO CAPTURING",  self.uid,
                             "Flutter sending PCM16 16kHz mono chunks")

                    resampled = self._resample_16k_to_24k(raw)
                    self._audio_chunks_sent         += 1
                    self._audio_bytes_sent          += len(raw)
                    self._audio_appended_since_commit = True

                    # Log throughput every 50 chunks (~4 seconds of audio)
                    if self._audio_chunks_sent % 50 == 0:
                        kb = round(self._audio_bytes_sent / 1024, 1)
                        _log("AUDIO STREAMING",  self.uid,
                             f"chunks={self._audio_chunks_sent} bytes={kb}KB → resampled 16k→24k → forwarded to OpenAI")

                    await self.openai_ws.send(
                        json.dumps({
                            "type":  "input_audio_buffer.append",
                            "audio": base64.b64encode(resampled).decode(),
                        })
                    )

                # ── Text/JSON messages ──────────────────────────────────────
                elif "text" in message:
                    try:
                        data = json.loads(message["text"])
                    except Exception:
                        continue

                    msg_type = data.get("type", "")

                    # ── Backend-only control messages ──────────────────────
                    if msg_type in _BACKEND_ONLY_TYPES:
                        if msg_type == "end":
                            _log("MIC BUTTON OFF", self.uid,
                                 f"user closed mic | chunks={self._audio_chunks_sent} "
                                 f"bytes={round(self._audio_bytes_sent/1024,1)}KB")
                            # If TTS is still playing when mic closes, the buffer only
                            # contains speaker echo — clear it instead of committing.
                            in_cooldown = (
                                self._tts_done_ts is not None
                                and (time.monotonic() - self._tts_done_ts) < _TTS_ECHO_COOLDOWN
                            )
                            if self._tts_streaming or in_cooldown:
                                _log("ECHO CLEAR",    self.uid,
                                     "mic closed during TTS — clearing echo buffer")
                                try:
                                    await self.openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                                except Exception:
                                    pass
                                self._audio_appended_since_commit = False
                                self._mic_streaming   = False
                                self._audio_chunks_sent = 0
                                self._audio_bytes_sent  = 0
                                continue
                            _log("AUDIO STREAM END", self.uid,
                                 "mic closed — appending silence to trigger VAD commit")
                            # server_vad needs silence to auto-commit the buffer.
                            # When mic closes, audio stops and VAD never fires,
                            # leaving the buffer stuck. Silence padding gives VAD
                            # the window it needs to detect end-of-speech.
                            if self._audio_appended_since_commit:
                                silence_16k = bytes(int(16000 * 0.50 * 2))  # 500ms PCM16 @ 16kHz
                                silence_24k = self._resample_16k_to_24k(silence_16k)
                                try:
                                    await self.openai_ws.send(json.dumps({
                                        "type":  "input_audio_buffer.append",
                                        "audio": base64.b64encode(silence_24k).decode(),
                                    }))
                                    _log("SILENCE PADDING", self.uid,
                                         "500ms silence appended → VAD will detect end-of-speech")
                                except Exception as _e:
                                    _log("SILENCE PAD ERR", self.uid, f"{type(_e).__name__}: {_e}")
                            self._audio_appended_since_commit = False
                            self._mic_streaming   = False
                            self._audio_chunks_sent = 0
                            self._audio_bytes_sent  = 0
                        elif msg_type == "start_wakeword":
                            _log("WAKEWORD LISTEN", self.uid, "wake-word detection started")
                        elif msg_type == "stop_wakeword":
                            _log("WAKEWORD STOP",   self.uid, "wake-word detection stopped")
                        elif msg_type == "ping":
                            _log("PING",            self.uid, "keepalive ping received")
                        continue

                    # ── Guard empty commits ────────────────────────────────
                    if msg_type == "input_audio_buffer.commit":
                        if not self._audio_appended_since_commit:
                            _log("COMMIT BLOCKED",  self.uid,
                                 "buffer empty — skipping commit (prevents OpenAI error)")
                            continue
                        self._audio_appended_since_commit = False
                        _log("AUDIO COMMITTED",  self.uid,
                             "input_audio_buffer.commit forwarded to OpenAI")

                    elif msg_type == "session.update":
                        new_mode = data.get("session", {}).get("instructions", "")[:60]
                        _log("MODE CHANGE",      self.uid,
                             f"client sent session.update | preview={new_mode}")

                    elif msg_type == "conversation.item.create":
                        _log("CLIENT MSG",       self.uid,
                             f"conversation item created by client")

                    await self.openai_ws.send(message["text"])

        except Exception as e:
            _log("CLIENT RELAY ERR", self.uid, f"{type(e).__name__}: {e}")
        finally:
            self._active = False

    # ─────────────────────────────────────────────────────────────────────────
    # OpenAI → Client relay
    # ─────────────────────────────────────────────────────────────────────────

    async def _relay_from_openai(self):
        try:
            async for raw in self.openai_ws:
                if not self._active:
                    break

                # Raw bytes (shouldn't happen with text protocol, but guard)
                if isinstance(raw, bytes):
                    await self.client_ws.send_bytes(raw)
                    continue

                try:
                    event = json.loads(raw)
                except Exception:
                    continue

                etype = event.get("type", "")

                # ══ SESSION ══════════════════════════════════════════════════

                if etype == "session.created":
                    sid = event.get("session", {}).get("id", "?")
                    _log("OPENAI SESSION OK",    self.uid,
                         f"OpenAI session created | session_id={sid[:16]}")

                elif etype == "session.updated":
                    _log("OPENAI SESSION UPD",   self.uid,
                         "session.update confirmed by OpenAI")
                    # don't forward — internal
                    continue

                # ══ AUDIO INPUT ═══════════════════════════════════════════════

                elif etype == "input_audio_buffer.speech_started":
                    offset_ms = event.get("audio_start_ms", "?")
                    # Suppress echo: if TTS is still playing or finished within cooldown,
                    # the mic is picking up the speaker — clear the buffer and ignore.
                    in_cooldown = (
                        self._tts_done_ts is not None
                        and (time.monotonic() - self._tts_done_ts) < _TTS_ECHO_COOLDOWN
                    )
                    if self._tts_streaming or in_cooldown:
                        _log("ECHO SUPPRESSED",  self.uid,
                             f"VAD fired during TTS playback/cooldown — clearing buffer | audio_offset={offset_ms}ms")
                        try:
                            await self.openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                        except Exception:
                            pass
                        continue
                    _log("VAD SPEECH START",     self.uid,
                         f"voice activity detected | audio_offset={offset_ms}ms")

                elif etype == "input_audio_buffer.speech_stopped":
                    offset_ms = event.get("audio_end_ms", "?")
                    _log("VAD SPEECH END",       self.uid,
                         f"silence detected | audio_end={offset_ms}ms → committing buffer")

                elif etype == "input_audio_buffer.committed":
                    item_id = event.get("item_id", "?")
                    _log("BUFFER COMMITTED",     self.uid,
                         f"audio buffer committed | item_id={item_id[:12]} → sending to Whisper STT")
                    continue  # don't forward to client

                elif etype == "input_audio_buffer.cleared":
                    _log("BUFFER CLEARED",       self.uid, "audio buffer cleared by OpenAI")
                    continue

                # ══ STT ════════════════════════════════════════════════════════

                elif etype == "conversation.item.created":
                    item      = event.get("item", {})
                    item_type = item.get("type", "?")
                    item_id   = item.get("id", "?")
                    role      = item.get("role", "?")
                    _log("CONV ITEM CREATED",    self.uid,
                         f"type={item_type} | role={role} | id={item_id[:12]}")

                elif etype == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta", "")
                    self._partial_transcript += delta
                    # Log partial every ~20 chars to show live progress
                    if len(self._partial_transcript) % 20 < len(delta):
                        _log("STT PARTIAL",      self.uid,
                             f"[live] \"{self._partial_transcript[-40:]}\"")

                elif etype == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "").strip()
                    self._partial_transcript = ""
                    if transcript:
                        lang = _detect_language(transcript)
                        _log_sep(self.uid, "USER SPOKE")
                        _log("STT COMPLETE",     self.uid,
                             f"[{lang}] \"{transcript}\"")
                        _log("STT SEND TO LLM",  self.uid,
                             f"transcript forwarded to LLM context | len={len(transcript)}chars")
                        self._response_start_ts = time.monotonic()

                elif etype == "conversation.item.input_audio_transcription.failed":
                    err = event.get("error", {})
                    _log("STT FAILED",           self.uid,
                         f"Whisper error: {err.get('message','unknown')}")

                # ══ LLM RESPONSE ══════════════════════════════════════════════

                elif etype == "response.created":
                    resp_id = event.get("response", {}).get("id", "?")
                    _log_sep(self.uid, "LLM PROCESSING")
                    _log("LLM THINKING",         self.uid,
                         f"response started | response_id={resp_id[:16]}")

                elif etype == "response.output_item.added":
                    item      = event.get("item", {})
                    item_type = item.get("type", "?")
                    item_id   = item.get("id", "?")
                    if item_type == "function_call":
                        fn = item.get("name", "?")
                        _log("LLM TOOL DECISION", self.uid,
                             f"LLM decided to call tool '{fn}' | item_id={item_id[:12]}")
                    elif item_type == "message":
                        _log("LLM COMPOSING",    self.uid,
                             f"LLM composing message response | item_id={item_id[:12]}")

                elif etype == "response.output_item.done":
                    item      = event.get("item", {})
                    item_type = item.get("type", "?")
                    _log("LLM OUTPUT DONE",      self.uid,
                         f"output item finished | type={item_type}")

                elif etype == "response.content_part.added":
                    part_type = event.get("part", {}).get("type", "?")
                    _log("LLM CONTENT PART",     self.uid,
                         f"content part started | type={part_type}")

                elif etype == "response.content_part.done":
                    part_type = event.get("part", {}).get("type", "?")
                    _log("LLM PART DONE",        self.uid,
                         f"content part complete | type={part_type}")

                # ══ TOOL CALL ══════════════════════════════════════════════════

                elif etype == "response.function_call_arguments.delta":
                    # Skip — high-frequency streaming of partial JSON args
                    continue

                elif etype == "response.function_call_arguments.done":
                    call_id = event.get("call_id", "")
                    name    = event.get("name", "")
                    raw_args = event.get("arguments", "{}")
                    _log("TOOL ARGS READY",      self.uid,
                         f"fn='{name}' | raw_args={raw_args[:80]}")
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                    await self._handle_tool_call(call_id, name, args)
                    continue

                # ══ AI TRANSCRIPT ══════════════════════════════════════════════

                elif etype == "response.audio_transcript.delta":
                    # Partial AI text — skip to avoid log spam
                    pass

                elif etype == "response.audio_transcript.done":
                    transcript = event.get("transcript", "").strip()
                    if transcript:
                        lang = _detect_language(transcript)
                        elapsed = ""
                        if self._response_start_ts:
                            elapsed = f" | latency={round((time.monotonic()-self._response_start_ts)*1000)}ms"
                        _log_sep(self.uid, "AI SPEAKING")
                        _log("AI RESPONSE TEXT",     self.uid,
                             f"[{lang}] \"{transcript[:140]}\"{elapsed}")

                # ══ TTS AUDIO ══════════════════════════════════════════════════

                elif etype == "response.audio.delta":
                    audio_b64 = event.get("delta", "")
                    if audio_b64:
                        if not self._tts_streaming:
                            self._tts_streaming  = True
                            self._tts_chunk_count = 0
                            _log("TTS AUDIO START",  self.uid,
                                 "OpenAI audio stream → forwarding PCM24k to Flutter")
                            # Clear any mic audio already buffered before TTS started.
                            # This prevents audio captured during the LLM thinking phase
                            # from being transcribed as user input.
                            try:
                                await self.openai_ws.send(json.dumps({"type": "input_audio_buffer.clear"}))
                                _log("ECHO PREEMPT",     self.uid,
                                     "cleared input buffer at TTS start — discarding any pre-TTS mic capture")
                            except Exception:
                                pass
                        self._tts_chunk_count += 1
                        try:
                            await self.client_ws.send_bytes(base64.b64decode(audio_b64))
                        except Exception:
                            pass
                    continue  # don't forward JSON event

                elif etype == "response.audio.done":
                    if self._tts_streaming:
                        self._tts_streaming = False
                        self._tts_done_ts   = time.monotonic()
                        _log("TTS AUDIO DONE",   self.uid,
                             f"audio stream complete | chunks_streamed={self._tts_chunk_count} | echo_cooldown={_TTS_ECHO_COOLDOWN}s")

                elif etype == "response.text.delta":
                    pass  # text-only delta — skip

                elif etype == "response.text.done":
                    text = event.get("text", "").strip()
                    if text:
                        _log("LLM TEXT DONE",    self.uid,
                             f"\"{text[:120]}\"")

                # ══ RESPONSE COMPLETE ══════════════════════════════════════════

                elif etype == "response.done":
                    resp    = event.get("response", {})
                    usage   = resp.get("usage", {})
                    status  = resp.get("status", "?")
                    _log("RESPONSE COMPLETE",    self.uid,
                         f"status={status} | "
                         f"tokens_in={usage.get('input_tokens','?')} "
                         f"tokens_out={usage.get('output_tokens','?')} "
                         f"total={usage.get('total_tokens','?')}")
                    _log_sep(self.uid, "TURN COMPLETE")

                elif etype == "rate_limits.updated":
                    limits = event.get("rate_limits", [])
                    parts  = [f"{r['name']}={r['remaining']}/{r['limit']}" for r in limits]
                    _log("RATE LIMITS",          self.uid,
                         " | ".join(parts))
                    continue  # don't forward

                # ══ ERRORS ═════════════════════════════════════════════════════

                elif etype == "error":
                    err = event.get("error", {})
                    _log("OPENAI ERROR",         self.uid,
                         f"code={err.get('code','?')} | msg={err.get('message','?')}")

                # Forward all remaining events to client
                await self.client_ws.send_text(raw)

        except Exception as e:
            _log("OPENAI RELAY ERR", self.uid, f"{type(e).__name__}: {e}")
        finally:
            self._active = False

    # ─────────────────────────────────────────────────────────────────────────
    # Cross-instance session watchdog
    # ─────────────────────────────────────────────────────────────────────────

    async def _session_watchdog(self, poll_interval: float = 30.0):
        """Poll the DB; self-close if another instance superseded this session.

        Overhead: one indexed SELECT on `users` every `poll_interval` seconds
        per active session (~1 query every 30s). Negligible on db-custom-1-3840.
        Anonymous sessions are skipped because they have no DB row.
        """
        if self.uid == "anonymous":
            return
        from app.services.user_db import get_session_id
        try:
            while self._active:
                await asyncio.sleep(poll_interval)
                if not self._active:
                    break
                try:
                    stored = await get_session_id(self.uid)
                except Exception as e:
                    _log("WATCHDOG DB ERR", self.uid, f"{type(e).__name__}: {e}")
                    continue
                if stored and stored != self.session_id:
                    _log("SESSION SUPERSEDED", self.uid,
                         f"another device took over | db={str(stored)[:12]} mine={self.session_id[:12]} — closing")
                    self._active = False
                    try:
                        await self.client_ws.send_text(
                            json.dumps({"type": "SESSION_KICKED"})
                        )
                    except Exception:
                        pass
                    try:
                        await self.client_ws.close(code=1008)
                    except Exception:
                        pass
                    break
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # Session lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def run(self):
        login_status = "LOGGED IN" if self.uid != "anonymous" else "ANONYMOUS"

        _log_sep(self.uid, "SESSION START")
        _log("WS CONNECT",          self.uid,
             f"user={login_status} | mode='{self.mode}' → db='{self.db_mode}'")
        _log("SESSION ID",          self.uid,
             f"session={self.session_id}")
        _log("ACTIVE SESSIONS",     self.uid,
             f"total_sessions_before={len(_ACTIVE_SESSIONS)}")

        # Persist session to DB
        if self.uid != "anonymous":
            try:
                from app.services.user_db import update_session_id
                await update_session_id(self.uid, self.session_id)
                _log("SESSION PERSISTED",   self.uid, "session_id saved to DB")
            except Exception as e:
                _log("SESSION PERSIST ERR", self.uid, f"{e}")

        # Kick previous session on same uid
        existing = _ACTIVE_SESSIONS.get(self.uid)
        if existing and existing is not self:
            _log("SESSION KICK",     self.uid,
                 "duplicate uid detected — killing previous session")
            existing._active = False
            try:
                await existing.client_ws.send_text(
                    json.dumps({"type": "SESSION_KICKED"})
                )
            except Exception:
                pass

        _ACTIVE_SESSIONS[self.uid] = self
        _log("SESSION REGISTERED",  self.uid,
             f"registered in active sessions | total={len(_ACTIVE_SESSIONS)}")

        # Connect to OpenAI
        connected = await self._connect_to_openai()
        if not connected:
            _log("SESSION ABORT",   self.uid,
                 "OpenAI connection failed — aborting session")
            await self.client_ws.send_text(json.dumps({
                "type":    "error",
                "message": "Failed to connect to AI service. Please try again.",
            }))
            _ACTIVE_SESSIONS.pop(self.uid, None)
            return

        # Configure OpenAI session
        await self._setup_session()

        # Notify client
        await self.client_ws.send_text(
            json.dumps({"type": "connection_success", "message": "Connected to EpicVerse AI"})
        )
        _log("CLIENT NOTIFIED",     self.uid, "connection_success sent to Flutter app")
        _log("SESSION LIVE",        self.uid,
             f"ready — waiting for mic input | mode='{self.db_mode}'")
        _log_sep(self.uid, "WAITING FOR USER")

        # Run both relays concurrently with a cross-instance session watchdog.
        # The watchdog is what makes duplicate-device kick work across Cloud
        # Run instances: if a newer session for this uid lands on a different
        # instance, our in-memory `_ACTIVE_SESSIONS` dict won't see it, but
        # the watchdog will detect the DB mismatch and self-close.
        try:
            await asyncio.gather(
                self._relay_from_client(),
                self._relay_from_openai(),
                self._session_watchdog(),
            )
        except Exception as e:
            _log("SESSION ERROR",   self.uid, f"{type(e).__name__}: {e}")
        finally:
            self._active = False
            if self.openai_ws:
                try:
                    await self.openai_ws.close()
                    _log("OPENAI WS CLOSED", self.uid, "OpenAI websocket closed cleanly")
                except Exception:
                    pass
            if _ACTIVE_SESSIONS.get(self.uid) is self:
                _ACTIVE_SESSIONS.pop(self.uid, None)
            _log_sep(self.uid, "SESSION END")
            _log("SESSION CLOSED",  self.uid,
                 f"all relays stopped | total_turns={self._turn_count} "
                 f"remaining_sessions={len(_ACTIVE_SESSIONS)}")
