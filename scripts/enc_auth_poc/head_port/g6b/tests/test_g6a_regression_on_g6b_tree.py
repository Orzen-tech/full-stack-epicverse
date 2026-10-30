"""G6a REGRESSION SUITE RUN AGAINST THE G6b-PATCHED TREE. This is a G6b-only copy of the G6a integration tests (G6a itself is untouched
and is also run, unchanged, against its own tree). Edits relative to the G6a original: lab names/ports (g6b), the helper route prefix,
the end-of-run diagnostic print, and the tests whose expectations the approved G6b policy deliberately changed (failure rate limiting)
- each such change is marked `G6b:` below.

G6b integration tests: Python client (G1 envelope sealer) -> loopback-published port -> isolated backend container (the patched
deployed lineage) -> Postgres + the LOCAL Firebase Auth emulator.

EVERYTHING HERE IS EMULATOR-BASED. The emulator is not real Firebase: nothing below proves real Firebase behaviour (custom-token
claims, signing, refresh/revocation at refresh time, recent-login rules, password-policy enforcement by Admin, throttling). Dummy
accounts and random dummy passwords only; credential values never appear in assertion messages.
"""
import base64
import concurrent.futures
import itertools
import json
import os
import secrets
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HARNESS = Path(__file__).resolve().parent.parent / "harness"
sys.path.insert(0, str(HARNESS))
sys.path.insert(0, str(HARNESS.parent.parent.parent))      # the G1 modules (envelope, testkeys, nonces) in the real scripts/enc_auth_poc
sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ["FIREBASE_AUTH_EMULATOR_HOST"] = "127.0.0.1:9099"      # the host-side Admin SDK talks to the emulator only
os.environ["FIREBASE_PROJECT_ID"] = "demo-epicverse-g6b"

import firebase_admin  # noqa: E402
from firebase_admin import auth  # noqa: E402

import envelope as G1  # noqa: E402  (G1 reference sealer)
from orchestrate import BACKENDS, REGRESSION, Lab  # noqa: E402

LAB = None
ADMIN = None
SECRETS = set()           # every dummy credential / proof used in the run: none may appear in a log or an error body
RESPONSES = []
_counter = [0]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
EMU = "http://127.0.0.1:9099/identitytoolkit.googleapis.com/v1/"
INVITE = "EPIC-G6B1"


def setUpModule():
    global LAB, ADMIN
    LAB = Lab(variants=REGRESSION).up()
    ADMIN = firebase_admin.initialize_app(options={"projectId": "demo-epicverse-g6b"}, name="g6b-host")
    LAB.psql("g6b_main", "INSERT INTO invite_codes (code, current_uses, max_uses, created_at) VALUES ('%s', 0, 100000, now())" % INVITE)


def tearDownModule():
    try:
        leaks = []
        logs = "".join(LAB.logs(name) for name in REGRESSION)
        for secret in SECRETS:
            if secret in logs:
                leaks.append("log")
            if any(secret in body for body in RESPONSES):
                leaks.append("response")
        main_log = LAB.logs("main").splitlines()
        unavailable = [line.strip() for line in main_log if "[ENC-AUTH]" in line and "outcome=unavailable" in line]
        for line in unavailable:                             # request id, stage and cause category: shows WHY any 503 happened
            print("  [gateway diagnostic]", line[:160])
        print("  [gateway diagnostic] unavailable (503) events on the main backend: %d" % len(unavailable))
        print("\n  [leak scan] %d dummy secrets checked against %d backend log characters and %d response bodies: %s"
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


def emu_refresh(refresh_token):
    request = urllib.request.Request("http://127.0.0.1:9099/securetoken.googleapis.com/v1/token?key=fake-api-key",
                                     data=urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh_token}).encode(),
                                     method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
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


