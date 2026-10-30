"""G8 egress guard. Makes NO network call by itself and starts nothing. LIVE EXECUTION IS DISABLED (see LIVE_EXECUTION_APPROVED).

One `Egress` object decides, for EVERY request that any library in this process is about to send, whether it may leave:
HTTPS only, an exact host + exact path allowlist for the one project below, a bounded timeout, no proxy, no redirect, a per-kind quota and a
hard total cap. The request is charged BEFORE it is sent, so requests actually sent <= cap. Three independent layers feed the same budget:

  1. urllib3 `HTTPConnectionPool._make_request`  - called once per wire ATTEMPT, including every SDK-internal retry (Firebase Admin SDK, google-auth);
  2. an httpx transport                          - the G7 REST / signBlob ports;
  3. a stdlib http.client transport (G3's)       - the G3 oracle calls (refresh, custom-token exchange, certificates).

A socket-level backstop (name resolution restricted to the allowed hosts, connections only to addresses that were resolved for them, a
connection cap) sits below all three. `cap_guaranteed()` is False unless every layer is installed and the library versions are the ones this
design was verified against; live execution refuses to start otherwise. Any violation TRIPS the guard: every later request is refused too.
Error text and logs contain fixed codes only - never a URL, key, token, e-mail, uid or password.
"""
import json
import os
import re
import socket
import sys
import threading
from collections import Counter
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
_G3 = os.path.join(os.path.dirname(_HERE), "g3")
for _p in (_HERE, _G3):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import g3_guard as g3  # noqa: E402

ALLOWED_PROJECT_ID = "epicverse-g3-auth-test"
assert ALLOWED_PROJECT_ID == g3.ALLOWED_PROJECT_ID

# Live (real network) execution is a global switch that only means anything together with a per-mode approval below.
# LOCKED DOWN after the completed live tests (Stage A 7/7, Stage B 25/25, cleanup 3/3). Re-enabling any mode needs a new explicit approval.
LIVE_EXECUTION_APPROVED = False

# Each execution mode needs its OWN explicit approval, i.e. its own reviewed change of one of these values.
STAGE_APPROVED = {"A": False, "B": False, "C": False}

GLOBAL_REQUEST_CAP = 45                       # Stage A + Stage B + cleanup together (8 + 34 + 3 = 45), never more
MAX_TIMEOUT_SECONDS = 10.0
LOOPBACK_CONNECT_CAP = 4                      # asyncio's internal socket pair on Windows; nothing else may use loopback
ADMIN_HTTP_TIMEOUT_SECONDS = 8.0

# Kinds of request. A quota of 0 (or a missing kind) means "refused at the transport", whatever the code above asks for.
STAGE_QUOTAS = {
    "A": {"sign_in": 2, "lookup": 2, "certs": 2, "sign_blob": 1, "custom_exchange": 1},
    "B": {"create": 2, "sign_in": 7, "lookup": 10, "update": 4, "refresh": 2, "sign_blob": 2, "custom_exchange": 2, "certs": 5},
    "C": {"lookup": 2, "delete": 1},                    # cleanup of the disposable account only; a separate, separately approved run
}
MUTATING_KINDS = frozenset({"create", "update", "delete"})
assert sum(sum(q.values()) for q in STAGE_QUOTAS.values()) <= GLOBAL_REQUEST_CAP

ID_TOOLKIT, SECURETOKEN, GOOGLEAPIS, IAMCREDENTIALS = ("identitytoolkit.googleapis.com", "securetoken.googleapis.com", "www.googleapis.com",
                                                       "iamcredentials.googleapis.com")
ALLOWED_HOSTS = frozenset({ID_TOOLKIT, SECURETOKEN, GOOGLEAPIS, IAMCREDENTIALS})
CERTS_PATH = "/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_KEY_QUERY_RE = re.compile(r"key=AIza[0-9A-Za-z_-]{35}")
_UID_RE = re.compile(r"[A-Za-z0-9]{1,128}")

