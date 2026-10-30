"""G3 project guard. Standard library only. Makes NO network call and starts nothing.

The only Firebase project this harness may ever talk to is ALLOWED_PROJECT_ID. Everything here fails closed: a missing, different,
inconsistent or merely suspicious setting raises GuardError BEFORE any request object exists. Nothing is discovered from gcloud, the
Firebase CLI, Application Default Credentials or the environment; the project id and the Web API key must be handed in explicitly.
Error text never contains a value that was supplied by the caller or read from the environment (only fixed reason codes and the NAME of
an offending environment variable).
"""
import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

ALLOWED_PROJECT_ID = "epicverse-g3-auth-test"

# host -> the only paths that may be requested on it. Real Firebase, HTTPS only, no port, no query in the URL itself.
ALLOWED_ENDPOINTS = {
    "identitytoolkit.googleapis.com": frozenset({"/v1/accounts:signInWithPassword"}),
    "securetoken.googleapis.com": frozenset({"/v1/token"}),
    "www.googleapis.com": frozenset({"/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"}),     # PUBLIC signing certificates
}
SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
REFRESH_URL = "https://securetoken.googleapis.com/v1/token"
CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
# The ONLY (method, url) pairs that may ever be requested. A live run may use each of them at most once (see LiveApproval).
APPROVED_REQUESTS = frozenset({("POST", SIGN_IN_URL), ("POST", REFRESH_URL), ("GET", CERTS_URL)})

# ---- Phase 5 (custom-token compatibility test): defined here but NOT part of APPROVED_REQUESTS. They can be used only through
# grant_custom_token_approval(), which refuses while CUSTOM_TOKEN_LIVE_APPROVED is False (it is, until a separate approval).
CUSTOM_TOKEN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken"
SIGN_BLOB_PREFIX = "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
SIGN_BLOB_SUFFIX = ":signBlob"
SERVICE_ACCOUNT_SUFFIX = "@" + ALLOWED_PROJECT_ID + ".iam.gserviceaccount.com"
CUSTOM_TOKEN_LIVE_APPROVED = False

# Variables that name a project: tolerated only when empty or exactly the allowed project. They are never READ to choose a project.
PROJECT_ENV_VARS = ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "GCP_PROJECT", "CLOUDSDK_CORE_PROJECT", "FIREBASE_PROJECT_ID",
                    "FIREBASE_PROJECT", "GOOGLE_CLOUD_QUOTA_PROJECT")
# Variables that would introduce credentials, an emulator or a hidden configuration: refused whenever they are set at all.
FORBIDDEN_ENV_VARS = ("GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_AUTH_EMULATOR_HOST", "FIRESTORE_EMULATOR_HOST", "FIREBASE_CONFIG",
                      "FIREBASE_TOKEN", "CLOUDSDK_AUTH_ACCESS_TOKEN", "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", "GOOGLE_OAUTH_ACCESS_TOKEN",
                      "FIREBASE_SERVICE_ACCOUNT", "GOOGLE_EXTERNAL_ACCOUNT_ALLOW_EXECUTABLES")

# Live (real network) execution is OFF. A later phase needs a separate, explicit approval and a code change reviewed on its own.
LIVE_MODE_ENABLED = False

MAX_RESPONSE_BYTES = 65536
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 30.0
ALLOWED_CONTENT_TYPES = frozenset({"application/json", "application/x-www-form-urlencoded"})

_SA_LOCAL_RE = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]")                    # service-account ids are 6 to 30 characters
_BEARER_RE = re.compile(r"[A-Za-z0-9._~+/=-]{20,2048}")
_API_KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{35}")
_URL_FORBIDDEN_RE = re.compile(r"[^\x21-\x7e]|\\")        # whitespace, control characters, non-ASCII, backslash


class GuardError(Exception):
    """Refusal by the guard. `reason` is a fixed code; `detail` is at most the NAME of an environment variable."""

    def __init__(self, reason, detail=""):
        super().__init__("G3 guard refused: %s%s" % (reason, (" (%s)" % detail) if detail else ""))
        self.reason, self.detail = reason, detail