class Gateway:
    """A client for one backend variant. Each instance uses its own X-Forwarded-For address so rate limits do not bleed between tests."""

    ips = itertools.count(1)

    def __init__(self, backend="main", kid="g6b-key-1", via_port=None):
        self.backend = backend
        self.port = via_port or BACKENDS[backend][1]
        self.kid = kid
        n = next(Gateway.ips)
        self.ip = "10.%d.%d.%d" % (n // 62500 % 250, n // 250 % 250, n % 250 + 1)   # unique per client

    def request(self, method, path, data=None, headers=None):
        headers = {"X-Forwarded-For": self.ip, **(headers or {})}
        prefix = "" if path.startswith("/__g6b") else "/api/v1"          # the lab helper routes are not under the API prefix
        request = urllib.request.Request("http://127.0.0.1:%d%s%s" % (self.port, prefix, path), data=data, method=method, headers=headers)
        try:
            with OPENER.open(request, timeout=30) as response:
                status, body, head = response.status, response.read(), dict(response.headers)
        except urllib.error.HTTPError as err:
            status, body, head = err.code, err.read(), dict(err.headers)
        text = body.decode("utf-8", "replace")
        if status >= 400:
            RESPONSES.append(text)          # error bodies are scanned for leaks (success bodies legitimately hold a token)
        try:
            return status, json.loads(text), head
        except ValueError:
            return status, text, head

    def nonce(self, op):
        status, body, _ = self.request("POST", "/auth/enc/nonce", json.dumps({"op": op}).encode(), {"Content-Type": "application/json"})
        assert status == 200, (status, body)
        return body["nonce"]

    def seal(self, op, fields, kid=None, nonce=None, ts=None, key_from=None):
        for value in fields.values():
            SECRETS.add(value)
        pub = LAB.public_key(key_from or kid or self.kid)
        return G1.seal_credentials(pub, kid or self.kid, op, nonce or self.nonce(op), fields, now=int(time.time()) if ts is None else ts)

    def call(self, op, fields=None, bearer=None, proof=None, raw=None, **seal_kw):
        envelope = raw if raw is not None else self.seal(op, fields, **seal_kw)
        headers = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        if proof:
            headers["X-Email-Proof"] = proof
            SECRETS.add(proof)
        status, body, head = self.request("POST", "/auth/enc/" + op, envelope.encode() if isinstance(envelope, str) else envelope, headers)
        for key in ("custom_token",):
            if isinstance(body, dict) and body.get(key):
                SECRETS.add(body[key])
        return status, body, head

    def proof(self, email):
        status, body, _ = self.request("POST", "/__g6b/issue_proof", json.dumps({"email": email}).encode(), {"Content-Type": "application/json"})
        assert body["result"] == "success", body
        SECRETS.add(body["proof"])
        return body["proof"]

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
        self.request("POST", "/__g6b/faults", json.dumps(faults).encode(), {"Content-Type": "application/json"})


def make_user(email=None, password=None, **kw):
    email, password = email or unique("u"), password or new_password()
    SECRETS.add(password)
    user = auth.create_user(email=email, password=password, app=ADMIN, **kw)
    return {"email": email, "password": password, "uid": user.uid}


def user_exists(email):
    try:
        auth.get_user_by_email(email, app=ADMIN)
        return True
    except auth.UserNotFoundError:
        return False


GENERIC = {"error": "AUTH_FAILED"}


class T00_Isolation(unittest.TestCase):
    def test_the_lab_is_isolated_from_the_outside_world(self):
        self.assertIn("127.0.0.1:9099", LAB.report["emulator"])
        self.assertIn("no route out", LAB.report["docker"])
        print("  [isolation]", LAB.report["docker"])

    def test_the_gateway_refuses_non_local_identity_environments(self):
        for backend in ("notdemo", "noemu"):
            g = Gateway(backend)
            status, body, _ = g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})
            self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}), backend)
            self.assertEqual(g.call("login", raw="{}")[:2], (503, {"error": "AUTH_UNAVAILABLE"}))