# Library versions the "every attempt passes the hook" argument was verified against. Anything else: no live run.
VALIDATED = {"urllib3": ("2.",), "requests": ("2.",), "httpx": ("0.28.",), "firebase_admin": ("6.4.",)}

PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy", "WS_PROXY", "WSS_PROXY")
TLS_ENV = ("REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR")
ADC_ENV = g3.FORBIDDEN_ENV_VARS


class LiveSafetyStop(BaseException):
    """BaseException on purpose: no `except Exception` inside an SDK can swallow it. `code` is a fixed vocabulary item."""

    def __init__(self, code):
        super().__init__("G8 safety stop: " + code)
        self.code = code


class LiveDisabled(LiveSafetyStop):
    pass


def classify(method, host, path, project=ALLOWED_PROJECT_ID, signer=None):
    """(kind) for an allowed request, else None. Exact strings only; nothing is matched by prefix except the one signBlob URL shape."""
    base = "/v1/projects/%s" % project
    if host == ID_TOOLKIT and method == "POST":
        return {"/v1/accounts:signInWithPassword": "sign_in", "/v1/accounts:signInWithCustomToken": "custom_exchange",
                base + "/accounts:lookup": "lookup", base + "/accounts": "create", base + "/accounts:update": "update",
                base + "/accounts:delete": "delete"}.get(path)
    if host == SECURETOKEN and method == "POST":
        return "refresh" if path == "/v1/token" else None
    if host == GOOGLEAPIS and method == "GET":
        return "certs" if path == CERTS_PATH else None
    if host == IAMCREDENTIALS and method == "POST" and signer:
        return "sign_blob" if path == "/v1/projects/-/serviceAccounts/%s:signBlob" % signer else None
    return None


def check_environment(env=None):
    """Fixed codes only. Proxies, TLS-trust overrides, credentials and emulators in the environment all refuse a live run."""
    env = os.environ if env is None else env
    for name in PROXY_ENV:
        if str(env.get(name, "")).strip():
            return "proxy_environment"
    for name in TLS_ENV:
        if str(env.get(name, "")).strip():
            return "tls_override_environment"
    for name in ADC_ENV:
        if str(env.get(name, "")).strip():
            return "credential_environment"
    for name in g3.PROJECT_ENV_VARS:
        value = str(env.get(name, "")).strip()
        if value and value != ALLOWED_PROJECT_ID:
            return "project_environment"
    return None


