"""Identity adapter of the encrypted-credential gateway, LOCAL TEST ONLY.

It can talk to exactly one kind of identity service: the Firebase Auth EMULATOR (or a hermetic fake with the same wire format) on a
host named in ENC_AUTH_ALLOWED_IDENTITY_HOSTS. There is intentionally NO production mode: real Firebase compatibility (G3) is
unverified, so the adapter refuses to operate unless ALL of these hold, and the gateway then answers "unavailable":
  * FIREBASE_AUTH_EMULATOR_HOST is set to host:port with an allowed host (no scheme),
  * FIREBASE_PROJECT_ID starts with "demo-",
  * no Google credential file is configured (GOOGLE_APPLICATION_CREDENTIALS).
Plain http to the emulator only; proxy environment variables are ignored. Nothing here logs an e-mail, password, token or uid.

Failure handling (G6b): every trouble with the identity service is an IdentityUnavailable carrying a fixed STAGE and CAUSE code. A
transport failure or an unexpected 4xx/5xx is NEVER reported as a wrong password. Only the idempotent verification/read operations
(password check, account lookup, ID-token check) are retried, at most twice inside a ~1.5 s budget, and only for transient causes;
account creation, password update, revocation and token minting are never retried automatically.
"""
import asyncio
import os
import socket
import time

import firebase_admin
import httpx
from fastapi import HTTPException
from firebase_admin import auth, exceptions as fb_exceptions

from app.core.config import settings
from app.services import enc_auth_diag as diag
from app.services.enc_auth_retry import retrying

_EMULATOR_API_KEY = "fake-api-key"  # ignored by the emulator; a real API key is deliberately not supported here


class NotLocal(Exception):
    """The environment is not a verified local test environment."""


class IdentityUnavailable(Exception):
    """The identity service could not be used. `stage` and `cause` are fixed codes (enc_auth_diag); the outcome of a write may be unknown."""

    def __init__(self, stage="verify_password", cause="transport"):
        super().__init__("%s/%s" % (stage, cause))
        self.stage, self.cause = stage, cause


class IdentityRejected(Exception):
    """The identity service definitively refused the request (nothing changed)."""


class EmailExists(Exception):
    pass


TRANSIENT_CAUSES = frozenset({"connect", "refused", "reset", "dns", "connect_timeout", "http_502", "http_503", "http_504", "sdk_unavailable"})


def is_transient(exc):
    return isinstance(exc, IdentityUnavailable) and exc.cause in TRANSIENT_CAUSES


def _allowed_hosts():
    return {h.strip().lower() for h in (settings.ENC_AUTH_ALLOWED_IDENTITY_HOSTS or "").split(",") if h.strip()}


def local_base_url():
    """Returns the emulator's Identity Toolkit base URL or raises NotLocal."""
    host = os.environ.get("FIREBASE_AUTH_EMULATOR_HOST", "").strip()
    name, _, port = host.partition(":")
    if not host or "/" in host or not name or not port.isdigit() or name.lower() not in _allowed_hosts():
        raise NotLocal()
    if not (os.environ.get("FIREBASE_PROJECT_ID", "") or "").startswith("demo-"):
        raise NotLocal()
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or settings.GOOGLE_APPLICATION_CREDENTIALS:
        raise NotLocal()
    return f"http://{host}/identitytoolkit.googleapis.com/v1"


_APP_NAME = "enc-gateway"


def _admin_app():
    """A private Admin SDK app for the gateway with a BOUNDED http timeout (the SDK default is 120 s, which would leave threads hanging
    long after a request was abandoned). Only ever initialised after local_base_url() passed, i.e. for a verified emulator."""
    try:
        return firebase_admin.get_app(_APP_NAME)
    except ValueError:
        return firebase_admin.initialize_app(
            options={"projectId": os.environ.get("FIREBASE_PROJECT_ID", ""), "httpTimeout": float(settings.ENC_AUTH_SDK_TIMEOUT_SECONDS)},
            name=_APP_NAME)


def ready():
    try:
        local_base_url()
        return True
    except NotLocal:
        return False


# ---- classification ---------------------------------------------------------------------------------------------

def transport_cause(exc):
    """A fixed cause code for an httpx failure, derived from the exception TYPES only (never from message text)."""
    chain = []
    cur = exc
    while cur is not None and len(chain) < 6:
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    if any(isinstance(c, socket.gaierror) for c in chain):
        return "dns"
    if any(isinstance(c, ConnectionRefusedError) for c in chain):
        return "refused"
    if any(isinstance(c, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)) for c in chain):
        return "reset"
    if isinstance(exc, httpx.ConnectTimeout):
        return "connect_timeout"
    if isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout)):
        return "read_timeout"
    if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)):
        return "reset"
    if isinstance(exc, httpx.ConnectError):
        return "connect"
    return "transport"


