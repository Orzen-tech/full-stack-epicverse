"""Encrypted-credential gateway (LOCAL TEST ONLY; disabled by default).

POST /auth/enc/nonce            {"op": "login|signup|reauth|change_password"} -> {"nonce", "ttl", "server_time"}
POST /auth/enc/login            body = the G1 envelope exactly                -> {"custom_token"}
POST /auth/enc/signup           body = envelope  (+ header X-Email-Proof)     -> {"custom_token", "created", "recovered"?}
POST /auth/enc/reauth           body = envelope  + Authorization: Bearer      -> {"custom_token"}
POST /auth/enc/change_password  body = envelope  + Authorization: Bearer      -> {"custom_token"|null, "password_changed", ...}

Rules: the e-mail and password exist in clear only inside this process, only while a request runs; nothing is logged except fixed
codes and a random request id; every envelope problem answers the same 400 {"error": "AUTH_FAILED"}; credential problems use the
Firebase-style codes the app already understands; a custom token is minted ONLY for a UID the identity service reported (never one the
client sent); anything unconfigured, unreachable, slow or non-local answers 503 {"error": "AUTH_UNAVAILABLE"} (headers X-Enc-Request-Id
and X-Enc-Cause carry a random id and a fixed cause category). A transport failure is NEVER reported as a credential error and never
counts as a wrong password. There is no plaintext path.

G6b hardening (Option D, no protocol change): the e-mail proof is accepted at the gateway only for ENC_AUTH_PROOF_MAX_AGE_SECONDS after
it was issued, and the first VERIFIED Firebase UID that uses it at the gateway is recorded (HMAC key, never the raw proof); /auth/mark-verified
then refuses a different UID. The record is written only after Firebase confirmed the account, so a failed creation claims nothing.
"""
import asyncio
import hmac
import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.services.user_db import email_verification_proof_is_live
from app.services import enc_auth_diag as diag
from app.services import enc_auth_envelope as env
from app.services import enc_auth_identity as ident
from app.services import enc_auth_store as store
from app.services.enc_auth_limits_eval import SCOPES  # noqa: F401  (documented policy scopes)
from app.services.enc_auth_policy import validate as policy_validate
from app.services.otp_rate_limiter import client_ip

router = APIRouter()

GENERIC = {"error": "AUTH_FAILED"}
UNAVAILABLE = {"error": "AUTH_UNAVAILABLE"}
_PROOF_INVALID = {"error": "email-proof-invalid"}
_THROTTLE_RETRY_SECONDS = 60


class _State:
    def __init__(self, keyring, nonces):
        self.keyring, self.nonces = keyring, nonces


_state = None


def _gateway_status():
    """(state, None) or (None, reason) where reason is a fixed cause code. Every call answers 503 when there is no state."""
    global _state
    if _state is not None:
        return _state, None
    if not settings.ENC_AUTH_ENABLED:
        return None, "disabled"
    if not settings.ENC_AUTH_HASH_SECRET:
        return None, "no_hash_secret"
    if not ident.ready():
        return None, getattr(ident, "not_ready_cause", lambda: "not_local")()
    try:
        keyring = env.load_keyring(settings.ENC_AUTH_KEYS)
    except env.KeyConfigError:
        return None, "bad_keys"
    _state = _State(keyring, store.PgNonceStore(settings.ENC_AUTH_NONCE_TTL_SECONDS, settings.ENC_AUTH_NONCE_MAX_OUTSTANDING))
    return _state, None


def _gateway_state():
    return _gateway_status()[0]


def _reply(status, body, headers=None):
    return JSONResponse(body, status_code=status,
                        headers={"Cache-Control": "no-store", "X-Enc-Request-Id": diag.request_id(), **(headers or {})})


def _unavailable(op, stage, cause, extra=None):
    """The one safe 503: same body every time; the cause category is a fixed code, also recorded in counters and the log."""
    diag.note("unavailable", stage, cause)
    diag.log(op, "unavailable", stage, cause)
    return _reply(503, {**UNAVAILABLE, **(extra or {})}, {"X-Enc-Cause": cause if cause in diag.CAUSES else "other"})