class Egress:
    """The single budget and policy. Thread-safe. `stage` is "A" (dummy, read-only), "B" (disposable account) or "C" (cleanup)."""

    def __init__(self, stage, signer_account=None, _loopback_test_port=None):
        if stage not in STAGE_QUOTAS:
            raise LiveSafetyStop("stage_unknown")
        if signer_account is not None:
            g3.validate_service_account(signer_account)
        self.stage, self.signer = stage, signer_account
        self._quota = dict(STAGE_QUOTAS[stage])
        self.total_cap = sum(self._quota.values())
        self._used, self._lock = Counter(), threading.Lock()
        self.tripped = None
        self.refused = 0
        self.enabled_mutations = set()          # kinds the human has separately confirmed (only ever non-empty in Stage B)
        self._bound_uid = None
        self._test_port = _loopback_test_port   # tests only: one loopback port, plain http, kind "lookup"
        self.sequence = []                      # (kind,) in order - fixed vocabulary, for the safe report
        self.connections = 0
        self.loopback_connections = 0
        self.dns_lookups = 0

    # ---- configuration ------------------------------------------------------------------------------------------------------
    def enable_mutation(self, kind):
        if self.stage not in ("B", "C") or kind not in MUTATING_KINDS or (self.stage == "C" and kind != "delete"):
            raise LiveSafetyStop("mutation_not_allowed")
        self.enabled_mutations.add(kind)

    def bind_disposable_uid(self, uid):
        """Update/delete requests must name exactly this uid in their JSON body. Bound once, only in Stage B."""
        if self.stage not in ("B", "C") or self._bound_uid is not None or not isinstance(uid, str) or not _UID_RE.fullmatch(uid):
            raise LiveSafetyStop("uid_bind_refused")
        self._bound_uid = uid

    # ---- the decision -------------------------------------------------------------------------------------------------------
    def used(self):
        with self._lock:
            return dict(self._used), sum(self._used.values())

    def _refuse(self, code):
        self.refused += 1
        if self.tripped is None:
            self.tripped = code
        raise LiveSafetyStop(code)

    def charge(self, method, url, body=None, timeout=None, proxied=False):
        """Called before every request attempt. Returns the kind, or raises LiveSafetyStop (and trips the guard)."""
        with self._lock:
            if self.tripped is not None:
                self.refused += 1
                raise LiveSafetyStop("guard_tripped")
            if proxied:
                self._refuse("proxy_not_allowed")
            if not isinstance(url, str) or g3._URL_FORBIDDEN_RE.search(url):
                self._refuse("url_invalid")
            try:
                parts = urlsplit(url)
                port = parts.port
            except ValueError:
                self._refuse("url_invalid")
            if self._test_port is not None and parts.hostname == "127.0.0.1" and port == self._test_port and parts.scheme == "http":
                kind = "lookup"
            else:
                if parts.scheme != "https":
                    self._refuse("not_https")
                host = parts.hostname or ""
                if parts.netloc != host or host != host.lower() or port is not None or parts.fragment:
                    self._refuse("authority_invalid")
                if host not in ALLOWED_HOSTS:
                    self._refuse("host_not_allowed")
                kind = classify(method, host, parts.path, ALLOWED_PROJECT_ID, self.signer)
                if kind is None:
                    self._refuse("path_not_allowed")
                if kind in ("sign_in", "custom_exchange", "refresh"):
                    if parts.query and not _KEY_QUERY_RE.fullmatch(parts.query):
                        self._refuse("query_not_allowed")
                elif parts.query:
                    self._refuse("query_not_allowed")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= MAX_TIMEOUT_SECONDS:
                self._refuse("timeout_unbounded")
            if kind in MUTATING_KINDS:
                if self.stage not in ("B", "C") or kind not in self.enabled_mutations:
                    self._refuse("mutation_not_confirmed")
                if kind in ("update", "delete") and not self._body_names_only(body, self._bound_uid):
                    self._refuse("mutation_target_not_disposable")
            if self._used[kind] >= self._quota.get(kind, 0):
                self._refuse("kind_quota_exhausted")
            if sum(self._used.values()) >= self.total_cap or sum(self._used.values()) >= GLOBAL_REQUEST_CAP:
                self._refuse("request_cap_reached")
            self._used[kind] += 1
            self.sequence.append(kind)
            return kind

    @staticmethod
    def _body_names_only(body, uid):
        if uid is None:
            return False
        try:
            raw = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
            document = json.loads(raw)
        except (TypeError, ValueError, UnicodeDecodeError):
            return False
        return isinstance(document, dict) and document.get("localId") == uid

    # ---- socket backstop callbacks ------------------------------------------------------------------------------------------
    def allow_dns(self, host):
        # anyio (used by httpx/httpcore) hands the name to the resolver as ASCII/IDNA BYTES; urllib3 passes str. Compare the decoded name EXACTLY.
        if isinstance(host, (bytes, bytearray)):
            try:
                host = bytes(host).decode("ascii")
            except UnicodeDecodeError:
                host = None
        with self._lock:
            if self.tripped is not None:
                raise LiveSafetyStop("guard_tripped")
            if isinstance(host, str) and (host in ALLOWED_HOSTS or (self._test_port is not None and host == "127.0.0.1")):
                self.dns_lookups += 1
                if self.dns_lookups > self.total_cap:
                    self._refuse("dns_cap_reached")
                return
            self._refuse("dns_host_not_allowed")

    def allow_connect(self, loopback):
        with self._lock:
            if self.tripped is not None:
                raise LiveSafetyStop("guard_tripped")
            if loopback and self._test_port is None:
                self.loopback_connections += 1
                if self.loopback_connections > LOOPBACK_CONNECT_CAP:
                    self._refuse("loopback_cap_reached")
                return
            self.connections += 1
            if self.connections > self.total_cap:
                self._refuse("connection_cap_reached")