class LiveModeDisabled(GuardError):
    pass


class Secret:
    """Holds a value that must never be printed, logged, pickled or compared. Only reveal() returns it."""
    __slots__ = ("_value",)

    def __init__(self, value):
        object.__setattr__(self, "_value", value)

    def reveal(self):
        return self._value

    def __setattr__(self, name, value):
        raise AttributeError("Secret is immutable")

    def __repr__(self):
        return "<redacted>"

    __str__ = __repr__

    def __format__(self, spec):
        return "<redacted>"

    def __reduce__(self):
        raise TypeError("Secret cannot be pickled")

    __hash__ = None


def validate_project(project_id):
    """Exactly ALLOWED_PROJECT_ID, byte for byte. No case folding, no trimming, no prefix or suffix matching."""
    if project_id is None or project_id == "":
        raise GuardError("project_missing")
    if not isinstance(project_id, str):
        raise GuardError("project_invalid")
    if project_id != ALLOWED_PROJECT_ID:
        raise GuardError("project_not_allowed")
    return project_id


def validate_api_key(api_key):
    """Shape check only (a Firebase Web API key is 'AIza' + 35 URL-safe characters). Returns the key wrapped in a Secret."""
    raw = api_key.reveal() if isinstance(api_key, Secret) else api_key
    if raw is None or raw == "":
        raise GuardError("api_key_missing")
    if not isinstance(raw, str) or not _API_KEY_RE.fullmatch(raw):
        raise GuardError("api_key_malformed")
    return Secret(raw)


def validate_bearer_token(token):
    """A short-lived access token, held as a Secret: shape check only (the value is never stored, printed or compared)."""
    raw = token.reveal() if isinstance(token, Secret) else None
    if not isinstance(raw, str) or not _BEARER_RE.fullmatch(raw):
        raise GuardError("bearer_malformed")
    return token


def validate_environment(env=None):
    """Refuses conflicting Firebase/Google settings. `env` defaults to the process environment (read-only use)."""
    env = os.environ if env is None else env
    for name in FORBIDDEN_ENV_VARS:
        if str(env.get(name, "")).strip():
            raise GuardError("environment_forbidden", name)
    for name in PROJECT_ENV_VARS:
        value = str(env.get(name, "")).strip()
        if value and value != ALLOWED_PROJECT_ID:
            raise GuardError("environment_conflict", name)


def validate_service_account(email):
    """Only a service account of the allowed project: <id>@epicverse-g3-auth-test.iam.gserviceaccount.com."""
    if not isinstance(email, str) or not email.endswith(SERVICE_ACCOUNT_SUFFIX) or not 6 <= len(email) - len(SERVICE_ACCOUNT_SUFFIX) <= 30:
        raise GuardError("service_account_not_in_project")
    if not _SA_LOCAL_RE.fullmatch(email[:-len(SERVICE_ACCOUNT_SUFFIX)]):
        raise GuardError("service_account_malformed")
    return email


def sign_blob_url(account):
    return SIGN_BLOB_PREFIX + validate_service_account(account) + SIGN_BLOB_SUFFIX


def is_custom_token_pair(method, url):
    """True only for the exact Phase 5 requests: signBlob of a project service account, and the custom-token sign-in."""
    if method != "POST" or not isinstance(url, str) or _URL_FORBIDDEN_RE.search(url):
        return False
    if url == CUSTOM_TOKEN_URL:
        return True
    if url.startswith(SIGN_BLOB_PREFIX) and url.endswith(SIGN_BLOB_SUFFIX):
        try:
            return url == sign_blob_url(url[len(SIGN_BLOB_PREFIX):-len(SIGN_BLOB_SUFFIX)])
        except GuardError:
            return False
    return False


