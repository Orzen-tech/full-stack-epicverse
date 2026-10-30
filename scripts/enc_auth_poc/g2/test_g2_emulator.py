"""G2: compatibility tests against the LOCAL Firebase Auth emulator (127.0.0.1:9099, project demo-epicverse-g2).

EVERYTHING HERE IS EMULATOR-ONLY. The emulator is a stand-in written by the Firebase team for local development: it does not
sign tokens, does not validate custom-token signatures, has no throttling, no recent-login enforcement and no project password
policy. A pass here is NOT proof of real Firebase behaviour; G3 (a disposable real project) must confirm it. Findings that
depend on the emulator are recorded as OBSERVED and printed at the end, with the claim names only (never tokens or secrets).

Run through run_g2.py, which starts and stops the emulator. Dummy accounts and random dummy passwords only.
"""
import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
POC = HERE.parent
REPO = POC.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(POC))

import emu_client as EC  # noqa: E402
import envelope as E  # noqa: E402
from firebase_admin import auth  # noqa: E402
from gateway_stub import GENERIC, ERROR_MAP, GatewayStub, admin_app, map_error  # noqa: E402
from nonces import NonceStore  # noqa: E402
from testkeys import generate_test_private_key, kid_for  # noqa: E402

PW_PREFIX = "G2pw-"  # run_g2.py asserts this marker never appears in the captured output
OBSERVED = []
_counter = [0]


def observe(name, value):
    OBSERVED.append("%-58s %s" % (name, value))


def new_email(tag="u"):
    _counter[0] += 1
    return "g2-%s-%d-%s@example.invalid" % (tag, _counter[0], secrets.token_hex(3))


def new_password():
    return PW_PREFIX + secrets.token_urlsafe(12)


def setUpModule():
    EC.require_isolation()
    assert EC.ready(), "emulator is not answering on %s" % EC.HOST
    assert EC.clear_accounts() == 200


def uid_of(session):
    """The UID of a session. The custom-token sign-in response carries no `localId`, so it is read from the ID token."""
    return EC.claims(session["idToken"])["user_id"]


def tearDownModule():
    print("\n=== OBSERVED (emulator behaviour; not proof of real Firebase behaviour) ===")
    for line in OBSERVED:
        print(line)