class T01_Signup(unittest.TestCase):
    def signup(self, email=None, password=None, proof="auto", gateway=None):
        g = gateway or Gateway()
        email, password = email or unique("s"), password or new_password()
        if proof == "auto":
            proof = g.proof(email)
        return g, email, password, proof

    def test_first_time_signup_then_sync_user_and_mark_verified(self):
        g, email, password, proof = self.signup()
        status, body, head = g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((status, body["created"], "recovered" in body), (200, True, False))
        self.assertEqual(head.get("cache-control") or head.get("Cache-Control"), "no-store")
        user = auth.get_user_by_email(email, app=ADMIN)
        self.assertFalse(user.email_verified)                         # D2: created with email_verified=false
        # the proof was only CHECKED by the gateway, not consumed: /auth/mark-verified below still accepts it
        session = session_from(body["custom_token"])
        self.assertEqual(claims(session["idToken"])["user_id"], user.uid)
        status, out, _ = g.bearer_post("/sync-user", session["idToken"], json_body={
            "firebase_id": user.uid, "display_name": "G6B", "email": email, "invite_code": INVITE, "session_id": "s1"})
        self.assertEqual(status, 200, out)
        status, out, _ = g.bearer_post("/auth/mark-verified", session["idToken"], form={"proof": proof})
        self.assertEqual((status, out), (200, {"status": "ok"}))
        self.assertEqual(LAB.psql("g6b_main", "SELECT email_verified FROM users WHERE uid='%s'" % user.uid), "t")
        # the one-time proof is now consumed: it cannot create another account
        status, body2, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((status, body2), (403, {"error": "email-proof-invalid"}))

    def test_missing_wrong_email_expired_and_malformed_proofs_are_rejected_and_create_nothing(self):
        g = Gateway()
        email, other, password = unique("p"), unique("p"), new_password()
        good_for_other = g.proof(other)
        expired = g.proof(email)
        attempts = {"missing": None, "wrong-email": good_for_other, "malformed": "not-a-proof", "unknown": "evp1_" + "z" * 40,
                    "too-long": "evp1_" + "z" * 400}
        for name, proof in attempts.items():
            status, body, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
            self.assertEqual((status, body), (403, {"error": "email-proof-invalid"}), name)
        self.assertFalse(user_exists(email))
        # expire the (only) proof issued for `email`, by e-mail hash: done through the stored row's age
        LAB.psql("g6b_main", "UPDATE email_verification_proofs_v2 SET created_at = now() - interval '2 minutes', expires_at = now() - interval '1 minute' WHERE consumed_at IS NULL "
                 "AND created_at > now() - interval '10 seconds'")
        status, body, _ = g.call("signup", {"email": email, "password": password}, proof=expired)
        self.assertEqual((status, body), (403, {"error": "email-proof-invalid"}))
        self.assertFalse(user_exists(email))
        self.assertFalse(user_exists(other))

    def test_weak_password_is_refused_before_anything_is_created(self):
        g = Gateway()
        email = unique("w")
        proof = g.proof(email)
        for weak in ("short1!", "alllowercase1!", "ALLUPPERCASE1!", "NoDigitsHere!", "NoSpecial123x"):
            status, body, _ = g.call("signup", {"email": email, "password": weak}, proof=proof)
            self.assertEqual((status, body), (400, {"error": "weak-password"}), weak)
        self.assertFalse(user_exists(email))

    def test_duplicate_signup_with_the_same_password_recovers_the_same_account(self):
        g = Gateway()
        email, password = unique("d"), new_password()
        proof = g.proof(email)
        s1, first, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        s2, second, _ = g.call("signup", {"email": email, "password": password}, proof=proof)      # lost response -> retry
        self.assertEqual((s1, s2, first["created"], second.get("created"), second.get("recovered")), (200, 200, True, False, True))
        uid = auth.get_user_by_email(email, app=ADMIN).uid
        self.assertEqual({claims(session_from(t["custom_token"])["idToken"])["user_id"] for t in (first, second)}, {uid})

    def test_duplicate_signup_with_a_different_password_is_refused_and_changes_nothing(self):
        g = Gateway()
        victim = make_user()
        proof = g.proof(victim["email"])
        status, body, _ = g.call("signup", {"email": victim["email"], "password": new_password()}, proof=proof)
        self.assertEqual((status, body), (400, {"error": "email-already-in-use"}))
        self.assertEqual(g.call("login", {"email": victim["email"], "password": victim["password"]})[0], 200)   # password unchanged

    def test_existing_disabled_account_and_non_password_account_get_the_same_refusal(self):
        g = Gateway()
        disabled = make_user()
        auth.update_user(disabled["uid"], disabled=True, app=ADMIN)
        no_password = unique("np")
        auth.create_user(email=no_password, app=ADMIN)                       # an account without a password provider
        results = []
        for email, password in ((disabled["email"], disabled["password"]), (no_password, new_password())):
            proof = g.proof(email)
            results.append(g.call("signup", {"email": email, "password": password}, proof=proof)[:2])
        self.assertEqual(results, [(400, {"error": "email-already-in-use"})] * 2)

    def test_concurrent_signups_create_exactly_one_account(self):
        email, password, other_password = unique("c"), new_password(), new_password()
        g0 = Gateway()
        proof = g0.proof(email)

        def attempt(index):
            g = Gateway()
            return g.call("signup", {"email": email, "password": password if index % 4 else other_password}, proof=proof)

        with concurrent.futures.ThreadPoolExecutor(8) as pool:
            results = list(pool.map(attempt, range(8)))
        statuses = [(r[0], r[1].get("created"), r[1].get("recovered"), r[1].get("error")) for r in results]
        created = [s for s in statuses if s[1] is True]
        self.assertEqual(len(created), 1, statuses)
        self.assertTrue(all(s[0] in (200, 400, 429) for s in statuses), statuses)     # 429: the failure limiter doing its job
        users = [u for u in auth.list_users(app=ADMIN).iterate_all() if u.email == email]
        self.assertEqual(len(users), 1)
        winner_password = password if statuses.index(created[0]) % 4 else other_password
        # checked against the emulator directly, because the gateway's own failure limiter may legitimately be blocking this e-mail now
        self.assertEqual(emu_post("accounts:signInWithPassword", {"email": email, "password": winner_password, "returnSecureToken": True})[0], 200)

    def test_firebase_account_created_but_sql_sync_fails_then_retry_completes(self):
        g = Gateway()
        email, password = unique("sql"), new_password()
        proof = g.proof(email)
        s, body, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        uid = auth.get_user_by_email(email, app=ADMIN).uid
        session = session_from(body["custom_token"])
        status, out, _ = g.bearer_post("/sync-user", session["idToken"], json_body={
            "firebase_id": uid, "display_name": "G6B", "email": email, "invite_code": "EPIC-DOESNOTEXIST", "session_id": "s"})
        self.assertEqual(status, 403)                                              # SQL synchronisation fails (invalid invite)
        self.assertEqual(LAB.psql("g6b_main", "SELECT count(*) FROM users WHERE uid='%s'" % uid), "0")
        # the response was "lost": the client retries the signup, recovers the SAME account, and finishes synchronisation
        s2, again, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((s2, again.get("recovered"), len([u for u in auth.list_users(app=ADMIN).iterate_all() if u.email == email])),
                         (200, True, 1))
        session2 = session_from(again["custom_token"])
        status, out, _ = g.bearer_post("/sync-user", session2["idToken"], json_body={
            "firebase_id": uid, "display_name": "G6B", "email": email, "invite_code": INVITE, "session_id": "s"})
        self.assertEqual(status, 200, out)
        self.assertEqual(LAB.psql("g6b_main", "SELECT count(*) FROM users WHERE uid='%s'" % uid), "1")

    def test_signup_is_rate_limited_per_ip(self):
        g = Gateway("rate")
        statuses = []
        for _ in range(4):
            email = unique("rl")
            statuses.append(g.call("signup", {"email": email, "password": new_password()}, proof=g.proof(email))[0])
        self.assertEqual(statuses, [200, 200, 200, 429])


