# EpicVerse — Technical Architecture Document

**Version:** 1.0.0  
**Last Updated:** May 2026  
**Organisation:** Kriyora Concepts Private Limited  

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Backend Architecture](#3-backend-architecture)
4. [Frontend Architecture](#4-frontend-architecture)
5. [Real-Time Voice Pipeline](#5-real-time-voice-pipeline)
6. [Data Layer](#6-data-layer)
7. [Authentication & Security](#7-authentication--security)
8. [Infrastructure & Deployment](#8-infrastructure--deployment)
9. [AI System Design](#9-ai-system-design)
10. [Echo Cancellation System](#10-echo-cancellation-system)
11. [Multilingual Support](#11-multilingual-support)
12. [Configuration & Environment](#12-configuration--environment)

---

## 1. System Overview

EpicVerse is a multilingual AI voice companion application for a card combo validation game. Users speak card numbers in any language; the AI validates combos against a game database and responds in the user's language with lore-accurate explanations.

### Core Capabilities

- Real-time bidirectional voice conversation (push-to-talk)
- Card combo validation against a PostgreSQL game database
- Multilingual support for 100+ languages (STT, number parsing, response translation)
- Lore-accurate AI responses derived exclusively from game data
- Firebase authentication with invite-code gating
- Cross-platform mobile app (Android + iOS)

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Flutter Mobile App                       │
│              (Android / iOS — Dart + Riverpod)               │
└────────────────────────┬────────────────────────────────────┘
                         │  WebSocket (WSS)
                         │  Binary PCM16 audio up
                         │  Binary PCM24k audio down
                         │  JSON control messages
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               FastAPI Backend — Google Cloud Run             │
│                    (asia-south1 region)                      │
│                                                              │
│  ┌─────────────┐   ┌──────────────┐   ┌─────────────────┐  │
│  │  REST API   │   │  WebSocket   │   │  Realtime       │  │
│  │  (routes)   │   │  Relay       │   │  Session Mgr    │  │
│  └─────────────┘   └──────┬───────┘   └────────┬────────┘  │
│                            │                    │            │
│  ┌─────────────────────────▼────────────────────▼────────┐  │
│  │              Services Layer                            │  │
│  │  user_db.py │ retriever.py │ realtime_service.py      │  │
│  └────────────────────────────────────────────────────────┘  │
│                            │                                  │
│  ┌─────────────────────────▼────────────────────────────┐   │
│  │           Data Layer                                  │   │
│  │   PostgreSQL (Cloud SQL)  │  Redis  │  Excel RAM      │   │
│  └───────────────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │  WSS (OpenAI Realtime Protocol)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              OpenAI Realtime API                             │
│    GPT-4o Realtime │ Whisper STT │ Built-in TTS             │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Backend Architecture

### Technology Stack

| Component | Technology | Version |
|---|---|---|
| Web Framework | FastAPI | Latest |
| Runtime | Python | 3.11 |
| ASGI Server | Uvicorn | Latest |
| WebSocket Client | websockets | Legacy client |
| Database Driver | asyncpg | Latest |
| ORM / Validation | Pydantic v2 | Latest |
| Auth SDK | Firebase Admin | Latest |
| Container | Docker (python:3.11-slim) | — |
| Platform | Google Cloud Run | — |

### Directory Structure

```
backend/
├── app/
│   ├── main.py                 # FastAPI app, lifespan, middleware
│   ├── api/
│   │   └── routes.py           # All REST + WebSocket endpoints
│   ├── core/
│   │   └── config.py           # Settings (Pydantic BaseSettings)
│   └── services/
│       ├── realtime_service.py # OpenAI Realtime WebSocket session
│       ├── retriever.py        # DB queries, Redis cache, Excel fallback
│       ├── user_db.py          # User CRUD, OTP, sessions, soft-delete
│       └── db_pool.py          # asyncpg connection pool management
├── Dockerfile
├── cloudbuild.yaml
└── requirements.txt
```

### Application Startup (Lifespan)

On startup the server performs three initialisation steps in order:

1. **DB Pool** — `get_pool()` creates the asyncpg connection pool to Cloud SQL PostgreSQL
2. **Schema Init** — `init_db()` creates all tables if they do not exist
3. **Data Load** — `load_excel_data()` preloads card combo data into RAM from Excel as a fallback cache

On shutdown, `close_pool()` gracefully closes all database connections.

### Middleware

- **CORSMiddleware** — `allow_origins=["*"]` (all origins, all methods, all headers)
- **OpenAPI** — Swagger UI and ReDoc are disabled in production (`ENV != dev`)

---

## 4. Frontend Architecture

### Technology Stack

| Component | Technology |
|---|---|
| Framework | Flutter (Dart) |
| State Management | Riverpod |
| Audio Recording | `record` package (PCM16, 24kHz mono) |
| Audio Playback | `flutter_pcm_sound` (PCM24k, 24kHz mono) |
| WebSocket | `websocket_channel` |
| Authentication | Firebase Auth (Google Sign-In) |
| HTTP Client | Dio |

### Screen Flow

```
SplashScreen
    └── WelcomeScreen
            ├── LoginScreen
            │       └── DashboardScreen
            │               └── ModeSelectionScreen
            │                       └── CompanionReadyScreen  ← Voice session
            └── CreateProfileScreen
```

### CompanionReadyScreen — Voice Session

The main voice interaction screen manages:
- Lazy WebSocket connection (connects only on first mic tap)
- Push-to-talk mic recording (tap or long-press)
- PCM audio streaming to backend
- PCM audio playback from backend (TTS)
- Echo cancellation — mic force-stopped when TTS audio arrives
- Circular audio visualiser (idle pulse / talking reactive)
- AI talking state animation

---

## 5. Real-Time Voice Pipeline

### Audio Flow — User Speaking

```
Flutter Mic
  │  PCM16 @ 16kHz (recorded at 24kHz, resampled server-side)
  │  Binary WebSocket frames
  ▼
Backend _relay_from_client()
  │  Drops audio if _tts_streaming or within 1.5s cooldown  ← Echo guard
  │  Resamples 16kHz → 24kHz
  │  input_audio_buffer.append → OpenAI
  ▼
OpenAI Whisper STT
  │  conversation.item.input_audio_transcription.completed
  ▼
Backend STT handler
  │  Language detection
  │  Logs transcript
  ▼
OpenAI GPT-4o (system instructions applied)
  │  Extracts card numbers from any language
  │  Calls query_database_for_combo tool
  ▼
Backend _handle_tool_call()
  │  _normalize_number() on character + attribute
  │  query_postgres_database()
  │  Returns avatar_response + revised_scholar_reason JSON
  ▼
GPT-4o composes response
  │  Translates avatar_response into user's language
  ▼
OpenAI TTS (built-in, voice: alloy)
  │  response.audio.delta — PCM24k chunks
  ▼
Backend _relay_from_openai()
  │  On first chunk: clears input buffer (ECHO PREEMPT)
  │  Forwards raw PCM bytes to Flutter via send_bytes()
  ▼
Flutter PCM playback (FlutterPcmSound)
  │  Mic force-stopped on first byte received
  │  RMS volume → circular visualiser
```

### Audio Flow — User Silent (mic released)

When the mic button is released:
1. `_stopRecording()` called in Flutter
2. `{"type": "end"}` sent to backend
3. Backend appends 500ms silence padding → VAD detects end-of-speech → buffer committed to Whisper
4. **Exception:** if TTS is active, backend clears buffer instead of committing (ECHO CLEAR)

### VAD Configuration

```python
"turn_detection": {
    "type":                "server_vad",
    "threshold":           0.5,
    "prefix_padding_ms":   200,
    "silence_duration_ms": 400,
}
```

---

## 6. Data Layer

### PostgreSQL (Cloud SQL)

**Primary tables:**

| Table | Purpose |
|---|---|
| `card_combos` | Game combo data — mode, character, attribute, status, reason |
| `users` | User profiles, sessions, soft-delete state |
| `otp_store` | Time-limited OTPs for email verification |
| `invite_codes` | Invite code pool with usage tracking |
| `user_feedback` | In-app feedback submissions |

**Combo lookup logic (`retriever.py`):**

1. Normalise character and attribute card numbers
2. Check Redis cache (TTL-based)
3. Query PostgreSQL `card_combos` table
4. Fall back to in-RAM Excel data if DB unavailable
5. Return `final_status`, `revised_scholar_reason`, `character_not_in_mode` flag

### Redis Cache

- TTL-based combo result caching
- Key: `combo:{mode}:{character}:{attribute}`
- Reduces DB load for repeated queries in active sessions

### Excel RAM Cache

- Loaded on server startup via `load_excel_data()`
- Used as last-resort fallback if PostgreSQL is unavailable
- Entire combo dataset held in memory

### Character-Mode Validation

`VALID_CHARACTERS_PER_MODE` dict in `retriever.py` maps each game mode to its valid character set. Queries with a character not in the mode return `character_not_in_mode: true` without hitting the DB.

---

## 7. Authentication & Security

### Firebase Authentication

- Users authenticate via Firebase (Google Sign-In or email/password)
- Firebase UID is used as the primary user identifier
- All protected endpoints verify the Firebase ID token via `firebase_admin.auth.verify_id_token()`

### Invite Code System

- New accounts require a valid invite code (`EPIC-XXXXXX` format)
- Codes are single-use; marked as consumed on successful registration
- OTP email verification via SendGrid before account activation

### Session Management

- Active sessions stored in PostgreSQL `users` table
- Cross-instance session conflict detection — only one active session per user
- Kicked users receive `SESSION_KICKED` error event over WebSocket

### Soft Delete

- Account deletion is a 30-day soft delete
- `deleted_at` timestamp set; account fully purged after 30 days
- User can cancel deletion by signing back in within 30 days

### API Security

- OpenAPI schema, Swagger UI, and ReDoc disabled in production
- CORS allows all origins (intended for mobile client)
- No rate limiting currently implemented at the application layer (Cloud Run handles concurrency)

---

## 8. Infrastructure & Deployment

### Google Cloud Run

| Parameter | Value |
|---|---|
| Region | `asia-south1` (Mumbai) |
| Service | `epicverse-backend` |
| Min instances | 1 (always warm) |
| Max instances | 20 |
| Memory | 2 GB |
| CPUs | 2 |
| Max concurrency | 80 |
| Timeout | Default (Cloud Run) |

### Cloud SQL

- PostgreSQL instance bound to Cloud Run via Cloud SQL proxy
- Connection via Unix socket (`/cloudsql/...`)

### CI/CD Pipeline (Cloud Build)

```yaml
Steps:
  1. docker build  → builds image from Dockerfile
  2. docker push   → pushes to Artifact Registry
  3. gcloud run deploy → deploys to Cloud Run with secrets
```

**Secrets injected at deploy time (Google Secret Manager):**
- `DATABASE_URL`
- `OPENAI_API_KEY`
- `REDIS_URL`
- `SENDGRID_API_KEY`
- `FIREBASE_PROJECT_ID`

### Docker Image

- Base: `python:3.11-slim`
- System deps: `build-essential`, `ffmpeg` (audio resampling)
- Entrypoint: `uvicorn app.main:app --host 0.0.0.0 --port 8080`

### Production URL

```
https://epicverse-backend-721191424605.asia-south1.run.app
```

---

## 9. AI System Design

### Model

- **OpenAI Realtime API** — `gpt-4o-realtime-preview`
- Voice: `alloy`
- Modalities: `text` + `audio`
- STT: Whisper-1 (built-in transcription)

### System Prompt Architecture

The AI operates in two strict modes only:

**MODE 1 — Combo Check**
- Extracts two card numbers from user speech (any language/format)
- Converts to digit strings before tool call
- Calls `query_database_for_combo` tool
- Speaks only the `avatar_response` field from the tool result
- Translates response into user's language if non-English

**MODE 2 — Reason**
- Triggered when user asks "why" or "how" after a combo check
- Does NOT call the tool again
- Reads `revised_scholar_reason` from most recent tool result
- Translates into user's language if non-English

**Strict rules enforced:**
- Never says "valid" or "invalid" without calling the tool
- Never generates its own explanations
- Never answers from memory or conversation history
- Requires TWO card numbers before tool call; asks for second if only one given
- Responds in the exact language the user spoke

### Tool Definition

```json
{
  "name": "query_database_for_combo",
  "parameters": {
    "mode":      "string — game mode e.g. 'OriginArc (Balakanda)'",
    "character": "string — character card number",
    "attribute": "string — attribute card number (25+)"
  }
}
```

### Avatar Response Pool

Pre-written lore-flavoured messages injected into `avatar_response`:

- **Valid:** "Ah... rightly placed. Valid." (11 variants)
- **Invalid:** "Hmm... invalid combo." (11 variants)
- **Excluded:** "Close! Valid, but excluded yet." (11 variants)
- **Wrong mode:** "Wrong mode. This character has no part in this chapter." (5 variants)

---

## 10. Echo Cancellation System

The echo problem: when the AI speaks through the device speaker, the open microphone picks up the audio and feeds it back to Whisper as user speech.

### Four-Layer Defence

**Layer 1 — Flutter mic kill (source suppression)**
When the first TTS audio byte arrives, Flutter immediately force-stops the microphone recorder and cancels the stream subscription. No audio is sent while the AI is speaking.

**Layer 2 — Backend ECHO PREEMPT**
At the start of the first TTS chunk (`response.audio.delta`), the backend sends `input_audio_buffer.clear` to OpenAI, discarding any audio already buffered before TTS started.

**Layer 3 — Backend audio drop**
While `_tts_streaming` is True or within 1.5s cooldown after TTS ends, all incoming mic audio bytes from the client are silently dropped without forwarding to OpenAI.

**Layer 4 — VAD suppress + ECHO CLEAR**
If `input_audio_buffer.speech_started` fires during TTS or cooldown, the backend sends `input_audio_buffer.clear`. If the mic button is released during TTS, the buffer is cleared instead of committed.

### Constants

```python
_TTS_ECHO_COOLDOWN = 1.5  # seconds after TTS ends to continue suppressing
```

---

## 11. Multilingual Support

### Speech-to-Text

OpenAI Whisper auto-detects and transcribes **99 languages** natively. No configuration required.

### Number Word Normalisation

`_normalize_number()` in `realtime_service.py` converts spoken number words to integers:

1. Try direct integer parse
2. Strip common prefixes ("number ", "card ", "no. ", "#")
3. Lookup in `_NUMBER_WORDS` dict (150+ entries: English, Tamil, Hindi, Malayalam)
4. Fall back to `word2number` library (English extended)
5. Return `None` if unrecognisable → triggers "ask to repeat"

For all other languages, the LLM itself converts number words to digit strings before calling the tool.

### Response Translation

The system prompt instructs GPT-4o to:
- Detect the language of every user message
- Translate `avatar_response` and `revised_scholar_reason` into that language before speaking
- Never output English to a non-English user
- Never mix languages

GPT-4o supports translation into **100+ languages** natively.

---

## 12. Configuration & Environment

### Environment Variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_REALTIME_MODEL` | Realtime model ID (default: `gpt-4o-realtime-preview`) |
| `REDIS_URL` | Redis connection URL |
| `SENDGRID_API_KEY` | Email OTP delivery |
| `FIREBASE_PROJECT_ID` | Firebase project for auth |
| `ENV` | `dev` enables Swagger UI; anything else = production |
| `SECRET_KEY` | JWT / session signing key |

### Settings Class

`app/core/config.py` uses Pydantic `BaseSettings` — all variables are loaded from environment with type validation. The async database URL is derived automatically from `DATABASE_URL` by replacing the scheme prefix.

---

*Document maintained by Kriyora engineering. For questions: support@kriyora.com*
