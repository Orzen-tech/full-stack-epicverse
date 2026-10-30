# G2: local Firebase Auth emulator compatibility tests (emulator only)

Everything here runs against the **Firebase Auth emulator on 127.0.0.1:9099** (project `demo-epicverse-g2`). It uses dummy accounts
(`*@example.invalid`) and random dummy passwords. It is **not** the production gateway and **not** proof of real Firebase behaviour.

## Run (Windows)

```powershell
python -B -I scripts\enc_auth_poc\g2\run_g2.py <scratch-dir>
```

`run_g2.py` starts the **already cached** `firebase-tools` (nothing is installed or downloaded) with: a `demo-` project, no login,
update checks off, usage tracking unset, all proxy variables pointing at a dead local port (any attempt to leave the machine fails),
its config redirected into `<scratch-dir>\cfg`, and the CLI logs written to `<scratch-dir>`. It checks that the emulator has no
external connections and no non-loopback listener (before and after the tests), runs `test_g2_emulator.py`, and always stops the
emulator by killing only the process tree it started.

## Files

| File | Purpose |
|---|---|
| `emu_client.py` | REST client for the emulator and the isolation guard (refuses anything but `127.0.0.1:9099`, a `demo-` project, or any Google credential variable) |
| `gateway_stub.py` | Test double of the gateway: opens the G1 envelope, verifies the password through the identity REST API, mints a custom token only for a UID it verified |
| `test_g2_emulator.py` | 33 tests (T01 to T08) |
| `run_g2.py` | Orchestrator (start, isolation checks, run, stop) |

## What the emulator can and cannot show

| Area | Result in the emulator | Real Firebase |
|---|---|---|
| Password signup, REST verification, error codes | Tested | Re-check codes in G3 |
| Custom-token sign-in keeps the UID and the password provider | Tested | Re-check in G3 |
| `email` / `email_verified` claims in custom-token sessions | Present (copied from the user record) | **UNVERIFIED, G3** |
| Custom-token **signature** | **Not validated** (unsigned tokens are accepted) | `signBlob` and signing: **G3** |
| Token refresh keeps `auth_time` | Tested | Re-check in G3 |
| Revocation | The old ID token fails `check_revoked`. **Refresh is NOT stopped** after a revoke. | Session ending at refresh: **G3** |
| Recent-login (`requires-recent-login`) | **Not emulated** | **G3** |
| Password policy beyond minimum length, throttling | **Not emulated** | **G3** |
| Disabled users | Rejected at password, custom-token and refresh | Re-check in G3 |
| Ghost account for an unknown UID | Created | The gateway must never mint for an unverified UID |
| MFA binding (uid and exact `auth_time`) | Real repository functions with emulator claims | Database part not exercised |
| Client SDK persistence / logout | Not covered (needs the app) | G5 |

## Findings worth carrying forward

* `accounts:signInWithCustomToken` returns no `localId` in the emulator; the UID comes from the ID token.
* A custom-token session has `sign_in_provider = custom`; the password provider stays on the account.
* `create_custom_token` never checks whether the user is disabled, so the gateway must.
* The backend calls `verify_id_token` without `check_revoked`, so an already-issued ID token keeps working after a password change until it expires.