class T02_Login(unittest.TestCase):
    def test_correct_password_returns_a_token_for_the_verified_uid(self):
        user = make_user()
        g = Gateway()
        status, body, _ = g.call("login", {"email": user["email"], "password": user["password"]})
        self.assertEqual(status, 200)
        self.assertEqual(claims(session_from(body["custom_token"])["idToken"])["user_id"], user["uid"])
        self.assertEqual(set(body), {"custom_token"})
        self.assertNotIn(user["password"], json.dumps(body))

    def test_wrong_password_and_unknown_email_are_indistinguishable(self):
        user = make_user()
        g = Gateway()
        wrong = g.call("login", {"email": user["email"], "password": new_password()})
        unknown = g.call("login", {"email": unique("nobody"), "password": new_password()})
        self.assertEqual(wrong[:2], (400, {"error": "invalid-credential"}))
        self.assertEqual(unknown[:2], wrong[:2])

    def test_client_cannot_choose_the_uid(self):
        user, other = make_user(), make_user()
        g = Gateway()
        # extra fields in the envelope are refused outright ...
        raw = g.seal("login", {"email": user["email"], "password": user["password"]})
        d = json.loads(raw)
        d["uid"] = other["uid"]
        self.assertEqual(g.call("login", raw=json.dumps(d))[:2], (400, GENERIC))
        # ... and a uid header is ignored: the token is for the uid the identity service verified
        token = g.call("login", {"email": user["email"], "password": user["password"]})[1]["custom_token"]
        self.assertEqual(claims(session_from(token)["idToken"])["user_id"], user["uid"])

    def test_disabled_account_is_refused(self):
        user = make_user()
        auth.update_user(user["uid"], disabled=True, app=ADMIN)
        g = Gateway()
        right = g.call("login", {"email": user["email"], "password": user["password"]})
        wrong = g.call("login", {"email": user["email"], "password": new_password()})
        self.assertEqual(right[:2], (400, {"error": "user-disabled"}))
        print("  [observed, emulator] disabled account with a WRONG password answers:", wrong[1])   # recorded, not asserted

    def test_failed_logins_are_rate_limited_per_email_and_per_ip(self):
        user = make_user()
        g = Gateway("rate")
        results = [g.call("login", {"email": user["email"], "password": new_password()})[0] for _ in range(3)]
        status, body, head = g.call("login", {"email": user["email"], "password": user["password"]})   # even the right one is blocked
        self.assertEqual((results, status, body), ([400, 400, 400], 429, {"error": "too-many-requests"}))
        self.assertGreaterEqual(int(head.get("retry-after") or head.get("Retry-After")), 1)
        # another e-mail from another address is not affected
        other = make_user()
        self.assertEqual(Gateway("rate").call("login", {"email": other["email"], "password": other["password"]})[0], 200)
        # one address failing against many e-mails hits the per-IP failure limit (6)
        ip_gateway = Gateway("rate")
        codes = [ip_gateway.call("login", {"email": unique("x"), "password": new_password()})[0] for _ in range(8)]
        self.assertEqual(codes[:6], [400] * 6)
        self.assertEqual(codes[6], 429)

    def test_request_rate_limit_per_ip(self):
        g = Gateway("rate")
        codes = [g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})[0] for _ in range(42)]
        self.assertEqual(codes[:40], [200] * 40)
        self.assertEqual(set(codes[40:]), {429})