def validate_url(url, method=None):
    """HTTPS, an approved host, an approved path; no userinfo, port, query or fragment; plain ASCII. With `method`, the pair must be approved."""
    if not isinstance(url, str) or not url or _URL_FORBIDDEN_RE.search(url):
        raise GuardError("url_invalid")
    if method is not None and is_custom_token_pair(method, url):      # exact Phase 5 pairs; usable only if an approval budget names them
        return url
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise GuardError("url_not_https")
    host = parts.hostname or ""
    if parts.netloc != host or host != host.lower():         # rejects userinfo, ports, upper-case tricks
        raise GuardError("url_authority_invalid")
    if host not in ALLOWED_ENDPOINTS:
        raise GuardError("url_host_not_allowed")
    if parts.path not in ALLOWED_ENDPOINTS[host]:
        raise GuardError("url_path_not_allowed")
    if parts.query or parts.fragment:
        raise GuardError("url_query_not_allowed")
    if method is not None and (method, url) not in APPROVED_REQUESTS:
        raise GuardError("request_not_approved")
    return url


def assert_request_safe(request):
    """Last check before a request leaves the client: every property that matters, on the request object itself."""
    method = getattr(request, "method", None)
    if method not in ("POST", "GET"):
        raise GuardError("request_method")
    url = getattr(request, "url", None)
    validate_url(url, method)
    bearer = getattr(request, "bearer", None)
    signblob = False
    if bearer is not None or (isinstance(url, str) and url.startswith(SIGN_BLOB_PREFIX)):
        # the one request that carries a bearer token: signBlob. No key parameter, JSON body, a well-formed secret token.
        if not (is_custom_token_pair(method, url) and url.startswith(SIGN_BLOB_PREFIX)) or not isinstance(bearer, Secret):
            raise GuardError("request_bearer_not_allowed")
        validate_bearer_token(bearer)
        if getattr(request, "params", None) != () or getattr(request, "content_type", None) != "application/json":
            raise GuardError("request_signblob_shape")
        signblob = True
    if getattr(request, "follow_redirects", True) is not False:
        raise GuardError("request_redirects")
    if getattr(request, "trust_env", True) is not False:
        raise GuardError("request_proxy_env")
    for name in ("connect_timeout", "read_timeout"):
        value = getattr(request, name, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= MAX_TIMEOUT_SECONDS:
            raise GuardError("request_timeout")
    limit = getattr(request, "max_response_bytes", None)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= MAX_RESPONSE_BYTES:
        raise GuardError("request_response_limit")
    params = getattr(request, "params", None)
    if signblob:                                              # shape already checked above; only the body type is left
        if not isinstance(getattr(request, "body", None), bytes):
            raise GuardError("request_body")
        return
    if method == "GET":                                       # the public certificate download: no key, no body, no content type
        if params != () or getattr(request, "body", None) != b"" or getattr(request, "content_type", None) != "":
            raise GuardError("request_get_shape")
        return
    if getattr(request, "content_type", None) not in ALLOWED_CONTENT_TYPES:
        raise GuardError("request_content_type")
    if not isinstance(params, tuple) or len(params) != 1 or params[0][0] != "key":
        raise GuardError("request_params")
    validate_api_key(params[0][1])
    if not isinstance(getattr(request, "body", None), bytes):
        raise GuardError("request_body")


def require_live_mode():
    """Called by anything that would open a real connection. Always refuses in Phase 1."""
    if LIVE_MODE_ENABLED is not True:
        raise LiveModeDisabled("live_mode_disabled")


_APPROVAL_KEY = object()


class LiveApproval:
    """Permission for ONE live run, held in memory only: it can be created solely by grant_live_approval() after an interactive
    confirmation that names the project, it allows each approved request exactly once (so at most three requests in total), and it is closed
    (becomes useless) by close(). There is no global switch: LIVE_MODE_ENABLED stays False."""
    __slots__ = ("_project", "_remaining", "_open", "used", "_order")

    def __init__(self, project_id, budget=None, order=None, _key=None):
        if _key is not _APPROVAL_KEY:
            raise LiveModeDisabled("approval_not_granted")
        validate_project(project_id)
        budget = {pair: 1 for pair in APPROVED_REQUESTS} if budget is None else dict(budget)
        if not budget or not all(pair in APPROVED_REQUESTS or is_custom_token_pair(*pair) for pair in budget) or any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 3 for n in budget.values()):
            raise LiveModeDisabled("budget_invalid")                 # a budget can only name approved requests, 1 to 3 uses each
        if order is not None:                                      # an exact sequence: each budgeted request exactly once, in this order
            order = tuple(order)
            if sorted(order) != sorted(budget) or any(n != 1 for n in budget.values()):
                raise LiveModeDisabled("order_invalid")
        self._project, self._remaining, self._open, self.used, self._order = project_id, budget, True, 0, order

    def consume(self, method, url):
        """Called by the live transport immediately before each connection. Re-checks the project, the pair, and that it was not used yet."""
        if not self._open:
            raise LiveModeDisabled("approval_closed")
        validate_project(self._project)
        validate_url(url, method)
        if self._remaining.get((method, url), 0) < 1:
            raise LiveModeDisabled("request_already_used")
        if self._order is not None and (method, url) != self._order[self.used]:
            raise LiveModeDisabled("request_out_of_order")
        self._remaining[(method, url)] -= 1
        self.used += 1

    def close(self):
        self._open, self._remaining = False, {}