def sdk_cause(exc):
    if isinstance(exc, fb_exceptions.UnavailableError):
        return "sdk_unavailable"
    if isinstance(exc, fb_exceptions.DeadlineExceededError):
        return "sdk_timeout"
    return "sdk_error"


CREDENTIAL_CODES = frozenset({"INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS", "EMAIL_NOT_FOUND", "USER_DISABLED", "MISSING_PASSWORD",
                              "INVALID_EMAIL"})
THROTTLE_CODES = frozenset({"TOO_MANY_ATTEMPTS_TRY_LATER"})


def classify_rest_error(code):
    """'credential' (a real wrong-credentials answer), 'throttled' (the identity service's own throttle) or 'service' (anything else:
    permission, API key, configuration, unknown). Only 'credential' may ever be reported to the user as a credential error."""
    if code in CREDENTIAL_CODES:
        return "credential"
    if code in THROTTLE_CODES:
        return "throttled"
    return "service"


REST_ERROR_MAP = {
    "INVALID_PASSWORD": "invalid-credential",
    "INVALID_LOGIN_CREDENTIALS": "invalid-credential",
    "EMAIL_NOT_FOUND": "invalid-credential",           # same answer as a wrong password: no account enumeration
    "MISSING_PASSWORD": "invalid-credential",
    "INVALID_EMAIL": "invalid-credential",
    "USER_DISABLED": "user-disabled",
}


def map_rest_error(code):
    return REST_ERROR_MAP.get(code, "invalid-credential")


def _error_code(response):
    try:
        message = (response.json().get("error") or {}).get("message", "")
    except Exception:
        return ""
    return str(message).split(" ")[0].split(":")[0]


async def _timed(stage, call):
    started = time.monotonic()
    try:
        return await call()
    finally:
        diag.observe(stage, (time.monotonic() - started) * 1000.0)


def _retry(stage, call):
    return retrying(call, stage=stage, is_transient=is_transient, max_retries=int(settings.ENC_AUTH_RETRY_MAX),
                    budget_ms=float(settings.ENC_AUTH_RETRY_BUDGET_MS), note=diag.note)


# ---- password verification (idempotent: retried on transient transport trouble only) --------------------------------

async def verify_password(email, password):
    """Server-side password check. Returns (uid, None) or (None, REST error code); the caller classifies the code with
    classify_rest_error(). Transport trouble and 5xx raise IdentityUnavailable (never a credential result).
    The uid is whatever the identity service reports for those credentials; it is never taken from the client."""
    base = local_base_url()

    async def attempt():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(8.0), trust_env=False) as client:
                response = await client.post(
                    base + "/accounts:signInWithPassword", params={"key": _EMULATOR_API_KEY},
                    json={"email": email, "password": password, "returnSecureToken": True})
        except httpx.HTTPError as exc:
            raise IdentityUnavailable("verify_password", transport_cause(exc)) from None
        status = response.status_code
        if status == 200:
            uid = response.json().get("localId")
            if isinstance(uid, str) and uid:
                return uid, None
            raise IdentityUnavailable("verify_password", "unexpected")
        if status in (502, 503, 504):
            raise IdentityUnavailable("verify_password", "http_%d" % status)
        if status >= 500:
            raise IdentityUnavailable("verify_password", "http_5xx")
        if 400 <= status < 500:
            return None, (_error_code(response) or "UNKNOWN")
        raise IdentityUnavailable("verify_password", "unexpected")

    return await _timed("verify_password", lambda: _retry("verify_password", attempt))


# ---- Admin SDK wrappers (blocking SDK, run in a thread). Module-level functions so tests can wrap them. ----

class Account:
    __slots__ = ("uid", "email", "disabled")

    def __init__(self, uid, email, disabled):
        self.uid, self.email, self.disabled = uid, email, disabled


async def get_account(uid):
    """Returns an Account, or None if the user does not exist. Idempotent read: retried on transient trouble."""
    local_base_url()

    async def attempt():
        try:
            user = await asyncio.to_thread(auth.get_user, uid, _admin_app())
        except auth.UserNotFoundError:
            return None
        except fb_exceptions.FirebaseError as exc:
            raise IdentityUnavailable("get_account", sdk_cause(exc)) from None
        except ValueError:
            raise IdentityUnavailable("get_account", "sdk_error") from None
        return Account(user.uid, user.email or "", bool(user.disabled))

    return await _timed("get_account", lambda: _retry("get_account", attempt))


async def create_user(email, password):
    """Creates a NEW user with email_verified=False and returns its uid (the one Firebase reports). NEVER retried automatically."""
    local_base_url()
    try:
        user = await _timed("create_user", lambda: asyncio.to_thread(auth.create_user, email=email, password=password, email_verified=False, app=_admin_app()))
    except auth.EmailAlreadyExistsError:
        raise EmailExists() from None
    except ValueError:
        raise IdentityRejected() from None
    except fb_exceptions.InvalidArgumentError:
        raise IdentityRejected() from None
    except fb_exceptions.FirebaseError as exc:
        raise IdentityUnavailable("create_user", sdk_cause(exc)) from None
    return user.uid


