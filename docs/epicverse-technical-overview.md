# EpicVerse — Technical Overview Document

**Prepared by:** Kriyora Engineering Team  
**Date:** May 2026  
**Confidential — Internal Use Only**

---

## PART 1 — GAME MODE DATA

### Overview

EpicVerse validates card combos across **7 game modes**. Each mode represents a chapter of the story and has its own set of valid characters and combo rules stored in the database.

---

### Game Modes

| Mode | Name | Valid Character Cards |
|---|---|---|
| Mode 1 | — | 1, 2, 3, 5, 6, 7, 8, 9, 10, 23, 24 |
| Mode 2 | — | 1, 2, 3, 5, 6, 8, 9, 10, 19, 24 |
| Mode 3 | — | 1, 2, 3, 5, 11, 12, 15, 23 |
| Mode 4 | — | 1, 2, 3, 4, 15, 17, 18, 21 |
| Mode 5 | — | 2, 4, 11, 13, 14, 18, 20, 21 |
| Mode 6 | — | 1, 3, 4, 11, 13, 14, 18, 20, 21, 22 |
| Mode 7 | — | 1, 2, 3, 4, 5, 6, 13 |

---

### Database Schema — `card_combos` Table

Each combo record in the PostgreSQL database has the following structure:

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL | Auto-incremented primary key |
| `gameplay_mode` | TEXT | Game mode name |
| `character` | TEXT | Character name |
| `character_card_number` | INTEGER | Character card number |
| `attribute` | TEXT | Attribute name |
| `attribute_card_no` | INTEGER | Attribute card number (25+) |
| `final_segment` | TEXT | Game segment reference |
| `final_status` | TEXT | `Valid`, `Invalid`, or `Excluded` |
| `revised_scholar_reason` | TEXT | Lore-accurate explanation |
| `valmiki_reference_anchor` | TEXT | Source reference |
| `kanda` | TEXT | Story chapter (Kanda) |
| `shloka` | TEXT | Verse reference |
| `explanation_summarized` | TEXT | Short summary |
| `created_at` | TIMESTAMPTZ | Record creation timestamp |

---

### Combo Validation Logic

When a user asks "is card X and card Y a combo?", the system:

1. **Extracts** two card numbers from the user's speech (any language)
2. **Validates** the character card belongs to the current game mode
3. **Queries** the `card_combos` table for the combination
4. **Returns** one of three statuses:

| Status | Meaning |
|---|---|
| `Valid` | The combination is a valid game combo |
| `Invalid` | The combination is not valid |
| `Excluded` | Valid combination but excluded from current gameplay |

5. **Responds** with a lore-accurate explanation from `revised_scholar_reason`

---

### Data Caching Strategy

```
User Query
    │
    ▼
Redis Cache (fast lookup, TTL-based)
    │ cache miss
    ▼
PostgreSQL card_combos table
    │ DB unavailable
    ▼
Excel RAM Cache (preloaded at server startup)
```

If a character card number does not belong to the current game mode, the system returns a lore-accurate "wrong mode" message without querying the database.

---

---

## PART 2 — TECHNOLOGY STACK

---

### Frontend — Flutter Mobile App

**Platform:** Android + iOS  
**Language:** Dart

#### Packages & Tools

| Package | Version | Purpose |
|---|---|---|
| `flutter` | SDK | Core mobile framework |
| `flutter_riverpod` | ^3.3.1 | State management |
| `dio` | ^5.9.2 | HTTP client for REST API calls |
| `web_socket_channel` | ^3.0.3 | WebSocket connection to backend |
| `record` | ^6.2.0 | Mic recording — PCM16, 24kHz mono |
| `flutter_pcm_sound` | ^3.3.3 | AI voice playback — PCM24k, 24kHz |
| `audioplayers` | ^6.6.0 | General audio playback |
| `speech_to_text` | ^7.0.0 | Wake word / local STT support |
| `firebase_core` | ^4.6.0 | Firebase initialisation |
| `firebase_auth` | ^6.3.0 | User authentication |
| `permission_handler` | ^12.0.1 | Mic + storage permissions |
| `shared_preferences` | ^2.5.4 | Local session storage |
| `uuid` | ^4.3.3 | Session ID generation |
| `image_picker` | ^1.2.1 | Profile picture selection |
| `google_fonts` | ^8.0.2 | Typography |
| `animations` | ^2.1.1 | Transition animations |
| `intl` | ^0.20.2 | Internationalisation |
| `flutter_launcher_icons` | ^0.14.4 | App icon generation |