# Phase 3 error-behaviour run: the sign-in endpoint twice (wrong password, then a nonexistent e-mail) and nothing else.
ERROR_TEST_BUDGET = {("POST", SIGN_IN_URL): 2}


def grant_live_approval(project_id, typed_project_id, interactive, budget=None, order=None):
    """The only way to obtain a LiveApproval: an interactive session in which the operator typed the exact project id.
    `budget` (default: each approved request once) may only narrow or repeat approved requests, at most 3 uses each."""
    validate_project(project_id)
    if interactive is not True:
        raise LiveModeDisabled("not_interactive")
    if not isinstance(typed_project_id, str) or typed_project_id != project_id:
        raise LiveModeDisabled("confirmation_mismatch")
    return LiveApproval(project_id, budget, order, _key=_APPROVAL_KEY)


def custom_token_sequence(service_account):
    """The five requests of the Phase 5 test, in the only order they may happen: password sign-in, certificates (so the normal-login token is
    verified BEFORE anything is signed), signBlob, custom-token exchange, refresh."""
    return (("POST", SIGN_IN_URL), ("GET", CERTS_URL), ("POST", sign_blob_url(service_account)), ("POST", CUSTOM_TOKEN_URL), ("POST", REFRESH_URL))


def grant_custom_token_approval(project_id, typed_project_id, interactive, service_account, enabled_for_this_run=False):
    """Phase 5 only: exactly FIVE requests, once each, in a fixed order. Refuses unless the phase was approved: either the code constant
    CUSTOM_TOKEN_LIVE_APPROVED is True (never in the repository) or the operator explicitly enabled THIS run at the interactive prompt
    (`enabled_for_this_run`, held only by the running process)."""
    if CUSTOM_TOKEN_LIVE_APPROVED is not True and enabled_for_this_run is not True:
        raise LiveModeDisabled("custom_token_phase_not_approved")
    order = custom_token_sequence(service_account)
    return grant_live_approval(project_id, typed_project_id, interactive, {pair: 1 for pair in order}, order)


@dataclass(frozen=True, repr=False)
class GuardedConfig:
    """A validated project + API key. Only load_config() should create one; the client re-checks it on every call."""
    project_id: str
    api_key: Secret

    def __repr__(self):
        return "GuardedConfig(project_id=%r, api_key=<redacted>)" % self.project_id

    def assert_valid(self):
        validate_project(self.project_id)
        validate_api_key(self.api_key)


def load_config(project_id, api_key, env=None):
    """Order matters: project, then environment, then key - all before any request object can exist. Nothing is discovered."""
    validate_project(project_id)
    validate_environment(env)
    return GuardedConfig(project_id=project_id, api_key=validate_api_key(api_key))