def _limited(retry):
    return _reply(429, {"error": "too-many-requests"}, {"Retry-After": str(max(int(retry), 1))})


async def _read_body(request):
    """The request body, or None when it is larger than any valid envelope."""
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > env.MAX_ENVELOPE_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/auth/enc/nonce")
async def issue_nonce(request: Request):
    diag.new_request_id()
    state, reason = _gateway_status()
    if state is None:
        return _unavailable("nonce", "gateway", reason)
    try:
        async with asyncio.timeout(float(settings.ENC_AUTH_REQUEST_DEADLINE_SECONDS)):
            body = await _read_body(request)
            try:
                op = json.loads(body.decode("utf-8")).get("op") if body else None
            except (ValueError, AttributeError, UnicodeDecodeError):
                return _reply(400, GENERIC)
            if op not in env.OPERATIONS:
                return _reply(400, GENERIC)
            allowed, retry = await store.hit("ip_req", client_ip(request), settings.ENC_AUTH_IP_LIMIT)
            if not allowed:
                return _limited(retry)
            nonce = await state.nonces.issue(op)
    except env.EnvelopeError:
        return _unavailable("nonce", "nonce", "capacity")
    except store.StoreUnavailable as exc:
        return _unavailable("nonce", "db", exc.cause)
    except TimeoutError:
        return _unavailable("nonce", "overall", "timeout")
    except Exception:
        return _unavailable("nonce", "request", "unexpected")
    return _reply(200, {"nonce": nonce, "ttl": state.nonces.ttl, "server_time": int(time.time())})


async def _open(request, op, state):
    """(credentials, None) or (None, response). Applies the per-IP limiter first and never reveals which envelope check failed."""
    allowed, retry = await store.hit("ip_req", client_ip(request), settings.ENC_AUTH_IP_LIMIT)
    if not allowed:
        return None, _limited(retry)
    raw = await _read_body(request)
    if raw is None:
        return None, _reply(400, GENERIC)
    try:
        return await env.open_credentials(raw, state.keyring, state.nonces, op), None
    except env.EnvelopeError as exc:
        if exc.code == env.CAPACITY:
            return None, _unavailable(op, "nonce", "capacity")
        diag.note("outcome", "envelope", exc.code if exc.code in diag.CAUSES else "-")
        diag.log(op, "refused", "envelope", exc.code)
        return None, _reply(400, GENERIC)


async def _caller(request):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    return await ident.verify_id_token(header.split(" ", 1)[1].strip())


async def _check_password(email, password, ip):
    """Server-side password verification behind the gateway's failure limiter (policy: see enc_auth_limits_eval / README).

    The attempt is counted BEFORE the check, in one transaction, and given back unless the identity service answered with a real
    credential failure; so a correct login, a throttle answer, a transport failure, a 5xx or a deadline never count as wrong passwords.
    Returns ("ok", uid) | ("fail", rest_error_code) | ("limited", retry_after). Raises IdentityUnavailable / StoreUnavailable."""
    attempt = await store.reserve_password_attempt(email, ip)
    if attempt.blocked:
        diag.note("outcome", "limiter", "limited")
        return "limited", attempt.retry_after
    keep = False
    try:
        uid, code = await ident.verify_password(email.strip(), password)
        if uid:
            return "ok", uid
        kind = ident.classify_rest_error(code)
        if kind == "credential":
            keep = True
            return "fail", code
        if kind == "throttled":
            return "limited", _THROTTLE_RETRY_SECONDS
        raise ident.IdentityUnavailable("verify_password", "http_4xx")     # permission/API-key/unknown: a service problem, not a wrong password
    finally:
        if not keep:
            try:
                await asyncio.shield(store.release_password_attempt(attempt))
            except Exception:
                diag.note("unavailable", "limiter", "db_unavailable")       # the count stays one too high: fail-safe direction