# ---------------------------------------------------------------------------------------------------------------------------
# Layer 1: urllib3 (requests, firebase_admin, google-auth)
# ---------------------------------------------------------------------------------------------------------------------------

_installed = {"urllib3": None, "socket": None}
CONNECT_TIMEOUT_SECONDS = 5.0


def bounded_timeout(timeout):
    """A fresh urllib3 Timeout whose connect and read limits are numeric and never exceed the caps, whatever the library asked for."""
    from urllib3.util.timeout import Timeout

    def pick(name, cap):
        try:
            value = getattr(timeout, name, None)
        except Exception:
            value = None
        return min(float(value), cap) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else cap

    return Timeout(connect=pick("connect_timeout", CONNECT_TIMEOUT_SECONDS), read=pick("read_timeout", MAX_TIMEOUT_SECONDS))


def install_urllib3_hook(egress):
    """Wraps HTTPConnectionPool._make_request (HTTPS pools inherit it). Every attempt - first try, SDK retry, redirect hop - passes through."""
    import urllib3.connectionpool as cp
    if _installed["urllib3"] is not None:
        raise LiveSafetyStop("hook_already_installed")
    original = cp.HTTPConnectionPool._make_request

    def guarded(self, conn, method, url, *args, **kwargs):
        bounded = bounded_timeout(kwargs.get("timeout"))            # SDK defaults (google-auth uses 120 s, None means "forever") are clamped
        kwargs["timeout"] = bounded
        seconds = max(bounded.connect_timeout, bounded.read_timeout)
        proxied = getattr(self, "proxy", None) is not None
        full = "%s://%s%s%s" % (self.scheme, self.host, "" if self.port in (None, 443, 80) else ":%d" % self.port, url)
        if egress._test_port is not None and self.port == egress._test_port:
            full = "%s://%s:%d%s" % (self.scheme, self.host, self.port, url)
        egress.charge(method, full, kwargs.get("body"), seconds, proxied=proxied)
        response = original(self, conn, method, url, *args, **kwargs)
        if 300 <= getattr(response, "status", 0) < 400:
            egress.tripped = egress.tripped or "redirect_refused"
            raise LiveSafetyStop("redirect_refused")
        return response

    guarded._g8_original = original
    cp.HTTPConnectionPool._make_request = guarded
    _installed["urllib3"] = (cp.HTTPConnectionPool, original, guarded)


def uninstall_urllib3_hook():
    state = _installed["urllib3"]
    if state is not None:
        state[0]._make_request = state[1]
        _installed["urllib3"] = None


# ---------------------------------------------------------------------------------------------------------------------------
# Layer 2: httpx (G7 REST sign-in and signBlob ports)
# ---------------------------------------------------------------------------------------------------------------------------

def guarded_client_factory(egress, timeout=8.0):
    """A `client_factory` for the G7 ports: verified TLS, no redirects, no environment (proxy) inheritance, no transport retries, and every
    request charged to `egress` before it is sent."""
    import httpx
    if not 0 < timeout <= MAX_TIMEOUT_SECONDS:
        raise LiveSafetyStop("timeout_unbounded")

    class GuardedTransport(httpx.AsyncBaseTransport):
        def __init__(self):
            self._inner = httpx.AsyncHTTPTransport(verify=True, trust_env=False, retries=0)

        async def handle_async_request(self, request):
            await request.aread()
            egress.charge(request.method, "%s://%s%s" % (request.url.scheme, request.url.host, request.url.raw_path.decode("ascii")),
                          request.content, request.extensions.get("timeout", {}).get("read", timeout) or timeout)
            response = await self._inner.handle_async_request(request)
            if 300 <= response.status_code < 400:
                egress.tripped = egress.tripped or "redirect_refused"
                raise LiveSafetyStop("redirect_refused")
            return response

        async def aclose(self):
            await self._inner.aclose()

    return lambda: httpx.AsyncClient(transport=GuardedTransport(), timeout=httpx.Timeout(timeout), trust_env=False, follow_redirects=False)