async def update_password(uid, new_password):
    """NEVER retried automatically: if it fails after being sent, the outcome is unknown."""
    local_base_url()
    try:
        await _timed("update_password", lambda: asyncio.to_thread(auth.update_user, uid, password=new_password, app=_admin_app()))
    except (ValueError, fb_exceptions.InvalidArgumentError):
        raise IdentityRejected() from None        # definitive: nothing changed
    except fb_exceptions.FirebaseError as exc:
        raise IdentityUnavailable("update_password", sdk_cause(exc)) from None
    except Exception:
        raise IdentityUnavailable("update_password", "sdk_error") from None


async def revoke_refresh_tokens(uid):
    """NEVER retried automatically."""
    local_base_url()
    try:
        await _timed("revoke", lambda: asyncio.to_thread(auth.revoke_refresh_tokens, uid, _admin_app()))
    except fb_exceptions.FirebaseError as exc:
        raise IdentityUnavailable("revoke", sdk_cause(exc)) from None
    except Exception:
        raise IdentityUnavailable("revoke", "sdk_error") from None


async def mint_custom_token(uid):
    """A custom token for exactly this uid. Callers must pass a uid reported by the identity service, never one from a client."""
    local_base_url()
    try:
        token = await _timed("mint", lambda: asyncio.to_thread(auth.create_custom_token, uid, None, _admin_app()))
    except fb_exceptions.FirebaseError as exc:
        raise IdentityUnavailable("mint", sdk_cause(exc)) from None
    except Exception:
        raise IdentityUnavailable("mint", "sdk_error") from None
    return token.decode("ascii") if isinstance(token, bytes) else token


async def verify_id_token(token):
    """Verifies the caller's Firebase ID token INCLUDING revocation and disabled-user checks. Returns the claims or None.
    Idempotent read: retried on transient infrastructure trouble."""
    local_base_url()

    async def attempt():
        try:
            return await asyncio.to_thread(auth.verify_id_token, token, _admin_app(), True)
        except (ValueError, auth.InvalidIdTokenError, auth.ExpiredIdTokenError, auth.RevokedIdTokenError, auth.UserDisabledError):
            return None
        except fb_exceptions.FirebaseError as exc:
            raise IdentityUnavailable("verify_token", sdk_cause(exc)) from None
        except Exception:
            raise IdentityUnavailable("verify_token", "sdk_error") from None

    return await _timed("verify_token", lambda: _retry("verify_token", attempt))


# ---- D6: where the backend needs "the verified e-mail of the token's account" ----

async def resolve_token_email(current_user):
    """The e-mail of the signed-in account, for /sync-user, /auth/mark-verified, /auth/email/verify-request and /verify-confirm.

    * Gateway disabled (the default): exactly the old behaviour, the token claim, so nothing changes in production.
    * Gateway enabled: the UID comes ONLY from the already-verified token. The account is looked up through the Admin SDK and
      must exist, be enabled and have an e-mail. If the token also carries an e-mail claim it must equal the account's; if the
      claim is absent (a custom-token session may omit it) the account's e-mail is used. Nothing the client sends is trusted.
    Returns the e-mail string (or None when the gateway is off and the claim is absent, as before). Raises HTTPException 403 for a
    missing, disabled or inconsistent account and 503 when the identity service cannot be reached."""
    claim = (current_user.get("email") or "").strip()
    if not settings.ENC_AUTH_ENABLED:
        return claim or None
    diag.new_request_id()                       # so a 503 here can be matched to its log line
    uid = current_user.get("uid")
    if not isinstance(uid, str) or not uid:
        raise HTTPException(status_code=403, detail="Account email could not be verified")
    try:
        account = await get_account(uid)
    except IdentityUnavailable as exc:
        diag.log("token_email", "unavailable", "token_email", exc.cause)
        raise HTTPException(status_code=503, detail="Account email could not be verified right now") from None
    except NotLocal:
        diag.log("token_email", "unavailable", "config", "not_local")
        raise HTTPException(status_code=503, detail="Account email could not be verified right now") from None
    if account is None or account.disabled or not account.email:
        raise HTTPException(status_code=403, detail="Account email could not be verified")
    if claim and claim.lower() != account.email.strip().lower():
        raise HTTPException(status_code=403, detail="Account email could not be verified")
    return claim or account.email.strip()


# ---- G7: selectable identity backend (the emulator code above is unchanged and stays the default) -------------------------------
if settings.ENC_AUTH_IDENTITY_BACKEND != "emulator":
    from app.services import enc_auth_identity_firebase as _firebase_backend  # noqa: E402
    _firebase_backend.install(globals(), settings.ENC_AUTH_IDENTITY_BACKEND)