async def _verified_uid(email, password, ip):
    """Maps _check_password to (uid, None) or (None, response). A disabled account is refused even with a correct password."""
    verdict, value = await _check_password(email, password, ip)
    if verdict == "limited":
        return None, _limited(value)
    if verdict == "fail":
        return None, _reply(400, {"error": ident.map_rest_error(value)})
    account = await ident.get_account(value)
    if account is None or account.disabled:
        return None, _reply(400, {"error": "user-disabled" if account else "invalid-credential"})
    if account.email.strip().lower() != email.strip().lower():
        return None, _reply(400, {"error": "invalid-credential"})
    return value, None


async def _proof_valid(email, proof):
    """Non-consuming check of the single-use e-mail ownership proof (D1) PLUS the gateway-side maximum age (G6b).
    The global 30-minute expiry and the one-time consumption in /auth/mark-verified are unchanged."""
    if not isinstance(proof, str):
        return False
    async with store.connection() as conn:
        return await email_verification_proof_is_live(conn, proof, email, float(settings.ENC_AUTH_PROOF_MAX_AGE_SECONDS))


async def _mint(uid):
    return {"custom_token": await ident.mint_custom_token(uid)}


async def _dispatch(request, op):
    diag.new_request_id()
    started = time.monotonic()
    state, reason = _gateway_status()
    if state is None:
        return _unavailable(op, "gateway", reason)
    ip = client_ip(request)
    ctx = {"update_sent": False, "update_done": False, "revoked": None}
    try:
        async with asyncio.timeout(float(settings.ENC_AUTH_REQUEST_DEADLINE_SECONDS)):
            caller = None
            if op in ("reauth", "change_password"):
                caller = await _caller(request)
                if caller is None:
                    return _reply(401, GENERIC)
            creds, early = await _open(request, op, state)
            if early is not None:
                return early
            if op == "login":
                response = await _login(creds, ip)
            elif op == "signup":
                response = await _signup(request, creds, ip)
            else:
                response = await _reauth_or_change(op, creds, caller, ip, ctx)
    except ident.IdentityUnavailable as exc:
        extra = {"password_changed": "unknown"} if (exc.stage == "update_password") else None
        return _unavailable(op, exc.stage, exc.cause, extra)
    except store.StoreUnavailable as exc:
        return _unavailable(op, "db", exc.cause)
    except ident.NotLocal:
        return _unavailable(op, "config", "not_local")
    except TimeoutError:
        return _timeout_reply(op, ctx)
    except Exception:
        return _unavailable(op, "request", "unexpected")
    diag.log(op, "ok" if response.status_code < 400 else "refused", milliseconds=int((time.monotonic() - started) * 1000))
    return response


def _timeout_reply(op, ctx):
    """The request ran out of time. Say only what is true: before the update was sent nothing changed; after it was sent the
    outcome is unknown; after it succeeded the password IS changed (the client must sign in again)."""
    if op == "change_password" and ctx["update_done"]:
        diag.note("unavailable", "overall", "timeout")
        diag.log(op, "unavailable", "overall", "timeout")
        return _reply(200, {"password_changed": True, "custom_token": None, "retry_signin": True, "sessions_revoked": bool(ctx["revoked"])})
    extra = {"password_changed": "unknown"} if (op == "change_password" and ctx["update_sent"]) else None
    return _unavailable(op, "overall", "timeout", extra)


async def _login(creds, ip):
    uid, early = await _verified_uid(creds.email, creds.password, ip)
    if early is not None:
        return early
    return _reply(200, await _mint(uid))


