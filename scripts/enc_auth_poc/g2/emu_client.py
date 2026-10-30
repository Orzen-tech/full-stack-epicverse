"""G2 helpers: a tiny REST client for the LOCAL Firebase Auth emulator plus the isolation guard.

EMULATOR ONLY. Every call is refused unless it targets exactly 127.0.0.1:9099, the project id is a `demo-` id and no Google
credential variable is set. Nothing here can reach real Firebase, Google Cloud or any other external service.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

HOST = "127.0.0.1:9099"
PROJECT = "demo-epicverse-g2"
API_KEY = "fake-api-key"  # the emulator ignores the key; it is not a real key
_BASE = "http://%s" % HOST
_IT = _BASE + "/identitytoolkit.googleapis.com/v1/"
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never use a proxy, even if one is configured
_FORBIDDEN_ENV = ("GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT_CREDENTIALS", "FIREBASE_TOKEN", "FIREBASE_CONFIG")


class IsolationError(RuntimeError):
    pass


def require_demo_project(project_id):
    if not isinstance(project_id, str) or not project_id.startswith("demo-"):
        raise IsolationError("project id is not a demo- id")


def require_isolation(env=None):
    env = os.environ if env is None else env
    if env.get("FIREBASE_AUTH_EMULATOR_HOST") != HOST:
        raise IsolationError("FIREBASE_AUTH_EMULATOR_HOST must be exactly %s" % HOST)
    for name in _FORBIDDEN_ENV:
        if env.get(name):
            raise IsolationError("%s must not be set during emulator tests" % name)
    require_demo_project(PROJECT)


def _call(method, url, body=None, headers=None, form=False):
    require_isolation()
    if not url.startswith(_BASE + "/"):
        raise IsolationError("refusing to call a URL outside the local emulator")
    data, hdrs = None, dict(headers or {})
    if body is not None:
        if form:
            data = urllib.parse.urlencode(body).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with _OPENER.open(req, timeout=15) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        parsed = {}
    return status, parsed


def error_code(js):
    err = js.get("error") if isinstance(js, dict) else None
    msg = err.get("message", "") if isinstance(err, dict) else (err or "")
    return str(msg).split(" ")[0].split(":")[0]


def ready():
    status, js = _call("GET", _BASE + "/")
    return status == 200 and isinstance(js, dict) and "authEmulator" in js


def clear_accounts():
    return _call("DELETE", "%s/emulator/v1/projects/%s/accounts" % (_BASE, PROJECT))[0]


def sign_up(email, password):
    return _call("POST", _IT + "accounts:signUp?key=" + API_KEY,
                 {"email": email, "password": password, "returnSecureToken": True})


def sign_in_password(email, password):
    return _call("POST", _IT + "accounts:signInWithPassword?key=" + API_KEY,
                 {"email": email, "password": password, "returnSecureToken": True})


def sign_in_custom_token(token):
    return _call("POST", _IT + "accounts:signInWithCustomToken?key=" + API_KEY,
                 {"token": token, "returnSecureToken": True})


def lookup(id_token):
    return _call("POST", _IT + "accounts:lookup?key=" + API_KEY, {"idToken": id_token})


def update_password(id_token, new_password):
    return _call("POST", _IT + "accounts:update?key=" + API_KEY,
                 {"idToken": id_token, "password": new_password, "returnSecureToken": True})


def refresh(refresh_token):
    return _call("POST", "%s/securetoken.googleapis.com/v1/token?key=%s" % (_BASE, API_KEY),
                 {"grant_type": "refresh_token", "refresh_token": refresh_token}, form=True)


def claims(jwt):
    """Decodes the payload WITHOUT verifying anything (emulator tokens are unsigned). For tests only."""
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