# ---------------------------------------------------------------------------------------------------------------------------
# Layer 3: the stdlib transport of the G3 client (refresh, custom-token exchange, certificates)
# ---------------------------------------------------------------------------------------------------------------------------

def g3_transport(egress):
    """G3's LiveTransport (TLS verified, no proxy, no redirect, response cap) with the G8 budget in place of G3's single-use approval.
    G3's own request validation (`assert_request_safe`) runs first; the budget is charged exactly once, before the connection."""
    import g3_live_transport as lt

    class _ChargedElsewhere:
        @staticmethod
        def consume(method, url):
            return None

    class G8Transport(lt.LiveTransport):
        def __init__(self):
            self._approval, self._factory, self._context = _ChargedElsewhere(), lt.http.client.HTTPSConnection, lt.make_tls_context()

        def __call__(self, request):
            g3.assert_request_safe(request)
            egress.charge(request.method, request.url, request.body, max(request.connect_timeout, request.read_timeout))
            return super().__call__(request)

    return G8Transport()


# ---------------------------------------------------------------------------------------------------------------------------
# The backstop below all of them
# ---------------------------------------------------------------------------------------------------------------------------

def install_socket_backstop(egress):
    if _installed["socket"] is not None:
        raise LiveSafetyStop("hook_already_installed")
    original = (socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex)
    resolved = set()

    def getaddrinfo(host, *args, **kwargs):
        egress.allow_dns(host)
        result = original[0](host, *args, **kwargs)
        for item in result:
            resolved.add(item[4][0])
        return result

    def _check(address):
        ip = address[0] if isinstance(address, tuple) and address else None
        loopback = ip in ("127.0.0.1", "::1")
        if not loopback and ip not in resolved:
            egress.tripped = egress.tripped or "connect_address_not_resolved"
            raise LiveSafetyStop("connect_address_not_resolved")
        egress.allow_connect(loopback)

    def connect(self, address):
        _check(address)
        return original[1](self, address)

    def connect_ex(self, address):
        _check(address)
        return original[2](self, address)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = getaddrinfo, connect, connect_ex
    _installed["socket"] = original


def uninstall_socket_backstop():
    original = _installed["socket"]
    if original is not None:
        socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = original
        _installed["socket"] = None


def uninstall_all():
    uninstall_urllib3_hook()
    uninstall_socket_backstop()


# ---------------------------------------------------------------------------------------------------------------------------
# Can the cap be guaranteed? (If not, live execution must not start.)
# ---------------------------------------------------------------------------------------------------------------------------

def cap_guaranteed(env=None, versions=None, retry_max=None):
    """(True, []) only when every layer is in place and verified; otherwise (False, [fixed reason codes])."""
    reasons = []
    state = _installed["urllib3"]
    try:
        import urllib3.connectionpool as cp
        if state is None or cp.HTTPConnectionPool._make_request is not state[2] or getattr(state[2], "_g8_original", None) is not state[1]:
            reasons.append("urllib3_hook_missing")
    except Exception:
        reasons.append("urllib3_unavailable")
    if _installed["socket"] is None or socket.getaddrinfo.__name__ != "getaddrinfo" or socket.socket.connect.__name__ != "connect":
        reasons.append("socket_backstop_missing")
    versions = versions if versions is not None else _versions()
    for name, prefixes in VALIDATED.items():
        found = versions.get(name)
        if not isinstance(found, str) or not found.startswith(prefixes):
            reasons.append("library_not_validated_" + name)
    if retry_max != 0:
        reasons.append("adapter_retries_not_disabled")
    problem = check_environment(env)
    if problem:
        reasons.append(problem)
    return (not reasons), reasons


def _versions():
    result = {}
    for name in VALIDATED:
        try:
            module = __import__(name)
            result[name] = getattr(module, "__version__", None)
        except Exception:
            result[name] = None
    return result
