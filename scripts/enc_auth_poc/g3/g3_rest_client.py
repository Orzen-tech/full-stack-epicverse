"""G3 REST client for FUTURE real-Firebase tests: e-mail/password sign-in, refresh-token exchange, and safe classification of the answers.

Phase 1: NOTHING here opens a connection. The client talks to an injected `transport` callable (tests inject a fake). The only real
transport, HttpxTransport, refuses to be created while guard.LIVE_MODE_ENABLED is False. There is no command line, no environment
reading (except through the guard), no logging module, no printing, and no retry. Credentials are accepted at run time only
(collect_runtime_credentials uses hidden prompts) and are never part of an error message, a repr or a diagnostic.
"""
import getpass
import json
import re
from urllib.parse import urlencode
from dataclasses import dataclass

import g3_guard as guard
from g3_guard import GuardedConfig, GuardError, Secret
from g3_token_inspect import decode_unverified

SIGN_IN_URL = guard.SIGN_IN_URL
REFRESH_URL = guard.REFRESH_URL
CERTS_URL = guard.CERTS_URL

MAX_EMAIL_CHARS = 254
MAX_PASSWORD_CHARS = 4096
MAX_TOKEN_CHARS = 4096
_CODE_RE = re.compile(r"^[A-Z][A-Z_]{2,63}$")

# Outcomes (fixed vocabulary)
OK = "ok"
WRONG_CREDENTIALS = "wrong_credentials"
UNKNOWN_EMAIL = "unknown_email"
USER_DISABLED = "user_disabled"
USER_NOT_FOUND = "user_not_found"
TOKEN_INVALID = "token_invalid"
THROTTLED = "throttled"
SERVICE_ERROR = "service_error"
BAD_RESPONSE = "bad_response"
REDIRECT_REJECTED = "redirect_rejected"
TRANSPORT_ERROR = "transport_error"
UNEXPECTED_ERROR = "unexpected_error"

_SIGN_IN_CODES = {
    "INVALID_PASSWORD": WRONG_CREDENTIALS, "INVALID_LOGIN_CREDENTIALS": WRONG_CREDENTIALS,    # enumeration protection ON answers the latter
    "EMAIL_NOT_FOUND": UNKNOWN_EMAIL,                                                         # only when enumeration protection is OFF
    "USER_DISABLED": USER_DISABLED, "TOO_MANY_ATTEMPTS_TRY_LATER": THROTTLED,
}
_REFRESH_CODES = {
    "INVALID_REFRESH_TOKEN": TOKEN_INVALID, "TOKEN_EXPIRED": TOKEN_INVALID, "MISSING_REFRESH_TOKEN": TOKEN_INVALID,
    "USER_DISABLED": USER_DISABLED, "USER_NOT_FOUND": USER_NOT_FOUND, "TOO_MANY_ATTEMPTS_TRY_LATER": THROTTLED,
}
_CUSTOM_TOKEN_CODES = {
    "INVALID_CUSTOM_TOKEN": TOKEN_INVALID, "CREDENTIAL_MISMATCH": TOKEN_INVALID, "USER_DISABLED": USER_DISABLED, "TOO_MANY_ATTEMPTS_TRY_LATER": THROTTLED,
}
KNOWN_ERROR_CODES = frozenset(_CUSTOM_TOKEN_CODES) | frozenset(_SIGN_IN_CODES) | frozenset(_REFRESH_CODES)     # public Firebase code names; the only upper-case text that may be displayed
TRANSPORT_CAUSES = frozenset({"connect", "timeout", "reset", "tls", "dns", "too_large", "other"})


class TransportFailure(Exception):
    """Raised by a transport for connection-level trouble. `cause` comes from TRANSPORT_CAUSES."""

    def __init__(self, cause="other"):
        super().__init__("transport failure")
        self.cause = cause if cause in TRANSPORT_CAUSES else "other"


class InputRejected(Exception):
    """A caller-supplied value was refused before any request was built. Fixed reason only."""

    def __init__(self, reason):
        super().__init__("input rejected: %s" % reason)
        self.reason = reason


@dataclass(frozen=True, repr=False)
class TransportRequest:
    method: str
    url: str
    params: tuple                 # (("key", Secret),)  - the API key travels as a query parameter, never inside the URL string
    body: bytes
    content_type: str
    connect_timeout: float
    read_timeout: float
    max_response_bytes: int
    follow_redirects: bool = False
    trust_env: bool = False
    bearer: object = None         # a Secret access token; allowed ONLY for the Phase 5 signBlob request (see the guard)

    def __repr__(self):
        return "TransportRequest(%s %s, body=<%d bytes redacted>)" % (self.method, self.url, len(self.body))