#### Audio Format

| Direction | Format | Sample Rate | Channels |
|---|---|---|---|
| Mic → Backend | PCM 16-bit | 16 kHz | Mono |
| Backend → Speaker | PCM 16-bit | 24 kHz | Mono |

---

### Backend — FastAPI Server

**Language:** Python 3.11  
**Hosting:** Google Cloud Run (asia-south1 / Mumbai)

#### Packages & Tools

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | 0.103.2 | Web framework + REST API + WebSocket |
| `uvicorn` | 0.23.2 | ASGI server |
| `websockets` | 12.0 | WebSocket client (OpenAI Realtime bridge) |
| `asyncpg` | 0.29.0 | Async PostgreSQL driver |
| `pydantic-settings` | ≥2.0.0 | Settings + environment variable validation |
| `openai` | ≥1.3.5 | OpenAI API client |
| `firebase-admin` | 6.2.0 | Firebase token verification |
| `redis` | ≥5.0.0 | Redis cache client |
| `numpy` | ≥1.26.0 | PCM audio resampling (16kHz → 24kHz) |
| `pandas` | 2.1.1 | Excel data loading |
| `openpyxl` | 3.1.2 | Excel file parsing |
| `python-multipart` | 0.0.6 | File upload handling |
| `httpx` | ≥0.24.0 | Async HTTP client |
| `google-cloud-storage` | 2.11.0 | GCS file storage |
| `google-cloud-logging` | 3.6.0 | Cloud logging |
| `python-dotenv` | 1.0.0 | Local environment variables |

---

### AI & Voice Services

| Service | Provider | Purpose |
|---|---|---|
| **LLM** | OpenAI GPT-4o Realtime | Language understanding + combo validation logic |
| **STT** | OpenAI Whisper-1 | Speech-to-text (99 languages, auto-detect) |
| **TTS** | OpenAI Realtime (voice: alloy) | AI voice response |
| **Translation** | GPT-4o native | Response translation into user's language (100+ languages) |

---

### Infrastructure & Cloud Services

| Service | Provider | Purpose |
|---|---|---|
| **App Hosting** | Google Cloud Run | Serverless backend — auto-scales 1 to 20 instances |
| **Database** | Google Cloud SQL (PostgreSQL) | Primary game data + user data |
| **Cache** | Redis | Fast combo lookup cache |
| **Container Registry** | Google Artifact Registry | Docker image storage |
| **CI/CD Pipeline** | Google Cloud Build | Automated build + deploy on code push |
| **Secrets** | Google Secret Manager | API keys, DB credentials |
| **Authentication** | Firebase Authentication | User login (Google Sign-In + Email) |
| **Email** | SendGrid | OTP delivery for email verification |
| **Source Control** | GitHub | Code repository |

---

### Cloud Run Configuration

| Parameter | Value |
|---|---|
| Region | asia-south1 (Mumbai) |
| Min Instances | 1 (always warm) |
| Max Instances | 20 |
| Memory | 2 GB |
| vCPUs | 2 |
| Max Concurrency | 80 connections |

---

### Production Endpoint

```
https://epicverse-backend-721191424605.asia-south1.run.app
```

---

*Kriyora Concepts Private Limited — Confidential*  
*For queries: support@kriyora.com*