class EmuTestCase(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        cls.app = admin_app()

    def account(self, tag="u"):
        email, pw = new_email(tag), new_password()
        status, js = EC.sign_up(email, pw)
        self.assertEqual(status, 200, EC.error_code(js))
        return {"email": email, "password": pw, "uid": js["localId"], "idToken": js["idToken"], "refreshToken": js["refreshToken"]}

    def mint(self, uid):
        return auth.create_custom_token(uid, app=self.app).decode("ascii")

    def custom_session(self, uid):
        status, js = EC.sign_in_custom_token(self.mint(uid))
        self.assertEqual(status, 200, EC.error_code(js))
        return js


# ---------------------------------------------------------------------------------------------------------------------------
class T01_Isolation(EmuTestCase):
    def test_guard_accepts_only_the_local_emulator_and_demo_project(self):
        EC.require_isolation()
        self.assertTrue(EC.PROJECT.startswith("demo-"))
        for bad in ({"FIREBASE_AUTH_EMULATOR_HOST": "localhost:9099"}, {"FIREBASE_AUTH_EMULATOR_HOST": "identitytoolkit.googleapis.com"},
                    {}, {"FIREBASE_AUTH_EMULATOR_HOST": EC.HOST, "GOOGLE_APPLICATION_CREDENTIALS": "x"}):
            with self.assertRaises(EC.IsolationError):
                EC.require_isolation(bad)
        with self.assertRaises(EC.IsolationError):
            EC.require_demo_project("a-real-looking-project")

    def test_client_refuses_any_url_outside_the_emulator(self):
        with self.assertRaises(EC.IsolationError):
            EC._call("GET", "https://identitytoolkit.googleapis.com/v1/accounts:lookup")
        with self.assertRaises(EC.IsolationError):
            EC._call("GET", "http://127.0.0.1:9100/")

    def test_no_google_credentials_are_configured(self):
        for name in EC._FORBIDDEN_ENV:
            self.assertFalse(os.environ.get(name))

    def test_emulator_is_up(self):
        self.assertTrue(EC.ready())


# ---------------------------------------------------------------------------------------------------------------------------
class T02_PasswordSignupLogin(EmuTestCase):
    """EMULATOR-ONLY: email/password signup and REST password verification."""

    def test_signup_then_login_returns_a_stable_uid(self):
        a = self.account()
        status, js = EC.sign_in_password(a["email"], a["password"])
        self.assertEqual(status, 200)
        self.assertEqual(js["localId"], a["uid"])
        c = EC.claims(js["idToken"])
        self.assertEqual((c["user_id"], c["sub"], c["email"]), (a["uid"], a["uid"], a["email"]))
        self.assertEqual(c["firebase"]["sign_in_provider"], "password")
        observe("password sign-in claim names", sorted(c))

    def test_rest_verification_errors(self):
        a = self.account()
        s1, j1 = EC.sign_in_password(a["email"], new_password())
        s2, j2 = EC.sign_in_password(new_email("none"), new_password())
        self.assertEqual((s1, s2), (400, 400))
        observe("wrong password / unknown email REST codes", (EC.error_code(j1), EC.error_code(j2)))
        self.assertEqual(map_error(EC.error_code(j1)), "invalid-credential")
        self.assertEqual(map_error(EC.error_code(j2)), "invalid-credential")  # no enumeration through the mapped code

    def test_duplicate_signup_and_weak_password(self):
        a = self.account()
        s, j = EC.sign_up(a["email"], new_password())
        self.assertEqual(EC.error_code(j), "EMAIL_EXISTS")
        self.assertEqual(map_error("EMAIL_EXISTS"), "email-already-in-use")
        s, j = EC.sign_up(new_email(), "123")
        self.assertEqual(EC.error_code(j), "WEAK_PASSWORD")
        self.assertEqual(map_error("WEAK_PASSWORD"), "weak-password")
        observe("password policy beyond min length", "NOT emulated (needs G3)")

    def test_error_mapping_table_covers_the_codes_the_screens_use(self):
        needed = {"email-already-in-use", "invalid-credential", "weak-password", "user-disabled", "too-many-requests"}
        self.assertTrue(needed <= set(ERROR_MAP.values()))
        self.assertEqual(map_error("SOMETHING_NEW"), "unknown")


# ---------------------------------------------------------------------------------------------------------------------------
class T03_CustomToken(EmuTestCase):
    """EMULATOR-ONLY. The emulator does NOT verify custom-token signatures, so nothing here verifies real production signing."""

    def test_custom_token_signs_in_the_same_uid(self):
        a = self.account()
        js = self.custom_session(a["uid"])
        observe("custom-token sign-in response keys", sorted(js))
        self.assertEqual(uid_of(js), a["uid"])
        c = EC.claims(js["idToken"])
        self.assertEqual((c["sub"], c["user_id"]), (a["uid"], a["uid"]))
        self.assertEqual(c["firebase"]["sign_in_provider"], "custom")
        observe("custom-token session claim names", sorted(c))
        observe("custom-token ID token carries email / email_verified", ("email" in c, "email_verified" in c))
        observe("custom-token email equals account email (emulator copies from user record)", c.get("email") == a["email"])
        observe("REAL FIREBASE email claim for custom-token sessions", "UNVERIFIED -> G3")

    def test_the_password_provider_survives_custom_token_sign_in(self):
        a = self.account()
        js = self.custom_session(a["uid"])
        status, lk = EC.lookup(js["idToken"])
        providers = [p.get("providerId") for p in lk["users"][0].get("providerUserInfo", [])]
        observe("providers on the account after custom-token sign-in", providers)
        self.assertIn("password", providers)
        # the original password still works afterwards
        self.assertEqual(EC.sign_in_password(a["email"], a["password"])[0], 200)

    def test_unknown_uid_creates_a_ghost_account(self):
        ghost = "g2-ghost-" + secrets.token_hex(4)
        js = self.custom_session(ghost)
        self.assertEqual(uid_of(js), ghost)
        status, lk = EC.lookup(js["idToken"])
        observe("ghost account created for never-seen uid; has email", bool(lk["users"][0].get("email")))
        observe("=> gateway MUST mint only for a UID it verified", "stub does (see T08)")

    def test_garbage_and_wrong_audience_tokens_are_rejected(self):
        a = self.account()
        for token in ("garbage", "a.b.c", a["idToken"]):  # an ID token is not a custom token
            status, js = EC.sign_in_custom_token(token)
            self.assertEqual(status, 400)
            self.assertEqual(EC.error_code(js), "INVALID_CUSTOM_TOKEN")

    def test_emulator_accepts_unsigned_custom_tokens_so_signing_is_not_tested(self):
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        body = {"aud": "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
                "uid": "g2-unsigned-" + secrets.token_hex(3), "iat": int(time.time()), "exp": int(time.time()) + 3600}
        token = header + "." + base64.urlsafe_b64encode(json.dumps(body).encode()).rstrip(b"=").decode() + "."
        status, _ = EC.sign_in_custom_token(token)
        observe("emulator accepts an UNSIGNED custom token", status == 200)
        self.assertEqual(status, 200)  # documents that signing/signBlob cannot be validated here


# ---------------------------------------------------------------------------------------------------------------------------
class T04_RefreshAndLogout(EmuTestCase):
    """EMULATOR-ONLY. Client-side logout is local in the SDK; only server-side revocation can be exercised here."""

    def test_refresh_keeps_the_uid_for_password_and_custom_sessions(self):
        a = self.account()
        for session in (a, self.custom_session(a["uid"])):
            status, js = EC.refresh(session["refreshToken"])
            self.assertEqual(status, 200)
            self.assertEqual(js["user_id"], a["uid"])
            self.assertEqual(EC.claims(js["id_token"])["sub"], a["uid"])

    def test_refresh_keeps_the_auth_time_of_the_sign_in(self):
        a = self.account()
        js = self.custom_session(a["uid"])
        before = EC.claims(js["idToken"])["auth_time"]
        time.sleep(1.1)
        status, r = EC.refresh(js["refreshToken"])
        after = EC.claims(r["id_token"])["auth_time"]
        observe("auth_time unchanged by a token refresh", before == after)
        self.assertEqual(before, after)  # required so an MFA session bound to auth_time survives refresh

    def test_server_side_revocation_ends_refresh(self):
        a = self.account()
        js = self.custom_session(a["uid"])
        time.sleep(1.1)
        auth.revoke_refresh_tokens(a["uid"], app=self.app)
        status, r = EC.refresh(js["refreshToken"])
        observe("refresh after revoke_refresh_tokens (status, code)", (status, EC.error_code(r)))
        try:
            auth.verify_id_token(js["idToken"], app=self.app, check_revoked=True)
            revoked = False
        except auth.RevokedIdTokenError:
            revoked = True
        observe("old ID token rejected with check_revoked=True", revoked)
        self.assertTrue(status != 200 or revoked)
        # the backend today calls verify_id_token WITHOUT check_revoked (dependencies.py:14): the old ID token still verifies
        still_ok = bool(auth.verify_id_token(js["idToken"], app=self.app)["uid"])
        observe("old ID token still verifies WITHOUT check_revoked (backend today)", still_ok)
        self.assertTrue(still_ok)


# ---------------------------------------------------------------------------------------------------------------------------
class T05_PasswordAndReauth(EmuTestCase):
    """EMULATOR-ONLY. Recent-login enforcement and the project password policy are NOT emulated."""

    def test_rest_password_update(self):
        a = self.account()
        newpw = new_password()
        status, js = EC.update_password(a["idToken"], newpw)
        self.assertEqual(status, 200)
        self.assertEqual(EC.sign_in_password(a["email"], a["password"])[0], 400)
        self.assertEqual(EC.sign_in_password(a["email"], newpw)[0], 200)

    def test_admin_password_update_is_what_the_gateway_would_use(self):
        a = self.account()
        newpw = new_password()
        old_session = self.custom_session(a["uid"])
        time.sleep(1.1)
        auth.update_user(a["uid"], password=newpw, app=self.app)
        auth.revoke_refresh_tokens(a["uid"], app=self.app)
        self.assertEqual(EC.sign_in_password(a["email"], a["password"])[0], 400)
        self.assertEqual(EC.sign_in_password(a["email"], newpw)[0], 200)
        status, r = EC.refresh(old_session["refreshToken"])
        observe("refresh token of an older session after password change + revoke (status)", status)
        observe("=> emulator does NOT end sessions at refresh after revoke; real Firebase must be checked", "UNVERIFIED -> G3")
        with self.assertRaises(auth.RevokedIdTokenError):
            auth.verify_id_token(old_session["idToken"], app=self.app, check_revoked=True)
        with self.assertRaises(Exception):
            auth.update_user(a["uid"], password="123", app=self.app)

    def test_recent_login_is_not_enforced_by_the_emulator(self):
        a = self.account()
        js = self.custom_session(a["uid"])
        status, _ = EC.update_password(js["idToken"], new_password())
        observe("password update from a custom-token session without re-entering the password", status == 200)
        observe("REAL Firebase requires-recent-login behaviour", "UNVERIFIED -> G3")
        self.assertEqual(status, 200)

    def test_fresh_custom_token_sign_in_advances_auth_time(self):
        a = self.account()
        first = EC.claims(self.custom_session(a["uid"])["idToken"])["auth_time"]
        time.sleep(1.2)
        second = EC.claims(self.custom_session(a["uid"])["idToken"])["auth_time"]
        self.assertGreater(second, first)
        observe("reauth by a new custom-token sign-in advances auth_time", True)


# ---------------------------------------------------------------------------------------------------------------------------
class T06_DisabledUsers(EmuTestCase):
    """EMULATOR-ONLY."""

    def test_disabled_user_is_rejected_everywhere_the_emulator_checks(self):
        a = self.account()
        session = self.custom_session(a["uid"])
        auth.update_user(a["uid"], disabled=True, app=self.app)
        s1, j1 = EC.sign_in_password(a["email"], a["password"])
        s2, j2 = EC.sign_in_custom_token(self.mint(a["uid"]))
        s3, j3 = EC.refresh(session["refreshToken"])
        self.assertEqual([EC.error_code(j) for j in (j1, j2, j3)], ["USER_DISABLED"] * 3)
        with self.assertRaises(auth.UserDisabledError):
            auth.verify_id_token(session["idToken"], app=self.app, check_revoked=True)
        # unchanged backend behaviour: without check_revoked an already-issued ID token still verifies until it expires
        self.assertTrue(auth.verify_id_token(session["idToken"], app=self.app)["uid"])
        observe("create_custom_token() itself refuses a disabled user", False)  # it never looks the user up
        self.assertTrue(self.mint(a["uid"]))  # so the gateway must check `disabled` itself (the stub does)
        auth.update_user(a["uid"], disabled=False, app=self.app)


# ---------------------------------------------------------------------------------------------------------------------------
def _load_mfa_challenge():
    """Loads backend/app/services/mfa_challenge.py UNMODIFIED with the two modules it imports replaced by stand-ins."""
    import importlib.util
    names = ("app", "app.core", "app.core.config", "app.services", "app.services.db_pool")
    saved = {n: sys.modules.get(n) for n in names}
    try:
        for n in names:
            sys.modules[n] = types.ModuleType(n)
        sys.modules["app.core.config"].settings = types.SimpleNamespace(MFA_OTP_HASH_SECRET="g2-test-only")
        sys.modules["app.services.db_pool"].get_pool = lambda: None
        spec = importlib.util.spec_from_file_location("g2_mfa_challenge", REPO / "backend" / "app" / "services" / "mfa_challenge.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


class T07_EpicVerseAuthTimeCompat(EmuTestCase):
    """Uses the REAL auth_time/recent-sign-in functions of the repository with emulator-issued claims (no database)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mfa = _load_mfa_challenge()

    def test_deployed_lineage_carries_the_same_functions(self):
        patch = (REPO / "scripts" / "mfa_additive" / "mfa_additive.patch").read_text(encoding="utf-8")
        for needle in ("def auth_time_from_claims(claims: dict)", "timedelta(minutes=-5) <= age <= RECENT_SIGN_IN",
                       "RECENT_SIGN_IN = timedelta(minutes=15)", "WHERE session_hash = $1 AND uid = $2 AND auth_time = $3"):
            self.assertIn(needle, patch)

    def test_both_session_kinds_yield_uid_and_auth_time_the_backend_needs(self):
        a = self.account()
        pw = auth.verify_id_token(EC.sign_in_password(a["email"], a["password"])[1]["idToken"], app=self.app)
        cu = auth.verify_id_token(self.custom_session(a["uid"])["idToken"], app=self.app)
        for claims in (pw, cu):
            self.assertEqual(claims["uid"], a["uid"])  # what get_current_user() hands to the routes
            self.assertIsNotNone(self.mfa.auth_time_from_claims(claims))
            self.assertTrue(self.mfa.signed_in_recently(self.mfa.auth_time_from_claims(claims)))
        self.assertEqual(pw.get("email"), cu.get("email"))  # routes.py reads claims["email"] (emulator behaviour)
        observe("backend claim keys uid/auth_time/email present in both session kinds", True)

    def test_recent_sign_in_window(self):
        old = {"auth_time": int(time.time()) - 16 * 60}
        self.assertFalse(self.mfa.signed_in_recently(self.mfa.auth_time_from_claims(old)))
        self.assertIsNone(self.mfa.auth_time_from_claims({}))
        self.assertIsNone(self.mfa.auth_time_from_claims({"auth_time": "x"}))

    def test_mfa_session_binding_survives_refresh_but_not_a_new_sign_in(self):
        """validate_mfa_session requires uid AND auth_time to be equal (mfa_challenge.py:209-219)."""
        a = self.account()
        first = self.custom_session(a["uid"])
        bound = self.mfa.auth_time_from_claims(EC.claims(first["idToken"]))
        time.sleep(1.1)
        refreshed = self.mfa.auth_time_from_claims(EC.claims(EC.refresh(first["refreshToken"])[1]["id_token"]))
        again = self.mfa.auth_time_from_claims(EC.claims(self.custom_session(a["uid"])["idToken"]))
        self.assertEqual(bound, refreshed)  # MFA session stays valid across ID-token refresh
        self.assertNotEqual(bound, again)  # a re-authentication needs a new MFA challenge (same as SDK reauth today)
        observe("MFA binding: kept on refresh, renewed on re-authentication", True)


# ---------------------------------------------------------------------------------------------------------------------------
class _Recorder(BaseHTTPRequestHandler):
    stub, wire = None, None

    def log_message(self, *args):  # never log request lines
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).wire.append({"path": self.path, "body": body,
                                "headers": {k: v for k, v in self.headers.items() if k.lower() != "authorization"}})
        status, js = type(self).stub.handle(self.path, body, self.headers.get("Authorization"))
        out = json.dumps(js).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


class Clock:
    def __init__(self):
        self.t = int(time.time())

    def __call__(self):
        return self.t


class T08_GatewayRoundTrip(EmuTestCase):
    """G1 envelope -> local gateway stub (127.0.0.1) -> emulator -> custom token -> emulator session. EMULATOR-ONLY."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.priv = generate_test_private_key()
        cls.kid = kid_for(cls.priv.public_key())
        cls.clock = Clock()
        cls.ring = E.KeyRing()
        cls.ring.add(cls.kid, cls.priv)
        cls.nonces = NonceStore(ttl_seconds=120, clock=cls.clock)
        cls.wire = []
        handler = type("H", (_Recorder,), {"stub": GatewayStub(cls.ring, cls.nonces, cls.app), "wire": cls.wire})
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        assert cls.server.server_address[0] == "127.0.0.1"
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    # --- tiny client simulation ----------------------------------------------------------------------------------------
    def post(self, path, data, authorization=None):
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        if authorization:
            req.add_header("Authorization", "Bearer " + authorization)
        try:
            with self.opener.open(req, timeout=20) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def seal(self, op, fields, nonce=None):
        nonce = nonce or json.loads(self.post("/enc/nonce", json.dumps({"op": op}).encode())[1])["nonce"]
        return E.seal_credentials(self.priv.public_key(), self.kid, op, nonce, fields, now=self.clock())

    def call(self, op, fields, authorization=None, raw=None):
        status, body = self.post("/enc/auth/" + op, (raw or self.seal(op, fields)).encode(), authorization)
        return status, json.loads(body)

    def session(self, custom_token):
        status, js = EC.sign_in_custom_token(custom_token)
        self.assertEqual(status, 200)
        return js

    # --- tests ---------------------------------------------------------------------------------------------------------
    def test_login_round_trip_and_wire_hygiene(self):
        a = self.account("gw")
        start = len(self.wire)
        status, js = self.call("login", {"email": a["email"], "password": a["password"]})
        self.assertEqual(status, 200)
        sess = self.session(js["custom_token"])
        self.assertEqual(uid_of(sess), a["uid"])
        self.assertEqual(EC.claims(sess["idToken"])["firebase"]["sign_in_provider"], "custom")
        # what a TLS-intercepting proxy would capture on the client->gateway hop (bodies and headers, minus the bearer token)
        captured = b"".join(w["path"].encode() + w["body"] + json.dumps(w["headers"]).encode() for w in self.wire[start:])
        self.assertTrue(captured)
        for secret in (a["email"], a["password"]):
            forms = [secret.encode(), secret.encode().hex().encode(), urllib.parse.quote(secret, safe="").encode(),
                     base64.b64encode(secret.encode()), base64.urlsafe_b64encode(secret.encode()),
                     base64.urlsafe_b64encode(secret.encode()).rstrip(b"="), json.dumps(secret).encode()[1:-1]]
            for form in forms:
                self.assertNotIn(form, captured)
        self.assertNotIn(b"@", json.dumps(js).encode())  # the gateway response carries no email either
        observe("login: plaintext/hex/base64/urlencoded email+password absent from captured request", True)
        observe("scope note", "this proves the client->gateway hop only; the gateway->emulator hop is plaintext on loopback by design")

    def test_wrong_password_unknown_email_and_disabled_use_firebase_style_codes(self):
        a = self.account("gw")
        _, wrong = self.call("login", {"email": a["email"], "password": new_password()})
        _, unknown = self.call("login", {"email": new_email("none"), "password": new_password()})
        self.assertEqual(wrong, {"error": "invalid-credential"})
        self.assertEqual(unknown, wrong)  # no enumeration
        auth.update_user(a["uid"], disabled=True, app=self.app)
        _, dis = self.call("login", {"email": a["email"], "password": a["password"]})
        self.assertEqual(dis, {"error": "user-disabled"})
        auth.update_user(a["uid"], disabled=False, app=self.app)

    def test_every_envelope_failure_returns_one_identical_answer(self):
        a = self.account("gw")
        fields = {"email": a["email"], "password": a["password"]}
        good = self.seal("login", fields)
        tampered = json.loads(good)
        tampered["ct"] = tampered["ct"][:10] + ("A" if tampered["ct"][10] != "A" else "B") + tampered["ct"][11:]
        stale_nonce = json.loads(self.post("/enc/nonce", json.dumps({"op": "login"}).encode())[1])["nonce"]
        stale = self.seal("login", fields, nonce=stale_nonce)
        self.clock.t += 130  # nonce expires (and the timestamp is still inside the skew window)
        self.assertEqual(self.call("login", fields, raw=stale), (400, GENERIC))
        self.clock.t -= 130
        results = {
            "tampered": self.call("login", fields, raw=json.dumps(tampered)),
            "garbage": self.call("login", fields, raw="not json"),
            "wrong op": self.call("signup", fields, raw=self.seal("login", fields)),
            "never issued nonce": self.call("login", fields, raw=self.seal("login", fields, nonce=E.b64e(secrets.token_bytes(32)))),
        }
        first = self.call("login", fields, raw=good)
        self.assertEqual(first[0], 200)
        results["replay"] = self.call("login", fields, raw=good)
        for name, res in results.items():
            self.assertEqual(res, (400, GENERIC), name)
        observe("envelope failures (tamper/garbage/wrong-op/unknown-nonce/expired/replay) answer", "identical 400 AUTH_FAILED")

    def test_signup_new_and_signup_recovery(self):
        email, pw = new_email("gw"), new_password()
        status, js = self.call("signup", {"email": email, "password": pw})
        self.assertEqual((status, js["created"]), (200, True))
        uid = uid_of(self.session(js["custom_token"]))
        status, js = self.call("signup", {"email": email, "password": pw})  # app was closed mid-signup: same email, same password
        self.assertEqual((status, js.get("created"), js.get("recovered")), (200, False, True))
        self.assertEqual(uid_of(self.session(js["custom_token"])), uid)
        status, js = self.call("signup", {"email": email, "password": new_password()})  # someone else's email
        self.assertEqual((status, js), (400, {"error": "email-already-in-use"}))
        status, js = self.call("signup", {"email": new_email("gw"), "password": "123"})
        self.assertEqual((status, js), (400, {"error": "weak-password"}))

    def test_reauth_requires_the_signed_in_account_and_advances_auth_time(self):
        a, other = self.account("gw"), self.account("gw")
        sess = self.session(self.call("login", {"email": a["email"], "password": a["password"]})[1]["custom_token"])
        before = EC.claims(sess["idToken"])["auth_time"]
        time.sleep(1.2)
        status, js = self.call("reauth", {"email": a["email"], "password": a["password"]}, authorization=sess["idToken"])
        self.assertEqual(status, 200)
        again = self.session(js["custom_token"])
        self.assertEqual(uid_of(again), a["uid"])
        self.assertGreater(EC.claims(again["idToken"])["auth_time"], before)
        # another account's valid credentials, wrong password, and no bearer token are all refused
        self.assertEqual(self.call("reauth", {"email": other["email"], "password": other["password"]}, authorization=sess["idToken"]),
                         (400, {"error": "invalid-credential"}))
        self.assertEqual(self.call("reauth", {"email": a["email"], "password": new_password()}, authorization=sess["idToken"]),
                         (400, {"error": "invalid-credential"}))
        self.assertEqual(self.call("reauth", {"email": a["email"], "password": a["password"]}), (401, GENERIC))

    def test_change_password_ends_other_sessions_and_keeps_the_caller_signed_in(self):
        a = self.account("gw")
        s1 = self.session(self.call("login", {"email": a["email"], "password": a["password"]})[1]["custom_token"])
        s_other_device = self.session(self.call("login", {"email": a["email"], "password": a["password"]})[1]["custom_token"])
        newpw = new_password()
        time.sleep(1.2)
        status, js = self.call("change_password", {"email": a["email"], "password": a["password"], "new_password": newpw},
                               authorization=s1["idToken"])
        self.assertEqual(status, 200)
        kept = self.session(js["custom_token"])
        self.assertEqual(uid_of(kept), a["uid"])
        self.assertEqual(EC.refresh(kept["refreshToken"])[0], 200)  # the new session refreshes normally
        status, r = EC.refresh(s_other_device["refreshToken"])
        observe("other device's refresh token after gateway password change (status)", status)
        with self.assertRaises(auth.RevokedIdTokenError):  # what the emulator does enforce: the old ID token fails check_revoked
            auth.verify_id_token(s_other_device["idToken"], app=self.app, check_revoked=True)
        self.assertTrue(auth.verify_id_token(kept["idToken"], app=self.app, check_revoked=True)["uid"])
        self.assertEqual(self.call("login", {"email": a["email"], "password": a["password"]}), (400, {"error": "invalid-credential"}))
        self.assertEqual(self.call("login", {"email": a["email"], "password": newpw})[0], 200)
        # wrong current password leaves the password unchanged
        bad = self.call("change_password", {"email": a["email"], "password": new_password(), "new_password": new_password()},
                        authorization=kept["idToken"])
        self.assertEqual(bad, (400, {"error": "invalid-credential"}))
        self.assertEqual(self.call("login", {"email": a["email"], "password": newpw})[0], 200)
        # weak new password is refused and nothing changes
        weak = self.call("change_password", {"email": a["email"], "password": newpw, "new_password": "123"}, authorization=kept["idToken"])
        self.assertEqual(weak, (400, {"error": "weak-password"}))
        self.assertEqual(self.call("login", {"email": a["email"], "password": newpw})[0], 200)

    def test_stub_never_mints_for_an_unverified_uid(self):
        before = len(list(auth.list_users(app=self.app).iterate_all()))
        for fields in ({"email": new_email("none"), "password": new_password()},):
            self.call("login", fields)
        self.assertEqual(len(list(auth.list_users(app=self.app).iterate_all())), before)  # no ghost account appeared

    def test_unknown_routes_and_methods_give_no_information(self):
        self.assertEqual(self.post("/enc/auth/delete_account", b"{}")[0], 404)
        self.assertEqual(self.post("/other", b"{}")[0], 404)
        self.assertEqual(self.post("/enc/nonce", b"{}")[0], 400)
        self.assertEqual(self.post("/enc/nonce", json.dumps({"op": "nope"}).encode())[0], 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
