"""Shared lab support for the G6b integration tests: starts the isolated lab once per test module, a gateway client that seals real G1
envelopes, synthetic accounts, the leak scan and failure-time diagnostics.

EVERYTHING HERE IS EMULATOR-BASED. The emulator is not real Firebase: nothing proves real Firebase behaviour. Dummy accounts and random
dummy passwords only; credential values never appear in assertion messages.
"""
import base64
import itertools
import json
import os
import secrets
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HARNESS.parent.parent))      # the G1 modules (envelope, testkeys, nonces)

os.environ["FIREBASE_AUTH_EMULATOR_HOST"] = "127.0.0.1:9099"      # the host-side Admin SDK talks to the emulator only
os.environ["FIREBASE_PROJECT_ID"] = "demo-epicverse-g6b"

import firebase_admin  # noqa: E402
from firebase_admin import auth  # noqa: E402

import envelope as G1  # noqa: E402
from orchestrate import BACKENDS, Lab  # noqa: E402

LAB = None
ADMIN = None
SECRETS = set()           # every dummy credential / proof used in the run: none may appear in a log or an error body
RESPONSES = []
UNAVAILABLE_RESPONSES = []      # (path, headers) of every 503 answered by a gateway endpoint
_counter = [0]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EMU = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1/"
INVITE = "EPIC-G6B1"
VARIANTS = []
NO_DATABASE = {"nodb"}


def start(variants):
    global LAB, ADMIN
    VARIANTS[:] = list(variants)
    LAB = Lab(variants=variants).up()
    try:
        ADMIN = firebase_admin.initialize_app(options={"projectId": "demo-epicverse-g6b"}, name="g6b-host-%s" % secrets.token_hex(2))
        for name in variants:
            if name in NO_DATABASE:
                continue                                    # this variant has no reachable database on purpose
            LAB.psql(BACKENDS[name][2], "INSERT INTO invite_codes (code, current_uses, max_uses, created_at) VALUES ('%s', 0, 100000, now())" % INVITE)
    except BaseException:
        LAB.down()                                          # setUpModule failures skip tearDownModule: never leave the lab running
        raise
    return LAB


def stop(scan_backends=None):
    """Leak scan over every backend log and every error response, then tear the lab down."""
    try:
        leaks = []
        logs = "".join(LAB.logs(name) for name in (scan_backends or VARIANTS))
        for secret in SECRETS:
            if secret in logs:
                leaks.append("log")
            if any(secret in body for body in RESPONSES):
                leaks.append("response")
        unavailable = [l.strip() for l in LAB.logs(VARIANTS[0]).splitlines() if "[ENC-AUTH]" in l and "outcome=unavailable" in l]
        print("\n  [diagnostic] %d unavailable (503) events on the %s backend; causes: %s"
              % (len(unavailable), VARIANTS[0], sorted({l.split("cause=")[1].split()[0] for l in unavailable})))
        print("  [leak scan] %d dummy secrets checked against %d backend log characters and %d error-response bodies: %s"
              % (len(SECRETS), len(logs), len(RESPONSES), "LEAK FOUND" if leaks else "none found"))
        if leaks:
            raise AssertionError("credential material found in %s" % sorted(set(leaks)))
    finally:
        LAB.down()


def unique(tag):
    _counter[0] += 1
    return "g6b-%s-%d-%s@example.invalid" % (tag, _counter[0], secrets.token_hex(3))


def new_password():
    value = "G6bpw-" + secrets.token_urlsafe(9)      # satisfies the app's password policy
    SECRETS.add(value)
    return value