class T03_Reauth(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.g = Gateway()
        self.session = session_from(self.g.call("login", {"email": self.user["email"], "password": self.user["password"]})[1]["custom_token"])

    def test_correct_account_gets_a_fresh_token_with_a_later_auth_time_in_the_emulator(self):
        before = claims(self.session["idToken"])["auth_time"]
        time.sleep(1.3)
        status, body, _ = self.g.call("reauth", {"email": self.user["email"], "password": self.user["password"]}, bearer=self.session["idToken"])
        self.assertEqual(status, 200)
        after = claims(session_from(body["custom_token"])["idToken"])
        self.assertGreater(after["auth_time"], before)           # EMULATOR ONLY: real Firebase behaviour is unverified (G3)
        self.assertEqual(after["user_id"], self.user["uid"])

    def test_wrong_password_missing_bearer_and_bad_bearer(self):
        self.assertEqual(self.g.call("reauth", {"email": self.user["email"], "password": new_password()}, bearer=self.session["idToken"])[:2],
                         (400, {"error": "invalid-credential"}))
        self.assertEqual(self.g.call("reauth", {"email": self.user["email"], "password": self.user["password"]})[:2], (401, GENERIC))
        self.assertEqual(self.g.call("reauth", {"email": self.user["email"], "password": self.user["password"]}, bearer="garbage")[:2],
                         (401, GENERIC))

    def test_uid_and_email_mismatch_never_yield_a_token(self):
        other = make_user()
        # valid credentials of ANOTHER account with this account's bearer token
        status, body, _ = self.g.call("reauth", {"email": other["email"], "password": other["password"]}, bearer=self.session["idToken"])
        self.assertEqual((status, body), (400, {"error": "invalid-credential"}))
        # this account's e-mail with the other account's password
        status, body, _ = self.g.call("reauth", {"email": self.user["email"], "password": other["password"]}, bearer=self.session["idToken"])
        self.assertEqual((status, body), (400, {"error": "invalid-credential"}))

    def test_revoked_session_cannot_reauthenticate(self):
        time.sleep(1.1)
        auth.revoke_refresh_tokens(self.user["uid"], app=ADMIN)
        time.sleep(0.5)
        self.assertEqual(self.g.call("reauth", {"email": self.user["email"], "password": self.user["password"]},
                                     bearer=self.session["idToken"])[:2], (401, GENERIC))

    def test_disabled_account_cannot_reauthenticate(self):
        auth.update_user(self.user["uid"], disabled=True, app=ADMIN)
        self.assertEqual(self.g.call("reauth", {"email": self.user["email"], "password": self.user["password"]},
                                     bearer=self.session["idToken"])[0], 401)


class T04_PasswordChange(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.g = Gateway()
        self.session = session_from(self.g.call("login", {"email": self.user["email"], "password": self.user["password"]})[1]["custom_token"])

    def change(self, new, current=None, bearer=None, gateway=None):
        return (gateway or self.g).call("change_password", {"email": self.user["email"], "password": current or self.user["password"],
                                                          "new_password": new}, bearer=bearer or self.session["idToken"])

    def login(self, password):
        return Gateway().call("login", {"email": self.user["email"], "password": password})[0]

    def test_correct_current_password_changes_it_and_ends_the_other_session_in_the_emulator(self):
        new = new_password()
        time.sleep(1.1)
        status, body, _ = self.change(new)
        self.assertEqual((status, body["password_changed"], body["sessions_revoked"], bool(body["custom_token"])), (200, True, True, True))
        self.assertEqual((self.login(self.user["password"]), self.login(new)), (400, 200))
        with self.assertRaises(auth.RevokedIdTokenError):                      # simulated revocation of the old session
            auth.verify_id_token(self.session["idToken"], app=ADMIN, check_revoked=True)
        fresh = session_from(body["custom_token"])
        self.assertTrue(auth.verify_id_token(fresh["idToken"], app=ADMIN, check_revoked=True)["uid"])
        status, _ = emu_refresh(self.session["refreshToken"])
        print("  [observed, emulator] the OLD refresh token after the change answers HTTP %d (real Firebase: UNVERIFIED, G3)" % status)

    def test_wrong_current_weak_new_and_unchanged_passwords_change_nothing(self):
        self.assertEqual(self.change(new_password(), current=new_password())[:2], (400, {"error": "invalid-credential"}))
        for weak in ("short1!", "nouppercase1!", "NOLOWERCASE1!", "NoDigitsHere!", "NoSpecial123x"):
            self.assertEqual(self.change(weak)[:2], (400, {"error": "weak-password"}), weak)
        self.assertEqual(self.change(self.user["password"])[:2], (400, {"error": "password-unchanged"}))
        self.assertEqual(self.login(self.user["password"]), 200)

    def test_update_failure_is_reported_as_unknown_and_nothing_changed(self):
        self.g.set_faults(update_password=True)
        try:
            status, body, _ = self.change(new_password())
        finally:
            self.g.set_faults()
        self.assertEqual((status, body["error"], body["password_changed"]), (503, "AUTH_UNAVAILABLE", "unknown"))
        self.assertEqual(self.login(self.user["password"]), 200)

    def test_a_lost_update_response_never_claims_the_password_was_unchanged(self):
        new = new_password()
        self.g.set_faults(update_then_fail=True)                              # the update IS applied, the answer is lost
        try:
            status, body, _ = self.change(new)
        finally:
            self.g.set_faults()
        self.assertEqual((status, body["password_changed"]), (503, "unknown"))
        self.assertNotEqual(body["password_changed"], False)
        self.assertEqual(self.login(new), 200)                                # and indeed it was changed

    def test_revocation_failure_still_returns_a_session_and_says_so(self):
        new = new_password()
        self.g.set_faults(revoke_refresh_tokens=True)
        try:
            status, body, _ = self.change(new)
        finally:
            self.g.set_faults()
        self.assertEqual((status, body["password_changed"], body["sessions_revoked"], bool(body["custom_token"])), (200, True, False, True))
        self.assertEqual(self.login(new), 200)

    def test_token_minting_failure_after_a_successful_update_is_a_distinct_safe_outcome(self):
        new = new_password()
        self.g.set_faults(mint_custom_token=True)
        try:
            status, body, _ = self.change(new)
        finally:
            self.g.set_faults()
        self.assertEqual((status, body["password_changed"], body["custom_token"], body["retry_signin"]), (200, True, None, True))
        self.assertEqual(self.login(new), 200)

    def test_concurrent_changes_leave_exactly_one_new_password(self):
        a, b = new_password(), new_password()

        def attempt(new):
            return self.change(new, gateway=Gateway())

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            ra, rb = pool.map(attempt, (a, b))
        self.assertTrue(all(r[0] in (200, 400, 401) for r in (ra, rb)), (ra[:2], rb[:2]))   # 401: the first change revoked the shared bearer token
        works = [self.login(p) == 200 for p in (a, b)]
        self.assertEqual(sum(works) + (1 if self.login(self.user["password"]) == 200 else 0), 1, (ra[:2], rb[:2], works))

    def test_other_accounts_credentials_cannot_change_this_password(self):
        other = make_user()
        status, body, _ = self.g.call("change_password", {"email": other["email"], "password": other["password"], "new_password": new_password()},
                                      bearer=self.session["idToken"])
        self.assertEqual((status, body), (400, {"error": "invalid-credential"}))
        self.assertEqual(Gateway().call("login", {"email": other["email"], "password": other["password"]})[0], 200)


class T05_EnvelopeAndOperations(unittest.TestCase):
    def setUp(self):
        self.user = make_user()
        self.g = Gateway()
        self.fields = {"email": self.user["email"], "password": self.user["password"]}

    def good(self):
        return self.g.seal("login", self.fields)

    def test_tampered_ciphertext_and_metadata_are_rejected_without_burning_the_nonce(self):
        raw = self.good()
        d = json.loads(raw)
        flip = lambda s: s[:10] + ("A" if s[10] != "A" else "B") + s[11:]       # noqa: E731
        for name, value in (("ct", flip(d["ct"])), ("ek", flip(d["ek"])), ("iv", flip(d["iv"])), ("ts", d["ts"] + 1), ("kid", "g6b-key-2"),
                            ("op", "signup"), ("nonce", self.g.nonce("login"))):
            mutated = dict(d)
            mutated[name] = value
            self.assertEqual(self.g.call("login", raw=json.dumps(mutated))[:2], (400, GENERIC), name)
        self.assertEqual(self.g.call("login", raw=raw)[0], 200)                 # the genuine envelope still works exactly once

    def test_wrong_operation_endpoint(self):
        raw = self.good()
        self.assertEqual(self.g.call("signup", raw=raw, proof="evp1_x")[:2], (400, GENERIC))
        self.assertEqual(self.g.call("login", raw=raw)[0], 200)

    def test_expired_nonce(self):
        raw = self.good()
        LAB.psql("g6b_main", "UPDATE enc_auth_nonces SET expires_at = now() - interval '1 second' WHERE consumed_at IS NULL "
                 "AND issued_at > now() - interval '30 seconds'")
        self.assertEqual(self.g.call("login", raw=raw)[:2], (400, GENERIC))

    def test_replay_is_rejected(self):
        raw = self.good()
        self.assertEqual(self.g.call("login", raw=raw)[0], 200)
        self.assertEqual(self.g.call("login", raw=raw)[:2], (400, GENERIC))

    def test_concurrent_consumption_of_one_nonce_has_exactly_one_winner(self):
        raw = self.good()
        with concurrent.futures.ThreadPoolExecutor(12) as pool:
            results = list(pool.map(lambda _: Gateway().call("login", raw=raw)[0], range(12)))
        self.assertEqual((results.count(200), results.count(400)), (1, 11))

    def test_unknown_key_malformed_empty_and_oversized_envelopes_all_get_the_same_answer(self):
        raw = self.good()
        d = json.loads(raw)
        d["kid"] = "no-such-key"
        cases = {"unknown key": json.dumps(d), "garbage": "not json", "empty": "", "array": "[]", "oversized": " " * 9000,
                 "extra field": json.dumps({**json.loads(raw), "x": 1}), "never issued nonce": self.g.seal("login", self.fields, nonce="A" * 43)}
        answers = {name: self.g.call("login", raw=value)[:2] for name, value in cases.items()}
        self.assertTrue(all(v == (400, GENERIC) for v in answers.values()), answers)

    def test_key_rotation_overlap_and_retirement(self):
        user = make_user()
        both, retired = Gateway("rotate"), Gateway("retired")
        fields = {"email": user["email"], "password": user["password"]}
        self.assertEqual(both.call("login", fields, kid="g6b-key-1")[0], 200)
        self.assertEqual(both.call("login", fields, kid="g6b-key-2")[0], 200)
        self.assertEqual(retired.call("login", fields, kid="g6b-key-2")[0], 200)
        self.assertEqual(retired.call("login", fields, kid="g6b-key-1")[:2], (400, GENERIC))      # key-1 retired
        # the right id with the wrong key material is also refused
        self.assertEqual(both.call("login", fields, kid="g6b-key-1", key_from="g6b-key-2")[:2], (400, GENERIC))


class T06_FailClosed(unittest.TestCase):
    def test_gateway_disabled_answers_unavailable_and_old_routes_keep_working(self):
        g = Gateway("disabled")
        self.assertEqual(g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})[:2],
                         (503, {"error": "AUTH_UNAVAILABLE"}))
        for op in ("login", "signup", "reauth", "change_password"):
            self.assertEqual(g.call(op, raw="{}", bearer="x" if op in ("reauth", "change_password") else None)[:2],
                             (503, {"error": "AUTH_UNAVAILABLE"}), op)
        status, body, _ = g.request("GET", "/validate-invite/NOPE")
        self.assertEqual((status, body["valid"]), (200, False))
        # with the gateway off, /sync-user and /auth/mark-verified behave exactly as before (the token claim is used as is)
        user = make_user()
        session = session_from(Gateway().call("login", {"email": user["email"], "password": user["password"]})[1]["custom_token"])
        status, out, _ = g.bearer_post("/sync-user", session["idToken"], json_body={
            "firebase_id": user["uid"], "display_name": "G6B", "email": user["email"], "invite_code": "EPIC-NOPE", "session_id": "s"})
        self.assertEqual(status, 403)                                            # invite check, not an e-mail error

    def test_missing_private_key_or_hash_secret_or_database(self):
        for backend in ("nokey", "nohash", "nodb"):
            g = Gateway(backend)
            status, body, _ = g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})
            self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}), backend)
            self.assertEqual(g.call("login", raw="{}")[:2], (503, {"error": "AUTH_UNAVAILABLE"}), backend)

    def test_database_failure_is_reported_as_unavailable_not_as_a_credential_error(self):
        g = Gateway("nodb")
        status, body, _ = g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})
        self.assertEqual(status, 503)
        self.assertNotIn("credential", json.dumps(body))