@dataclass(frozen=True, repr=False)
class TransportResponse:
    status: int
    body: bytes
    url: str                      # the URL that actually answered (after any redirect handling by the transport)

    def __repr__(self):
        return "TransportResponse(status=%r, body=<%d bytes redacted>)" % (self.status, len(self.body))


class Tokens:
    """Session tokens from a successful answer. Every field is a Secret."""
    __slots__ = ("id_token", "refresh_token", "expires_in", "uid", "email", "is_new_user")

    def __init__(self, id_token, refresh_token, expires_in, uid, email=None, is_new_user=None):
        self.id_token, self.refresh_token, self.expires_in, self.uid = Secret(id_token), Secret(refresh_token), expires_in, uid
        self.is_new_user = is_new_user                                        # True if Firebase reports that the sign-in CREATED an account
        self.email = Secret(email) if isinstance(email, str) else None          # echoed by sign-in; kept only to compare locally

    def __repr__(self):
        return "Tokens(<redacted>)"


@dataclass(frozen=True, repr=False)
class Result:
    operation: str
    outcome: str
    http_class: str               # "2xx", "4xx", ... or "-"
    tokens: object = None
    cause: str = "-"
    data: object = None                  # parsed public data (the certificate map); never credentials
    status: int = 0                      # HTTP status of an answer that was received (0 = none)
    error_code: str = "-"                # a Firebase error code from the FIXED known set, "unrecognized" for any other, "-" if none

    def __repr__(self):
        return "Result(operation=%r, outcome=%r, http_class=%r)" % (self.operation, self.outcome, self.http_class)

    def detail(self):
        """Adds the exact HTTP status and the sanitised Firebase error code (never free text) to diagnostic()."""
        return {**self.diagnostic(), "http_status": self.status, "error_code": self.error_code}

    def diagnostic(self):
        """The ONLY thing meant to be printed or stored from a call. Fixed vocabulary, no values."""
        return {"operation": self.operation, "outcome": self.outcome, "http_class": self.http_class, "cause": self.cause}


