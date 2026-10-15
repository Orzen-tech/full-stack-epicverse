# Additive MFA backend (local implementation, not deployed)

`mfa_additive.patch` turns the **deployed** backend (commit `d733776`) into the additive release described in the MFA plan:
it adds the server-side MFA routes (`/auth/session-status`, `/auth/mfa/challenge|verify|logout`,
`/user/mfa/enable-request|enable-confirm|disable`), closes the legacy `/user/update-mfa` bypass, and carries the Excel-loader
fix. It also modifies `user_db.py` so that `/sync-user` can no longer change MFA state or the verified email of an existing user (it updates only display name, language and picture, and new accounts always start with MFA off). It does **not** touch the OTP rate limiter, `main.py`, config or dependencies, and needs no database change
(the MFA tables already exist in production).

```bash
bash scripts/mfa_additive/build_tree.sh <empty-dir>        # d733776 + patch  ->  <empty-dir>/backend
bash scripts/mfa_additive/run_local_tests.sh               # backend tests + Flutter contract tests (needs Docker + Flutter)
bash scripts/mfa_additive/run_local_tests.sh --no-flutter  # backend tests only
```

`run_local_tests.sh` is local only: an internet-less Docker network, a throwaway Postgres, a fake identity service and
captured (never sent) email, with fake users. Everything it creates is removed on exit. `harness/` is test infrastructure
and is never part of a deployment. The deployed `backend/` folder on `main` is the newer, unreleased code (`e121583`) and is
**not** what this patch targets.

## Phase 1 server-side MFA enforcement (second patch, local implementation, not deployed)

`mfa_enforcement.patch` is applied **after** the verified `mfa_additive.patch`, which is left byte-for-byte unchanged
(`build_tree_enforcement.sh` refuses to build if its SHA-256 is not `edab0188…ba2`; `build_tree.sh` is also unchanged).

```bash
bash scripts/mfa_additive/build_tree_enforcement.sh <empty-dir>   # d733776 + mfa_additive.patch + mfa_enforcement.patch
bash scripts/mfa_additive/run_local_tests.sh --no-flutter          # now also runs the enforcement tests last
bash scripts/mfa_additive/run_local_tests.sh --no-enforcement      # the original suites only
```

What it adds (no database migration, no new dependency, no Redis):

* `MFA_ENFORCEMENT_MODE` = `off` (default, existing behaviour) | `log` (observe only) | `canary` (enforce only the exact UIDs in
  `MFA_ENFORCE_UIDS`, observe the rest) | `enforce`. Anything else, an empty value, `canary` without exact UIDs, a mode other than `off`
  without `MFA_OTP_HASH_SECRET`, or `MFA_WS_FAILCLOSED_SECONDS` outside 30-600 stops the process at startup.
* One policy object (`app/api/dependencies.py`) used by the HTTP routes, the WebSocket handshake and the watchdog. Enforced
  surfaces: the voice WebSocket, `POST /process-audio`, `POST /auth/update-session`, `GET /auth/check-session`, `POST /feedback`.
  Account deletion and accounts without a database row are log-only in Phase 1. Everything else is unchanged.
* The decision uses only the verified token UID, the PostgreSQL `users.mfa_enabled` flag and the existing, unchanged
  `validate_mfa_session`. A refused MFA session is `401 MFA_SESSION_REQUIRED` (WebSocket: coded frame, close 1008); an unreachable
  database is `503 MFA_CHECK_UNAVAILABLE` (WebSocket: bare close 1013, no frame). Older apps are not given any bypass.
* A live MFA-protected voice connection is revalidated by the existing 30 s watchdog (after the single-device check, so
  `SESSION_KICKED` and session renewal are untouched), closed at the PostgreSQL-measured session expiry even while the
  database is down, and closed with a bare 1013 after `MFA_WS_FAILCLOSED_SECONDS` without a successful check. Connections that
  are not enforced (modes `off`/`log`, canary-unlisted users, MFA-off accounts) are never closed by the MFA check.

The enforcement tests (`harness/test_config_validation.py`, `harness/test_enforcement.py`) run only inside the same isolated Docker
network with fake users; `test_enforcement.py` starts its own backends with stubbed OpenAI, Cloud Storage and speech-to-text
and simulates database outages with `ALTER DATABASE ... ALLOW_CONNECTIONS`. They take several minutes.