class T07_EmailClaimFallback(unittest.TestCase):
    """D6: /sync-user and /auth/mark-verified with and without the e-mail claim (unsigned emulator tokens only)."""

    @staticmethod
    def forged_token(uid, email=None, project="demo-epicverse-g6b"):
        def b(d):
            return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
        now = int(time.time())
        payload = {"iss": "https://securetoken.google.com/" + project, "aud": project, "sub": uid, "user_id": uid, "auth_time": now,
                   "iat": now, "exp": now + 3600, "firebase": {"identities": {}, "sign_in_provider": "custom"}}
        if email is not None:
            payload["email"] = email
        return b({"alg": "none", "typ": "JWT"}) + "." + b(payload) + "."

    def sync(self, token, uid, email, invite=INVITE):
        return Gateway().bearer_post("/sync-user", token, json_body={"firebase_id": uid, "display_name": "G6B", "email": email,
                                                                     "invite_code": invite, "session_id": "s"})

    def test_token_without_an_email_claim_uses_the_account_email_found_by_the_verified_uid(self):
        user = make_user()
        status, out, _ = self.sync(self.forged_token(user["uid"]), user["uid"], user["email"])
        self.assertEqual(status, 200, out)
        self.assertEqual(LAB.psql("g6b_main", "SELECT lower(email) FROM users WHERE uid='%s'" % user["uid"]), user["email"].lower())

    def test_claim_that_disagrees_with_the_account_is_rejected(self):
        user = make_user()
        status, out, _ = self.sync(self.forged_token(user["uid"], email=unique("liar")), user["uid"], user["email"])
        self.assertEqual(status, 403)
        self.assertEqual(LAB.psql("g6b_main", "SELECT count(*) FROM users WHERE uid='%s'" % user["uid"]), "0")

    def test_unknown_uid_disabled_account_and_account_without_email_are_rejected(self):
        ghost = "g6b-ghost-" + secrets.token_hex(4)
        self.assertEqual(self.sync(self.forged_token(ghost), ghost, "ghost@example.invalid")[0], 403)
        user = make_user()
        auth.update_user(user["uid"], disabled=True, app=ADMIN)
        self.assertEqual(self.sync(self.forged_token(user["uid"]), user["uid"], user["email"])[0], 403)
        no_email = auth.create_user(app=ADMIN, uid="g6b-noemail-" + secrets.token_hex(3))
        self.assertEqual(self.sync(self.forged_token(no_email.uid), no_email.uid, "x@example.invalid")[0], 403)

    def test_a_client_supplied_uid_or_email_never_replaces_the_verified_identity(self):
        a, b = make_user(), make_user()
        # a token for A cannot be used to synchronise B's profile (existing check) ...
        self.assertEqual(self.sync(self.forged_token(a["uid"]), b["uid"], b["email"])[0], 403)
        # ... and the e-mail in the request body is not what ends up stored: the account's own e-mail is
        status, _, _ = self.sync(self.forged_token(a["uid"]), a["uid"], "someone-else@example.invalid")
        stored = LAB.psql("g6b_main", "SELECT lower(email) FROM users WHERE uid='%s'" % a["uid"])
        self.assertTrue(status == 200 and stored == a["email"].lower() or status == 403, (status, stored == a["email"].lower()))

    def test_mark_verified_works_for_a_session_without_the_email_claim(self):
        g = Gateway()
        email, password = unique("mv"), new_password()
        proof = g.proof(email)
        status, body, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        uid = auth.get_user_by_email(email, app=ADMIN).uid
        no_claim = self.forged_token(uid)
        self.assertEqual(self.sync(no_claim, uid, email)[0], 200)
        status, out, _ = g.bearer_post("/auth/mark-verified", no_claim, form={"proof": proof})
        self.assertEqual((status, out), (200, {"status": "ok"}))
        self.assertEqual(LAB.psql("g6b_main", "SELECT email_verified FROM users WHERE uid='%s'" % uid), "t")

    def resolve(self, token, backend="main"):
        status, body, _ = Gateway(backend).bearer_post("/__g6b/resolve_email", token, json_body={})
        return status, body

    def test_the_helper_on_its_own_follows_the_verified_uid_not_the_claim(self):
        user = make_user()
        self.assertEqual(self.resolve(self.forged_token(user["uid"])), (200, {"email": user["email"]}))                # no claim: Admin lookup
        self.assertEqual(self.resolve(self.forged_token(user["uid"], email=user["email"].upper())),
                         (200, {"email": user["email"].upper()}))                                                       # matching claim (case-insensitive)
        self.assertEqual(self.resolve(self.forged_token(user["uid"], email=unique("liar")))[0], 403)                   # disagreeing claim
        ghost = "g6b-ghost-" + secrets.token_hex(4)
        self.assertEqual(self.resolve(self.forged_token(ghost))[0], 403)                                               # unknown uid
        self.assertEqual(self.resolve(self.forged_token(ghost, email="ghost@example.invalid"))[0], 403)
        no_email = auth.create_user(app=ADMIN, uid="g6b-noemail-" + secrets.token_hex(3))
        self.assertEqual(self.resolve(self.forged_token(no_email.uid))[0], 403)                                        # account without e-mail
        auth.update_user(user["uid"], disabled=True, app=ADMIN)
        self.assertEqual(self.resolve(self.forged_token(user["uid"]))[0], 403)                                         # disabled account

    def test_with_the_gateway_disabled_the_helper_is_the_old_behaviour(self):
        user = make_user()
        self.assertEqual(self.resolve(self.forged_token(user["uid"], email="whatever@example.invalid"), "disabled"),
                         (200, {"email": "whatever@example.invalid"}))                                                  # claim used as is
        self.assertEqual(self.resolve(self.forged_token(user["uid"]), "disabled"), (200, {"email": None}))            # no lookup at all

    def test_enabled_gateway_in_a_non_local_environment_fails_closed_for_these_routes(self):
        user = make_user()
        status, out, _ = Gateway("notdemo").bearer_post("/sync-user", self.forged_token(user["uid"], project="g6b-notdemo-project"),
                                                        json_body={"firebase_id": user["uid"], "display_name": "G", "email": user["email"],
                                                                   "invite_code": INVITE, "session_id": "s"})
        self.assertIn(status, (401, 503))