async def _signup(request, creds, ip):
    email = creds.email.strip()
    allowed, retry = await store.hit("signup_ip", ip, settings.ENC_AUTH_SIGNUP_IP_LIMIT)
    if not allowed:
        return _limited(retry)
    proof = None
    if settings.ENC_AUTH_REQUIRE_EMAIL_PROOF:
        proof = request.headers.get("x-email-proof")
        if not await _proof_valid(email, proof):
            diag.note("outcome", "proof", "-")
            return _reply(403, _PROOF_INVALID)       # missing, malformed, unknown, too old, expired, consumed or for another e-mail
    if policy_validate(creds.password):
        return _reply(400, {"error": "weak-password"})
    try:
        uid = await ident.create_user(email, creds.password)   # a NEW account first: never a sign-in before it exists
    except ident.IdentityRejected:
        return _reply(400, {"error": "weak-password"})
    except ident.EmailExists:
        return await _recover(creds, ip, proof)
    if proof is not None and not await store.bind_claim(proof, uid):
        return _reply(403, _PROOF_INVALID)           # another UID already owns this proof: no token
    body = await _mint(uid)
    body["created"] = True
    return _reply(200, body)


async def _recover(creds, ip, proof):
    """The account already exists (an earlier signup that did not finish, or a lost response): prove ownership with the SAME
    password. Every failure (wrong password, disabled, other provider, limiter) is the same 'email-already-in-use'."""
    verdict, value = await _check_password(creds.email, creds.password, ip)
    if verdict == "ok":
        account = await ident.get_account(value)
        if account is not None and not account.disabled and account.email.strip().lower() == creds.email.strip().lower():
            if proof is not None and not await store.bind_claim(proof, value):
                return _reply(403, _PROOF_INVALID)
            body = await _mint(value)
            body.update({"created": False, "recovered": True})
            return _reply(200, body)
    if verdict == "limited":
        return _limited(value)
    return _reply(400, {"error": "email-already-in-use"})


async def _reauth_or_change(op, creds, caller, ip, ctx):
    account = await ident.get_account(caller["uid"])
    if account is None or account.disabled:
        return _reply(401, GENERIC)
    if account.email.strip().lower() != creds.email.strip().lower():
        await store.record_failure(creds.email, ip)
        diag.note("outcome", "claim", "email_mismatch")
        return _reply(400, {"error": "invalid-credential"})
    uid, early = await _verified_uid(creds.email, creds.password, ip)
    if early is not None:
        return early
    if uid != caller["uid"]:                      # the password belongs to a different Firebase account
        await store.record_failure(creds.email, ip)
        diag.note("outcome", "claim", "uid_mismatch")
        return _reply(400, {"error": "invalid-credential"})
    if op == "reauth":
        return _reply(200, await _mint(uid))
    return await _change_password(creds, uid, ctx)


async def _change_password(creds, uid, ctx):
    new = creds.new_password
    if new == creds.password:
        return _reply(400, {"error": "password-unchanged"})
    if policy_validate(new):
        return _reply(400, {"error": "weak-password"})
    ctx["update_sent"] = True
    try:
        await ident.update_password(uid, new)
    except ident.IdentityRejected:
        ctx["update_sent"] = False
        return _reply(400, {"error": "weak-password", "password_changed": False})
    # IdentityUnavailable propagates: the update may or may not have been applied, so _dispatch answers password_changed "unknown"
    ctx["update_done"] = True
    sessions_revoked = True
    try:
        await ident.revoke_refresh_tokens(uid)
    except ident.IdentityUnavailable:
        sessions_revoked = False
        diag.note("unavailable", "revoke", "sdk_error")
    ctx["revoked"] = sessions_revoked
    try:
        token = (await _mint(uid))["custom_token"]
    except ident.IdentityUnavailable:
        diag.note("unavailable", "mint", "sdk_error")
        return _reply(200, {"password_changed": True, "custom_token": None, "retry_signin": True,
                            "sessions_revoked": sessions_revoked})
    return _reply(200, {"password_changed": True, "custom_token": token, "sessions_revoked": sessions_revoked})


@router.post("/auth/enc/login")
async def enc_login(request: Request):
    return await _dispatch(request, "login")


@router.post("/auth/enc/signup")
async def enc_signup(request: Request):
    return await _dispatch(request, "signup")


@router.post("/auth/enc/reauth")
async def enc_reauth(request: Request):
    return await _dispatch(request, "reauth")


@router.post("/auth/enc/change_password")
async def enc_change_password(request: Request):
    return await _dispatch(request, "change_password")
