# G3 Phase 1 - isolated real-Firebase test harness (OFFLINE ONLY)

Phase 1 builds and tests the *safety layer* for a future real-Firebase compatibility run. **It never contacts Firebase or any other
service**, uses no credentials, starts no service and downloads nothing. The only Firebase project that may ever be used is
`epicverse-g3-auth-test` (a temporary Spark-plan test project).

## What Phase 1 tests

| Module | Purpose |
|---|---|
| `g3_guard.py` | The project guard: exact project allow-list, environment conflict checks, URL/host/path allow-list, request-safety checks, API-key shape check, hard-coded "live mode disabled". |
| `g3_rest_client.py` | A small client for e-mail/password sign-in and refresh-token exchange. It talks to an *injected* transport (a fake in tests), makes one attempt, follows no redirects, caps response size and classifies answers into a fixed vocabulary. |
| `g3_token_inspect.py` | Decodes an ID token and checks its *shape* (algorithm, issuer, audience, subject, `iat`/`exp`/`auth_time`, `firebase` claim). Always reports `verified=False`. |
| `tests/` | 88 offline tests: guard, client, token inspection, static/secret/integrity checks. |

Run (no network, no services): `python -B -I -m unittest discover -s scripts/enc_auth_poc/g3/tests -p "test_g3_*.py"`

## Why the project guard exists
A Google login on a developer machine usually has access to more than one project (including production). Anything that quietly falls
back to a default project, a default credential or a hidden configuration could therefore touch the wrong one. The guard refuses
instead: the project id and the Web API key must be passed explicitly; the id must equal `epicverse-g3-auth-test` byte for byte; any
`GOOGLE_APPLICATION_CREDENTIALS`, `FIREBASE_CONFIG`, `FIREBASE_TOKEN`, emulator host or a different project in a `*_PROJECT` variable
aborts the run before a request object can exist. Only two HTTPS hosts and two paths are approved; redirects and environment proxies are
disabled; the project is re-checked on every call. Nothing is discovered from gcloud, the Firebase CLI or Application Default Credentials.

## Why the tests are offline
Phase 1 only has to prove the *guard and client behave safely*. `tests/_support.py` installs a network-denial layer on import: name
resolution and socket connections raise `NetworkDenied` (a `BaseException`, so a broad `except Exception` cannot hide it) and are
recorded; the suite ends by asserting zero attempts. The denial is verified by calling the patched functions, not by trying to reach
anything. The transport is always a fake; the real one (`HttpxTransport`) refuses to be created while `LIVE_MODE_ENABLED` is `False`.

## Why decoded JWT claims are untrusted
Decoding only reads the claims; anybody can write a token whose claims look right. Authentication needs the RS256 signature checked
against Firebase's signing keys, which Phase 1 deliberately does not download. `decode_unverified` therefore returns
`UnverifiedClaims` (`verified=False`); `VerifiedClaims` can only be built by a future `SignatureVerifier`, and `Phase1Verifier` always
refuses. Emulator-style unsigned tokens (`alg: none`) are rejected.

## Credentials
Nothing secret is in the source, a config file, a report, a log or a command line. `collect_runtime_credentials` takes the password and the
Web API key through hidden prompts; both live in `Secret` objects that cannot be printed, formatted or pickled. The client prints and logs
nothing; `Result.diagnostic()` returns only fixed codes. **Production credentials, service-account keys and Application Default Credentials
are forbidden** and not supported.

## Not authorised yet (each needs a separate explicit approval)
* Any real network request, including sign-in, refresh, or fetching Firebase's signing keys.
* Signature verification with real keys.
* Service-account, IAM or Admin SDK use; custom-token minting.
* Enabling APIs, changing project settings, billing or the Spark plan.

## Operations that could modify dummy accounts (future phases)
Sign-in and refresh create sessions only. These change the test project's data and are **DESTRUCTIVE TO TEST DATA**: password change, account
creation, disabling/enabling, deletion, token revocation, and anything that triggers Firebase throttling or e-mail sending.

Emulator results (G2/G6) are not proof of real Firebase behaviour; that is what a later, approved live phase is for.