def emu_post(path, body):
    request = urllib.request.Request(EMU + path + "?key=fake-api-key", data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with OPENER.open(request, timeout=15) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def claims(jwt):
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def session_from(custom_token):
    status, body = emu_post("accounts:signInWithCustomToken", {"token": custom_token, "returnSecureToken": True})
    assert status == 200, body
    return body


def id_token_for(email, password):
    """A normal e-mail/password session straight from the emulator (carries the e-mail claim), as the app would have."""
    status, body = emu_post("accounts:signInWithPassword", {"email": email, "password": password, "returnSecureToken": True})
    assert status == 200, body
    return body["idToken"]


def forged_token(uid, email=None, project="demo-epicverse-g6b"):
    """An UNSIGNED emulator token (the emulator accepts alg=none), with or without an e-mail claim. Emulator-only."""
    def b(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    now = int(time.time())
    payload = {"iss": "https://securetoken.google.com/" + project, "aud": project, "sub": uid, "user_id": uid, "auth_time": now,
               "iat": now, "exp": now + 3600, "firebase": {"identities": {}, "sign_in_provider": "custom"}}
    if email is not None:
        payload["email"] = email
    return b({"alg": "none", "typ": "JWT"}) + "." + b(payload) + "."


class Gateway:
    """A client for one backend variant. Each instance uses its own X-Forwarded-For address so rate limits do not bleed between tests."""

    ips = itertools.count(1)

    def __init__(self, backend="main", kid="g6b-key-1", ip=None):
        self.backend = backend
        self.port = BACKENDS[backend][1]
        self.db = BACKENDS[backend][2]
        self.kid = kid
        n = next(Gateway.ips)
        self.ip = ip or "10.%d.%d.%d" % (n // 62500 % 250, n // 250 % 250, n % 250 + 1)

    def with_ip(self, ip):
        clone = Gateway(self.backend, self.kid, ip=ip)
        return clone

    def request(self, method, path, data=None, headers=None, timeout=60):
        headers = {"X-Forwarded-For": self.ip, **(headers or {})}
        prefix = "" if path.startswith("/__g6b") else "/api/v1"
        request = urllib.request.Request("http://127.0.0.1:%d%s%s" % (self.port, prefix, path), data=data, method=method, headers=headers)
        try:
            with OPENER.open(request, timeout=timeout) as response:
                status, body, head = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as err:
            status, body, head = err.code, err.read(), dict(err.headers)
        text = body.decode("utf-8", "replace")
        if status >= 400:
            RESPONSES.append(text)
        if status == 503 and path.startswith("/auth/enc/"):
            UNAVAILABLE_RESPONSES.append((path, head))
        try:
            return status, json.loads(text), head
        except ValueError:
            return status, text, head

    def nonce(self, op):
        status, body, _ = self.request("POST", "/auth/enc/nonce", json.dumps({"op": op}).encode(), {"Content-Type": "application/json"})
        assert status == 200, (status, body)
        return body["nonce"]

    def seal(self, op, fields):
        for value in fields.values():
            SECRETS.add(value)
        return G1.seal_credentials(LAB.public_key(self.kid), self.kid, op, self.nonce(op), fields, now=int(time.time()))

    def call(self, op, fields, bearer=None, proof=None):
        envelope = self.seal(op, fields)
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        if proof:
            headers["X-Email-Proof"] = proof
            SECRETS.add(proof)
        status, body, head = self.request("POST", "/auth/enc/" + op, envelope.encode(), headers)
        if isinstance(body, dict) and body.get("custom_token"):
            SECRETS.add(body["custom_token"])
        return status, body, head

    def login(self, email, password):
        return self.call("login", {"email": email, "password": password})

    def proof(self, email):
        status, body, _ = self.request("POST", "/__g6b/issue_proof", json.dumps({"email": email}).encode(), {"Content-Type": "application/json"})
        assert body["result"] == "success", body
        SECRETS.add(body["proof"])
        return body["proof"]

    def age_proof(self, proof, minutes):
        return self.post_json("/__g6b/age_proof", {"proof": proof, "minutes": minutes})

    def post_json(self, path, data):
        return self.request("POST", path, json.dumps(data).encode(), {"Content-Type": "application/json"})

    def bearer_post(self, path, token, json_body=None, form=None):
        headers = {"Authorization": "Bearer " + token}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode()
        else:
            data = urllib.parse.urlencode(form or {}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        return self.request("POST", path, data, headers)

    def set_faults(self, **faults):
        self.post_json("/__g6b/faults", faults)

    def clear_faults(self):
        self.post_json("/__g6b/faults", {})

    def diag(self):
        return self.request("GET", "/__g6b/diag")[1]

    def diag_reset(self):
        self.post_json("/__g6b/diag/reset", {})

    def sync_user(self, token, uid, email, invite=INVITE):
        return self.bearer_post("/sync-user", token, json_body={"firebase_id": uid, "display_name": "G6B", "email": email,
                                                                "invite_code": invite, "session_id": "s"})

    def sql(self, query):
        return LAB.psql(self.db, query)


def make_user(email=None, password=None, **kw):
    email, password = email or unique("u"), password or new_password()
    SECRETS.add(password)
    user = auth.create_user(email=email, password=password, app=ADMIN, **kw)
    return {"email": email, "password": password, "uid": user.uid}


class LabTestCase(unittest.TestCase):
    """Takes failure-time diagnostics BEFORE the lab is torn down (printed by Lab.down())."""

    backend_for_diag = "main"

    def run(self, result=None):
        before = (len(result.failures), len(result.errors)) if result is not None else (0, 0)
        out = super().run(result)
        if result is not None and (len(result.failures), len(result.errors)) != before and LAB is not None:
            LAB.capture(self.id().split(".", 1)[-1], self.backend_for_diag)
        return out
