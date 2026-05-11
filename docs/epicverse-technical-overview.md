# EpicVerse — Technical Overview

**Prepared by:** Kriyora Engineering Team
**Date:** May 2026
**Confidential — Internal Use Only**

---

## Part 1 — Game Mode Data

### How the Game Data is Structured

EpicVerse is built around a card-based game where players combine a character card with an attribute card to check if it forms a valid combo. The game is divided into seven modes, each representing a different chapter of the story. Every mode has its own set of characters that are relevant to that chapter, and only those characters are allowed in that mode.

All of this data lives in a PostgreSQL database table called `card_combos`. Each row in this table represents one possible combination — it stores the game mode, the character card number, the attribute card number, and whether that combination is valid, invalid, or excluded. Along with the result, each record also holds a lore-accurate explanation written in the scholar's voice, a reference to the original Valmiki source, the Kanda and Shloka it comes from, and a short summarized explanation.

### The Seven Game Modes

The game currently has seven modes. Each mode only recognises specific character cards. If a user asks about a character that does not belong to the current mode, the system immediately tells them so without even querying the database.

Mode 1 supports character cards 1, 2, 3, 5, 6, 7, 8, 9, 10, 23, and 24.
Mode 2 supports character cards 1, 2, 3, 5, 6, 8, 9, 10, 19, and 24.
Mode 3 supports character cards 1, 2, 3, 5, 11, 12, 15, and 23.
Mode 4 supports character cards 1, 2, 3, 4, 15, 17, 18, and 21.
Mode 5 supports character cards 2, 4, 11, 13, 14, 18, 20, and 21.
Mode 6 supports character cards 1, 3, 4, 11, 13, 14, 18, 20, 21, and 22.
Mode 7 supports character cards 1, 2, 3, 4, 5, 6, and 13.

### How a Combo Check Works

When a user speaks to the app and asks whether two cards form a combo, the system goes through a clear sequence of steps. First it extracts the two card numbers from whatever the user said, in any language or format. Then it checks whether the character card is valid for the current game mode. If the character does not belong to that mode, the AI responds with a lore-accurate message explaining that this character has no role in this chapter, and the process stops there.

If the character is valid for the mode, the system looks up the combination in the database and gets back one of three results. A result of Valid means the combination is a recognised and active game combo. A result of Invalid means the combination does not work in the game. A result of Excluded means the combination is technically valid but has been excluded from current gameplay. The AI then reads out the scholar's explanation that is stored alongside the result.

### How the Data is Served

The system uses a three-layer caching strategy to make sure data is always available quickly, even if the database has a temporary issue. The first layer is Redis, which holds recently looked-up combos in memory with a time-based expiry. If the result is not in Redis, the system queries PostgreSQL directly. If for any reason the database is not reachable, the system falls back to an in-memory copy of the Excel data that was loaded into RAM when the server started up. This means the app can keep working even during a database blip.

---

## Part 2 — Technology Stack

### Frontend — Mobile Application

The mobile app is built using Flutter, which is Google's cross-platform framework written in Dart. This allows us to maintain a single codebase that works on both Android and iOS. State across the app is managed using Riverpod, which is a reactive state management library for Flutter.

For network communication, the app uses two channels. Regular API calls such as login, profile fetch, and feedback submission go through the Dio HTTP client. The real-time voice session runs over a WebSocket connection managed by the web_socket_channel package.

Audio recording is handled by the record package, which captures the user's voice as raw PCM audio at 16kHz mono. The AI's response audio, which arrives from the backend as 24kHz PCM, is played back using flutter_pcm_sound. Firebase is used for user authentication, supporting both Google Sign-In and email-based login. The permission_handler package takes care of requesting microphone and storage permissions from the device.

For the user interface, the app uses google_fonts for typography, the animations package for smooth screen transitions, and intl for internationalisation support. Profile pictures are selected using image_picker. Sessions are stored locally using shared_preferences, and each session is identified by a unique ID generated with the uuid package.

### Backend — Server Application

The backend is a Python application built with FastAPI, a modern and high-performance web framework. It runs on Uvicorn, which is an ASGI server that handles asynchronous requests efficiently. The server is hosted on Google Cloud Run in the Mumbai region, which keeps latency low for Indian users.

Database access is done through asyncpg, which is an asynchronous PostgreSQL driver that works without blocking the server. All settings and environment variables are validated at startup using pydantic-settings. The Firebase Admin SDK is used to verify user tokens on every protected request.

For the AI voice pipeline, the server acts as a bridge between the Flutter app and the OpenAI Realtime API. It uses the websockets library to maintain a persistent connection to OpenAI. Audio resampling from 16kHz to 24kHz is done using numpy. Game data is loaded from Excel files at startup using pandas and openpyxl, and cached in Redis using the redis client. The openai package is used for any non-realtime AI calls.

For infrastructure-related tasks, the server uses google-cloud-storage for file operations, google-cloud-logging for structured cloud logs, and sendgrid for sending OTP emails during account verification.

### AI and Voice

The heart of the voice experience is the OpenAI Realtime API running GPT-4o. This single WebSocket connection handles everything — speech recognition, language understanding, tool execution, and voice response. There is no separate STT or TTS service to coordinate; it all happens within one session.

Speech-to-text is powered by OpenAI Whisper-1, which is built into the Realtime API. It automatically detects the user's language and transcribes their speech across 99 languages without any configuration. The AI response is spoken back using the built-in TTS with the alloy voice. If the user spoke in a language other than English, GPT-4o translates the response into that language before speaking it, covering over 100 languages natively.

### Cloud Infrastructure

The backend runs on Google Cloud Run, a fully managed serverless platform. It is configured to always keep at least one instance running so there is no cold start delay for users. It can scale up to 20 instances automatically under load, with each instance able to handle 80 concurrent connections, 2 virtual CPUs, and 2 gigabytes of memory.

The PostgreSQL database is hosted on Google Cloud SQL and connects to Cloud Run through a secure Unix socket. Deployment is automated through Google Cloud Build, which builds the Docker image, pushes it to Google Artifact Registry, and deploys it to Cloud Run whenever a new version is released. All sensitive credentials such as the OpenAI API key, database URL, and SendGrid key are stored in Google Secret Manager and injected into the server at deploy time. The source code is version controlled on GitHub.

The live backend is accessible at:
https://epicverse-backend-721191424605.asia-south1.run.app

---

*Kriyora Concepts Private Limited — Confidential*
*For queries: support@kriyora.com*