class CapturingProxy:
    """A loopback TCP proxy that records the bytes the client sent: stands in for a TLS-intercepting proxy that sees the plaintext HTTP."""

    def __init__(self, target_port):
        self.target, self.upstream = target_port, []
        self.server = socket.socket()
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(64)
        self.port = self.server.getsockname()[1]
        self.alive = True
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while self.alive:
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            upstream = socket.create_connection(("127.0.0.1", self.target))
            threading.Thread(target=self.pipe, args=(client, upstream, True), daemon=True).start()
            threading.Thread(target=self.pipe, args=(upstream, client, False), daemon=True).start()

    def pipe(self, src, dst, record):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                if record:
                    self.upstream.append(data)
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.close()
                except OSError:
                    pass

    def close(self):
        self.alive = False
        self.server.close()


class T08_CapturedTraffic(unittest.TestCase):
    def test_a_capturing_proxy_never_sees_an_email_address_or_password(self):
        user = make_user()
        new = new_password()
        email2, pw2 = unique("cap"), new_password()
        proxy = CapturingProxy(BACKENDS["main"][1])
        try:
            g = Gateway(via_port=proxy.port)
            proof = g.proof(email2)            # this helper request is test scaffolding and carries only the e-mail: excluded below
            mark = len(proxy.upstream)
            token = g.call("login", {"email": user["email"], "password": user["password"]})[1]["custom_token"]
            session = session_from(token)
            g.call("reauth", {"email": user["email"], "password": user["password"]}, bearer=session["idToken"])
            g.call("change_password", {"email": user["email"], "password": user["password"], "new_password": new}, bearer=session["idToken"])
            g.call("signup", {"email": email2, "password": pw2}, proof=proof)
            time.sleep(0.3)
            captured = b"".join(proxy.upstream[mark:])
        finally:
            proxy.close()
        self.assertGreater(len(captured), 3000)
        # the ID token in the Authorization header legitimately contains the e-mail claim (out of scope): remove those header lines
        text = captured.decode("latin-1")
        text = "\n".join(line for line in text.split("\r\n") if not line.lower().startswith("authorization:"))
        captured = text.encode("latin-1")
        for secret in (user["email"], user["password"], new, email2, pw2):
            raw = secret.encode()
            forms = [raw, raw.hex().encode(), urllib.parse.quote(secret, safe="").encode(), base64.b64encode(raw), base64.urlsafe_b64encode(raw),
                     base64.urlsafe_b64encode(raw).rstrip(b"="), json.dumps(secret).encode()[1:-1], raw.upper(), raw.lower()]
            for form in forms:
                self.assertNotIn(form, captured)
        print("  [capture] %d bytes of client->gateway traffic (login, reauth, change_password, signup) contain no e-mail or password in "
              "clear, hex, base64, base64url or URL-encoded form" % len(captured))