def _http_class(status):
    return "%dxx" % (status // 100) if isinstance(status, int) and not isinstance(status, bool) and 100 <= status < 600 else "-"


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _parse_json_object(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _error_code(document):
    """The leading UPPER_CASE code of error.message, or '' if the shape is anything else. The free text after it is dropped."""
    error = document.get("error")
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str):
        return ""
    head = re.split(r"[ :]", message, maxsplit=1)[0]
    return head if _CODE_RE.match(head) else ""


def _check_email(email):
    if (not isinstance(email, str) or not 3 <= len(email) <= MAX_EMAIL_CHARS or email.count("@") != 1 or email.startswith("@")
            or email.endswith("@") or re.search(r"[\x00-\x20\x7f]", email)):
        raise InputRejected("email_invalid")


def _check_password(password):
    if not isinstance(password, str) or not 1 <= len(password) <= MAX_PASSWORD_CHARS or "\x00" in password:
        raise InputRejected("password_invalid")


def screen_response(operation, response, url):
    """The checks every answer must pass before its content is looked at: right type, answered by the URL asked, not a redirect, bounded
    size, a JSON object. Returns (Result, None) when the answer is rejected, else (None, (document, http_class))."""
    if (not isinstance(response, TransportResponse) or isinstance(response.status, bool) or not isinstance(response.status, int)
            or not isinstance(response.body, bytes)):
        return Result(operation, BAD_RESPONSE, "-", cause="shape"), None
    http_class = _http_class(response.status)
    if response.url != url:
        return Result(operation, REDIRECT_REJECTED, http_class, cause="unexpected_url"), None
    if 300 <= response.status < 400:
        return Result(operation, REDIRECT_REJECTED, http_class, cause="redirect"), None
    if len(response.body) > guard.MAX_RESPONSE_BYTES:
        return Result(operation, BAD_RESPONSE, http_class, cause="too_large"), None
    document = _parse_json_object(response.body)
    if document is None:
        return Result(operation, BAD_RESPONSE, http_class, cause="not_json"), None
    return None, (document, http_class)


def check_sign_in_inputs(email, password):
    """The same input checks sign_in() applies, so a runner can reject bad input BEFORE any approval or request."""
    _check_email(email)
    _check_password(password)


class FirebaseRestClient:
    def __init__(self, config, transport):
        if not isinstance(config, GuardedConfig):
            raise GuardError("config_not_guarded")
        config.assert_valid()
        if not callable(transport):
            raise GuardError("transport_invalid")
        self._config, self._transport = config, transport

    # ---- operations -------------------------------------------------------------------------------------------------
    def sign_in(self, email, password):
        """E-mail/password sign-in. One request, never retried. Wrong-password and unknown-e-mail are told apart only as far as Firebase does."""
        _check_email(email)
        _check_password(password)
        body = json.dumps({"email": email, "password": password, "returnSecureToken": True}, separators=(",", ":")).encode("utf-8")
        return self._call("sign_in", SIGN_IN_URL, body, "application/json", _SIGN_IN_CODES, self._tokens_from_sign_in)

    def refresh(self, refresh_token):
        """Refresh-token exchange. Accepts a Secret or a str; never retried."""
        raw = refresh_token.reveal() if isinstance(refresh_token, Secret) else refresh_token
        if not isinstance(raw, str) or not 1 <= len(raw) <= MAX_TOKEN_CHARS or not re.match(r"^[A-Za-z0-9._~+/=-]+$", raw):
            raise InputRejected("refresh_token_invalid")
        body = urlencode({"grant_type": "refresh_token", "refresh_token": raw}).encode("ascii")
        return self._call("refresh", REFRESH_URL, body, "application/x-www-form-urlencoded", _REFRESH_CODES, self._tokens_from_refresh)

    def sign_in_with_custom_token(self, custom_token):
        """Exchanges a custom token for an ID/refresh token. NOT approved for live use in Phase 5 preparation: the guard allows this request
        only through grant_custom_token_approval(). One attempt. NOTE: with a uid that does not exist yet, Firebase CREATES the account
        (reported as is_new_user=True), so a live run must use the dummy account's existing uid."""
        raw = custom_token.reveal() if isinstance(custom_token, Secret) else custom_token
        if not isinstance(raw, str) or not 20 <= len(raw) <= 4096 or not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", raw):
            raise InputRejected("custom_token_invalid")
        body = json.dumps({"token": raw, "returnSecureToken": True}, separators=(",", ":")).encode("utf-8")
        return self._call("custom_token_sign_in", guard.CUSTOM_TOKEN_URL, body, "application/json", _CUSTOM_TOKEN_CODES, self._tokens_from_custom_token)

    @staticmethod
    def _tokens_from_custom_token(document):
        id_token, refresh, expires, new = document.get("idToken"), document.get("refreshToken"), document.get("expiresIn"), document.get("isNewUser")
        if new is not None and not isinstance(new, bool):
            return None
        tokens = FirebaseRestClient._build_tokens(id_token, refresh, expires, "-")                 # this answer carries no uid; it comes from the verified token
        if tokens is not None:
            tokens.uid, tokens.is_new_user = None, new
        return tokens

    def inspect_id_token(self, tokens_or_secret, now):
        """UNVERIFIED inspection of an ID token against this client's project. See g3_token_inspect: this is not authentication."""
        raw = tokens_or_secret.id_token.reveal() if isinstance(tokens_or_secret, Tokens) else (
            tokens_or_secret.reveal() if isinstance(tokens_or_secret, Secret) else tokens_or_secret)
        return decode_unverified(raw, self._config.project_id, now)

    # ---- internals --------------------------------------------------------------------------------------------------
    def fetch_public_certificates(self):
        """GET of Firebase's PUBLIC ID-token signing certificates (a key-id -> PEM map). No credentials are sent. One attempt."""
        return self._call("certificates", CERTS_URL, b"", "", {}, self._certs_from_document, method="GET")

    @staticmethod
    def _certs_from_document(document):
        if not 1 <= len(document) <= 16 or not all(isinstance(k, str) and 0 < len(k) <= 128 and isinstance(v, str) and 0 < len(v) <= 8192
                                                     for k, v in document.items()):
            return None
        return dict(document)

    def _call(self, operation, url, body, content_type, code_map, parse_success, method="POST"):
        self._config.assert_valid()                                    # project check on EVERY call, before a request object exists
        params = (("key", self._config.api_key),) if method == "POST" else ()
        request = TransportRequest(method, url, params, body, content_type, guard.CONNECT_TIMEOUT_SECONDS,
                                   guard.READ_TIMEOUT_SECONDS, guard.MAX_RESPONSE_BYTES)
        guard.assert_request_safe(request)
        try:
            response = self._transport(request)                       # exactly one attempt
        except TransportFailure as failure:
            return Result(operation, TRANSPORT_ERROR, "-", cause=failure.cause)
        except guard.LiveModeDisabled:                                 # an exhausted or missing approval must be loud, never a quiet "transport error"
            raise
        except Exception:                                              # anything unexpected fails safely, without its text
            return Result(operation, TRANSPORT_ERROR, "-", cause="other")
        rejected, screened = screen_response(operation, response, url)
        if rejected is not None:
            return rejected
        document, http_class = screened
        if response.status == 200:
            parsed = parse_success(document)
            if parsed is None:
                return Result(operation, BAD_RESPONSE, http_class, cause="shape")
            if method == "GET":
                return Result(operation, OK, http_class, data=parsed)
            return Result(operation, OK, http_class, tokens=parsed, status=200)
        if response.status >= 500:
            return Result(operation, SERVICE_ERROR, http_class, cause="http_5xx", status=response.status)
        code = _error_code(document)
        known = code if code in code_map else ("unrecognized" if code else "-")
        outcome = code_map.get(code)
        if outcome is not None:
            return Result(operation, outcome, http_class, status=response.status, error_code=known)
        # permission, API-key, quota, configuration or unknown codes are SERVICE problems, never credential results
        return Result(operation, SERVICE_ERROR if 400 <= response.status < 500 else UNEXPECTED_ERROR, http_class, cause="http_4xx",
                      status=response.status, error_code=known)

    @staticmethod
    def _tokens_from_sign_in(document):
        id_token, refresh, expires, uid = document.get("idToken"), document.get("refreshToken"), document.get("expiresIn"), document.get("localId")
        return FirebaseRestClient._build_tokens(id_token, refresh, expires, uid, document.get("email"))

    @staticmethod
    def _tokens_from_refresh(document):
        return FirebaseRestClient._build_tokens(document.get("id_token"), document.get("refresh_token"), document.get("expires_in"),
                                                document.get("user_id"))

    @staticmethod
    def _build_tokens(id_token, refresh, expires, uid, email=None):
        if not (isinstance(id_token, str) and 0 < len(id_token) <= MAX_TOKEN_CHARS and isinstance(refresh, str) and 0 < len(refresh) <= MAX_TOKEN_CHARS
                and isinstance(expires, str) and expires.isdigit() and len(expires) <= 7 and isinstance(uid, str) and 0 < len(uid) <= 128):
            return None
        return Tokens(id_token, refresh, int(expires), uid, email)


def collect_runtime_credentials(getpass_fn=getpass.getpass, input_fn=input):
    """Hidden prompts for the secret values (nothing on a command line, in a file or in the environment). Returns (email, Secret password,
    Secret API key). The e-mail is not secret but is still never echoed back by this module."""
    email = input_fn("Dummy account e-mail: ")
    password = Secret(getpass_fn("Dummy account password (hidden): "))
    api_key = Secret(getpass_fn("Web API key of the G3 test project (hidden): "))
    return email, password, api_key


class HttpxTransport:
    """The real transport, for a LATER phase. Constructing it is impossible while live mode is disabled (it is, by a constant in the guard).
    Settings it must keep: HTTPS only, no redirects, no environment proxies, split timeouts, streamed size cap, one attempt."""

    def __init__(self):
        guard.require_live_mode()
        import httpx                                                   # imported only here, so Phase 1 never loads an HTTP client
        self._httpx = httpx

    def __call__(self, request):
        guard.require_live_mode()
        guard.assert_request_safe(request)
        httpx = self._httpx
        timeout = httpx.Timeout(connect=request.connect_timeout, read=request.read_timeout, write=request.read_timeout, pool=request.connect_timeout)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, http2=False) as client:
                with client.stream("POST", request.url, params={"key": request.params[0][1].reveal()}, content=request.body,
                                   headers={"Content-Type": request.content_type}) as response:
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > request.max_response_bytes:
                            raise TransportFailure("too_large")
                        chunks.append(chunk)
                    return TransportResponse(response.status_code, b"".join(chunks), str(response.url.copy_with(query=None)))
        except httpx.ConnectTimeout:
            raise TransportFailure("timeout") from None
        except httpx.TimeoutException:
            raise TransportFailure("timeout") from None
        except httpx.ConnectError:
            raise TransportFailure("connect") from None
        except httpx.HTTPError:
            raise TransportFailure("other") from None
