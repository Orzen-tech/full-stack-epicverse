"""Shared helpers for the contract client: unsigned emulator-style Firebase tokens and a tiny HTTP wrapper (stdlib only)."""
import base64, json, time, urllib.request, urllib.error, urllib.parse, uuid
PROJECT = "demo-epicverse"
def _b(d): return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
def token(uid, email, auth_time=None):
    now = int(time.time()); at = auth_time or now
    p = {"iss": "https://securetoken.google.com/" + PROJECT, "aud": PROJECT, "sub": uid, "user_id": uid, "auth_time": at,
         "iat": now, "exp": now + 3600, "email": email, "email_verified": False,
         "firebase": {"identities": {"email": [email]}, "sign_in_provider": "password"}}
    return _b({"alg": "none", "typ": "JWT"}) + "." + _b(p) + "."
def call(base, method, path, tok=None, form=None, js=None, headers=None, timeout=20):
    h = dict(headers or {}); data = None
    if tok: h["Authorization"] = "Bearer " + tok
    if form is not None: data = urllib.parse.urlencode(form).encode(); h["Content-Type"] = "application/x-www-form-urlencoded"
    if js is not None: data = json.dumps(js).encode(); h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + "/api/v1" + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: code, body = r.status, r.read()
    except urllib.error.HTTPError as e: code, body = e.code, e.read()
    try: body = json.loads(body)
    except Exception: body = body.decode("utf-8", "replace")[:200]
    return code, body