class T09_DartSealerAgainstTheGateway(unittest.TestCase):
    """Envelopes sealed by the G4 DART sealer, through the real gateway container (skipped if the Dart package is not resolved)."""

    @classmethod
    def setUpClass(cls):
        g4 = HARNESS.parent.parent.parent / "g4"
        sys.path.insert(0, str(g4))
        from _common import DART, DART_PKG, run_seal_cli
        if not DART or not (DART_PKG / ".dart_tool" / "package_config.json").exists():
            raise unittest.SkipTest("run `dart pub get --offline` in scripts/enc_auth_poc/g4_dart first")
        cls.run_seal_cli = staticmethod(run_seal_cli)

    def dart_seal(self, g, op, fields):
        from cryptography.hazmat.primitives import serialization
        for value in fields.values():
            SECRETS.add(value)
        spki = base64.b64encode(LAB.public_key("g6b-key-1").public_bytes(serialization.Encoding.DER,
                                                                          serialization.PublicFormat.SubjectPublicKeyInfo)).decode()
        out, err, code = self.run_seal_cli([{"spki": spki, "kid": "g6b-key-1", "op": op, "nonce": g.nonce(op), "ts": int(time.time()),
                                             "fields": fields}])
        self.assertEqual((code, err), (0, ""))
        self.assertIn("envelope", out[0])
        return out[0]["envelope"]

    def test_signup_login_reauth_and_change_password_with_dart_envelopes_including_an_emoji_password(self):
        g = Gateway()
        email = unique("dart")
        emoji_password = "Aa1!😀🔒"            # 8 UTF-16 code units, 6 code points: accepted by the app's policy
        SECRETS.add(emoji_password)
        proof = g.proof(email)
        status, body, _ = g.call("signup", raw=self.dart_seal(g, "signup", {"email": email, "password": emoji_password}), proof=proof)
        self.assertEqual((status, body.get("created")), (200, True), body)
        status, body, _ = g.call("login", raw=self.dart_seal(g, "login", {"email": email, "password": emoji_password}))
        self.assertEqual(status, 200)
        session = session_from(body["custom_token"])
        status, body, _ = g.call("reauth", raw=self.dart_seal(g, "reauth", {"email": email, "password": emoji_password}),
                                 bearer=session["idToken"])
        self.assertEqual(status, 200)
        new = new_password()
        status, body, _ = g.call("change_password", raw=self.dart_seal(g, "change_password", {
            "email": email, "password": emoji_password, "new_password": new}), bearer=session["idToken"])
        self.assertEqual((status, body.get("password_changed")), (200, True))
        self.assertEqual(g.call("login", raw=self.dart_seal(g, "login", {"email": email, "password": new}))[0], 200)
        self.assertEqual(g.call("login", raw=self.dart_seal(g, "login", {"email": email, "password": emoji_password}))[0], 400)

    def test_too_short_in_utf16_units_is_refused_like_the_app_does(self):
        g = Gateway()
        email = unique("dart")
        short = "Aa1!😀"                                    # 6 UTF-16 code units: below the 8 minimum
        proof = g.proof(email)
        status, body, _ = g.call("signup", raw=self.dart_seal(g, "signup", {"email": email, "password": short}), proof=proof)
        self.assertEqual((status, body), (400, {"error": "weak-password"}))
        self.assertFalse(user_exists(email))


if __name__ == "__main__":
    unittest.main()
