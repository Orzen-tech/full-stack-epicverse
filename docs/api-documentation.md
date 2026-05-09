# EpicVerse — API Documentation

**Version:** 1.0.0  
**Base URL:** `https://epicverse-backend-721191424605.asia-south1.run.app`  
**API Prefix:** `/api/v1`  
**Last Updated:** May 2026  

---

## Table of Contents

1. [Overview](#1-overview)
2. [Authentication](#2-authentication)
3. [Health & Utility](#3-health--utility)
4. [Auth Endpoints](#4-auth-endpoints)
5. [User Endpoints](#5-user-endpoints)
6. [WebSocket — Real-Time Voice Session](#6-websocket--real-time-voice-session)
7. [Legal Endpoints](#7-legal-endpoints)
8. [FAQ & Feedback](#8-faq--feedback)
9. [Admin Endpoints](#9-admin-endpoints)
10. [Error Reference](#10-error-reference)

---

## 1. Overview

All REST endpoints are prefixed with `/api/v1`.  
Protected endpoints require a Firebase ID token in the `Authorization` header.

```
Authorization: Bearer <firebase_id_token>
```

All request and response bodies are JSON unless noted otherwise.

---

## 2. Authentication

EpicVerse uses Firebase Authentication. The client obtains a Firebase ID token after sign-in and includes it in every protected request.

**Token verification:** The backend calls `firebase_admin.auth.verify_id_token(token)` and extracts the `uid`.

---

## 3. Health & Utility

### GET /

Returns a welcome message.

**Response**
```json
{
  "message": "Welcome to the Game Guide AI Voice Agent Backend"
}
```

---

### GET /health

Returns server health status.

**Response**
```json
{
  "status": "ok"
}
```

---

### GET /delete-account

Returns a branded HTML page with account deletion instructions. Required by Google Play Store for apps with account creation.

**Response:** `text/html`

---

### GET /modes

Returns available game modes from the database.

**Response**
```json
{
  "available_modes": [
    "OriginArc (Balakanda)",
    "OriginArc (Ayodhyakanda)",
    "..."
  ]
}
```

---

## 4. Auth Endpoints

### POST /api/v1/auth/validate-invite

Validates an invite code before registration.

**Request**
```json
{
  "invite_code": "EPIC-XXXXXX"
}
```

**Response — Valid**
```json
{
  "valid": true,
  "message": "Invite code is valid"
}
```

**Response — Invalid**
```json
{
  "valid": false,
  "message": "Invalid or already used invite code"
}
```

---

### POST /api/v1/auth/send-otp

Sends a 6-digit OTP to the user's email address for verification.

**Request**
```json
{
  "email": "user@example.com"
}
```

**Response**
```json
{
  "message": "OTP sent successfully"
}
```

**Errors**

| Code | Reason |
|---|---|
| `400` | Email already registered |
| `500` | SendGrid delivery failure |

---

### POST /api/v1/auth/verify-otp

Verifies the OTP entered by the user.

**Request**
```json
{
  "email": "user@example.com",
  "otp": "123456"
}
```

**Response — Success**
```json
{
  "verified": true,
  "message": "Email verified successfully"
}
```

**Response — Failure**
```json
{
  "verified": false,
  "message": "Invalid or expired OTP"
}
```

---

### POST /api/v1/auth/sync-user

Creates or updates a user record after Firebase authentication. Called on every login.

**Headers:** `Authorization: Bearer <token>` *(required)*

**Request**
```json
{
  "display_name": "Arjun Kumar",
  "email": "arjun@example.com",
  "invite_code": "EPIC-XXXXXX",
  "profile_picture": "<base64_encoded_jpeg_string>"
}
```

> `profile_picture` is optional. `invite_code` is required for new users.

**Response**
```json
{
  "message": "User synced successfully",
  "user_id": "firebase_uid_here"
}
```

**Errors**

| Code | Reason |
|---|---|
| `400` | Invalid or already used invite code |
| `401` | Invalid Firebase token |
| `403` | Account pending deletion (within 30-day grace period) |

---

## 5. User Endpoints

### GET /api/v1/user/profile

Returns the authenticated user's profile.

**Headers:** `Authorization: Bearer <token>` *(required)*

**Response**
```json
{
  "id": "firebase_uid",
  "display_name": "Arjun Kumar",
  "email": "arjun@example.com",
  "profile_picture": "<base64_string_or_null>",
  "created_at": "2026-01-15T10:30:00Z",
  "last_login": "2026-05-09T08:00:00Z"
}
```

---

### PUT /api/v1/user/profile

Updates the authenticated user's display name and/or profile picture.

**Headers:** `Authorization: Bearer <token>` *(required)*

**Request**
```json
{
  "display_name": "New Name",
  "profile_picture": "<base64_string>"
}
```

Both fields are optional — include only what needs updating.

**Response**
```json
{
  "message": "Profile updated successfully"
}
```

---

### DELETE /api/v1/user/delete-account

Initiates a 30-day soft delete. The account is fully purged after 30 days unless the user signs back in.

**Headers:** `Authorization: Bearer <token>` *(required)*

**Response**
```json
{
  "message": "Account deletion initiated. Your account will be permanently deleted in 30 days."
}
```

---

### POST /api/v1/user/cancel-deletion

Cancels a pending deletion request. Called automatically when a user with a pending deletion logs in.

**Headers:** `Authorization: Bearer <token>` *(required)*

**Response**
```json
{
  "message": "Account deletion cancelled. Your account has been restored."
}
```

---

## 6. WebSocket — Real-Time Voice Session

### WS /api/v1/ws/{session_id}

Establishes a bidirectional real-time voice session.

**URL Parameters**

| Parameter | Type | Description |
|---|---|---|
| `session_id` | string | Unique session identifier (UUID) |

**Query Parameters**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token` | string | Yes | Firebase ID token |
| `game_mode` | string | Yes | Game mode name e.g. `OriginArc (Balakanda)` |

**Example**
```
wss://epicverse-backend-721191424605.asia-south1.run.app/api/v1/ws/abc123?token=<firebase_token>&game_mode=OriginArc%20(Balakanda)
```

---

### Message Protocol

#### Client → Server (Binary)

Raw PCM audio bytes. Format:
- Encoding: PCM 16-bit signed little-endian
- Sample rate: 16kHz (resampled to 24kHz server-side)
- Channels: Mono

Send continuously while the mic button is held.

---

#### Client → Server (JSON Control Messages)

**End of speech** — sent when mic button is released
```json
{"type": "end"}
```

**Ping / keepalive**
```json
{"type": "ping"}
```

**Stop wake word**
```json
{"type": "stop_wakeword"}
```

**Start wake word**
```json
{"type": "start_wakeword"}
```

---

#### Server → Client (Binary)

Raw PCM audio bytes — the AI's TTS response. Format:
- Encoding: PCM 16-bit signed little-endian
- Sample rate: 24kHz
- Channels: Mono

Feed directly to `FlutterPcmSound.feed()`.

---

#### Server → Client (JSON Events)

The server forwards relevant OpenAI Realtime API events to the client.

**Speech detected**
```json
{"type": "input_audio_buffer.speech_started"}
```

**Speech ended**
```json
{"type": "input_audio_buffer.speech_stopped"}
```

**STT transcript complete**
```json
{
  "type": "conversation.item.input_audio_transcription.completed",
  "transcript": "one and twenty nine"
}
```

**AI response complete**
```json
{
  "type": "response.done",
  "response": {
    "status": "completed",
    "usage": {
      "input_tokens": 1168,
      "output_tokens": 341,
      "total_tokens": 1509
    }
  }
}
```

**Error**
```json
{
  "type": "error",
  "code": "SESSION_KICKED",
  "message": "You have been logged in from another device."
}
```

---

### Session Lifecycle

```
Client                          Backend                      OpenAI
  │                                │                            │
  │── WS connect (token, mode) ──► │                            │
  │                                │── WSS connect ────────────►│
  │                                │◄─ session.created ─────────│
  │                                │── session.update ─────────►│
  │                                │   (system instructions,    │
  │                                │    VAD config, tools)      │
  │                                │◄─ session.updated ─────────│
  │                                │                            │
  │── PCM audio bytes ───────────► │── audio.append ───────────►│
  │── {"type":"end"} ────────────► │── silence padding ────────►│
  │                                │◄─ speech_stopped ──────────│
  │                                │◄─ buffer.committed ────────│
  │                                │◄─ STT transcript ──────────│
  │                                │◄─ response.created ────────│
  │                                │◄─ tool call ───────────────│
  │                                │── DB query ────────────────│
  │                                │── tool result ────────────►│
  │                                │◄─ audio.delta (TTS) ───────│
  │◄─ PCM audio bytes ─────────────│                            │
  │                                │◄─ audio.done ──────────────│
  │                                │◄─ response.done ───────────│
  │◄─ {"type":"response.done"} ────│                            │
```

---

### Tool: query_database_for_combo

Called internally by the AI when two card numbers are identified. Not callable directly by the client.

**Parameters**

| Field | Type | Description |
|---|---|---|
| `mode` | string | Exact game mode name |
| `character` | string | Character card number as digit string |
| `attribute` | string | Attribute card number as digit string |

**Tool Result (returned to LLM)**

```json
{
  "final_status": "Valid",
  "revised_scholar_reason": "Rama enters Tataka's forest in Sarga 26...",
  "avatar_response": "Ah... rightly placed. Valid.",
  "ask_to_repeat": false
}
```

| Field | Description |
|---|---|
| `final_status` | `"Valid"`, `"Invalid"`, `"Excluded"`, or `null` |
| `revised_scholar_reason` | Lore explanation from the database |
| `avatar_response` | Pre-written short response for the AI to speak |
| `ask_to_repeat` | `true` if card numbers could not be parsed |

---

## 7. Legal Endpoints

### GET /api/v1/legal/privacy

Returns the Privacy Policy content.

**Response**
```json
{
  "title": "Privacy Policy",
  "content": "EpicVerse Privacy Policy\n\n1. Introduction\n..."
}
```

---

### GET /api/v1/legal/terms

Returns the Terms of Service content.

**Response**
```json
{
  "title": "Terms of Service",
  "content": "EpicVerse Terms of Service\n\n..."
}
```

---

## 8. FAQ & Feedback

### GET /api/v1/faq

Returns the FAQ list.

**Response**
```json
{
  "faqs": [
    {
      "question": "How do I check a combo?",
      "answer": "Hold the mic button and say both card numbers..."
    }
  ]
}
```

---

### POST /api/v1/feedback

Submits user feedback.

**Headers:** `Authorization: Bearer <token>` *(required)*

**Request**
```json
{
  "rating": 5,
  "message": "Love the multilingual support!"
}
```

**Response**
```json
{
  "message": "Feedback submitted successfully"
}
```

---

## 9. Admin Endpoints

> Admin endpoints require a valid admin token. Not exposed in production Swagger UI.

### GET /api/v1/admin/feedback

Returns all user feedback submissions.

**Headers:** `Authorization: Bearer <admin_token>` *(required)*

**Response**
```json
{
  "results": [
    {
      "user_id": "uid_here",
      "rating": 5,
      "message": "Great app!",
      "created_at": "2026-05-09T10:00:00Z"
    }
  ]
}
```

---

### DELETE /api/v1/admin/purge-deleted-accounts

Permanently deletes all accounts where `deleted_at` is older than 30 days.

**Headers:** `Authorization: Bearer <admin_token>` *(required)*

**Response**
```json
{
  "message": "Purged 3 accounts"
}
```

---

## 10. Error Reference

### HTTP Error Codes

| Code | Meaning |
|---|---|
| `400` | Bad request — missing or invalid parameters |
| `401` | Unauthorized — missing or invalid Firebase token |
| `403` | Forbidden — account pending deletion or access denied |
| `404` | Not found |
| `409` | Conflict — e.g. email already registered |
| `500` | Internal server error |

### WebSocket Error Events

| Code | Meaning |
|---|---|
| `SESSION_KICKED` | User logged in from another device |
| `SESSION_INVALID` | Session token expired or invalid |
| `OPENAI_ERROR` | OpenAI API error — check backend logs |

### Standard Error Response Body

```json
{
  "detail": "Human-readable error description"
}
```

---

## Appendix — Audio Format Reference

| Direction | Format | Sample Rate | Channels | Bit Depth |
|---|---|---|---|---|
| Client → Server | PCM | 16kHz | Mono | 16-bit signed LE |
| Server → Client | PCM | 24kHz | Mono | 16-bit signed LE |
| OpenAI internal | PCM | 24kHz | Mono | 16-bit signed LE |

---

*Document maintained by Kriyora engineering. For questions: support@kriyora.com*
