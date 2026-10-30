"""FINAL OFFLINE WIRING CHECK of Stage A: the real runner (`g8_live_run.main`) -> real preflight, hooks, G7 adapter, firebase_admin 6.4.0, G3 client and
verifier, G8 protection and stage script - with FAKE HTTP at the three lowest layers (urllib3 `_make_request`, httpx transport, http.client connection)
and the network-denial layer active. The approval constants are patched True ONLY inside these tests and restored afterwards. Nothing leaves the
process; the fixtures sign ID tokens with a throw-away test key and serve a throw-away certificate."""
import asyncio
import base64
import contextlib
import datetime
import io
import json
import os
import time
import unittest
from unittest import mock

import _g8 as t
from _tree import ATTEMPTS

import httpx
import urllib3
import urllib3.connectionpool as cp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

import g8_guard as guard
import g8_live_run as runner
import g8_wiring as wiring
from app.core.config import settings

DUMMY_UID, DUMMY_EMAIL, DUMMY_PASSWORD = "dummyuid00000001", "dummy.person@example.net", "Dummy-Passw0rd!"
KID = "testkid1"
SIGNER_TOKEN, ADMIN_TOKEN = "ya29." + "s" * 40, "ya29." + "a" * 40
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert_pem():
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "offline-fixture")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(KEY.public_key()).serial_number(7)
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30)).sign(KEY, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


CERT_PEM = _cert_pem()


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def id_token(uid, provider="custom", project=t.PROJECT, signing_key=KEY):
    now = int(time.time()) - 5
    claims = {"iss": "https://securetoken.google.com/" + project, "aud": project, "sub": uid, "user_id": uid, "iat": now, "exp": now + 3600, "auth_time": now,
              "firebase": {"sign_in_provider": provider, "identities": {}}}
    signing = b64(json.dumps({"alg": "RS256", "kid": KID, "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
    return signing + "." + b64(signing_key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256()))


class Fixture:
    """Serves every request of Stage A from memory and records the order. `fail` maps a step name to a replacement behaviour."""

    def __init__(self, **overrides):
        self.log, self.overrides, self.methods = [], overrides, []

    def step(self, name, default):
        override = self.overrides.get(name)
        return override() if override else default

    # ---- layer 1: urllib3 wire (firebase_admin, google-auth) --------------------------------------------------------------------
    def wire(self, pool, conn, method, url, *args, **kwargs):
        path = url.split("?")[0]
        self.log.append((pool.host, path))
        self.methods.append(method)
        if path.endswith("accounts:lookup"):
            status, document = self.step("lookup", (200, {"users": [{"localId": DUMMY_UID, "email": DUMMY_EMAIL, "disabled": False, "validSince": "0"}]}))
        elif path.endswith("x509/securetoken@system.gserviceaccount.com"):
            status, document = self.step("sdk_certs", (200, {KID: CERT_PEM}))
        else:
            raise AssertionError("unexpected SDK request")
        body = document if isinstance(document, bytes) else json.dumps(document).encode()
        return urllib3.response.HTTPResponse(body=io.BytesIO(body), headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
                                             status=status, preload_content=False, request_method=method)

    # ---- layer 2: httpx (G7 REST sign-in, signBlob) --------------------------------------------------------------------------------
    def httpx_inner(self):
        fixture = self

        class Inner(httpx.AsyncBaseTransport):
            def __init__(self, *a, **k):
                pass

            async def handle_async_request(self, request):
                path = request.url.path
                fixture.log.append((request.url.host, path))
                fixture.methods.append(request.method)
                if path == "/v1/accounts:signInWithPassword":
                    password = json.loads(request.content)["password"]
                    right = password == DUMMY_PASSWORD
                    status, document = fixture.step("sign_in_right" if right else "sign_in_wrong",
                                                    (200, {"localId": DUMMY_UID}) if right else (400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}))
                elif path.endswith(":signBlob"):
                    status, document = fixture.step("sign_blob", (200, {"signedBlob": base64.b64encode(os.urandom(256)).decode()}))
                    if isinstance(status, int) and status == 302:
                        return httpx.Response(302, headers={"Location": "https://example.org/"}, request=request)
                else:
                    raise AssertionError("unexpected httpx request")
                if callable(status):
                    status()
                return httpx.Response(status, json=document, request=request)

            async def aclose(self):
                pass
        return Inner

    # ---- layer 3: http.client (G3 client: certificates, custom-token exchange) ---------------------------------------------------------
    def connection(self):
        fixture = self

        class Connection:
            def __init__(self, host, port, timeout=None, context=None):
                self.host, self.sock = host, mock.Mock()

            def connect(self):
                pass

            def request(self, method, target, body=None, headers=None):
                self.method, self.target, self.body = method, target, body

            def getresponse(self):
                path = self.target.split("?")[0]
                fixture.log.append((self.host, path))
                fixture.methods.append(self.method)
                if path == "/v1/accounts:signInWithCustomToken":
                    token = json.loads(self.body)["token"]
                    pad = lambda s: s + "=" * (-len(s) % 4)
                    uid = json.loads(base64.urlsafe_b64decode(pad(token.split(".")[1])))["uid"]
                    status, document = fixture.step("exchange", (200, {"idToken": id_token(uid), "refreshToken": "AMf-" + "r" * 40, "expiresIn": "3600", "isNewUser": False}))
                elif path.endswith("x509/securetoken@system.gserviceaccount.com"):
                    status, document = fixture.step("g3_certs", (200, {KID: CERT_PEM}))
                else:
                    raise AssertionError("unexpected G3 request")
                payload = document if isinstance(document, bytes) else json.dumps(document).encode()
                return mock.Mock(status=status, read=mock.Mock(side_effect=[payload, b""]))

            def close(self):
                pass
        return Connection


class Harness:
    """Runs `main` for stage-a (or `mode`) with all fakes in place. Approvals are patched True for the duration only, for the stages in `approve`."""
    mode, approve = "stage-a", ("A",)

    def __init__(self, fixture=None, hidden=None, visible=None, env=None):
        self.fixture = fixture or Fixture()
        self.hidden = list(hidden if hidden is not None else ["AIza" + "B" * 35, SIGNER_TOKEN, ADMIN_TOKEN, DUMMY_UID, DUMMY_EMAIL, DUMMY_PASSWORD])
        self.visible = list(visible if visible is not None else [str(t._tree.tree_dir()), guard.ALLOWED_PROJECT_ID, runner.PHRASES["A"], t.SIGNER])
        self.out, self.hidden_asked, self.visible_asked = [], [], []
        self.env = env

    def io(self):
        def hidden(prompt):
            self.hidden_asked.append(prompt)
            return self.hidden.pop(0)

        def visible(prompt):
            self.visible_asked.append(prompt)
            return self.visible.pop(0)
        return runner.IO(hidden=hidden, visible=visible, is_tty=lambda: True, out=self.out.append)

    def run(self):
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(guard, "LIVE_EXECUTION_APPROVED", True))
            stack.enter_context(mock.patch.dict(guard.STAGE_APPROVED, {k: (k in self.approve) for k in "ABC"}))
            for name in ("ENC_AUTH_RETRY_MAX", "ENC_AUTH_FIREBASE_PROJECT_ID", "FIREBASE_PROJECT_ID", "ENC_AUTH_SIGNER_SERVICE_ACCOUNT",
                         "ENC_AUTH_FIREBASE_WEB_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
                stack.enter_context(mock.patch.object(settings, name, getattr(settings, name)))
            stack.enter_context(mock.patch.object(cp.HTTPConnectionPool, "_make_request", lambda pool, conn, method, url, *a, **k: self.fixture.wire(pool, conn, method, url, *a, **k)))
            stack.enter_context(mock.patch.object(httpx, "AsyncHTTPTransport", self.fixture.httpx_inner()))
            stack.enter_context(mock.patch("http.client.HTTPSConnection", self.fixture.connection()))
            stack.enter_context(mock.patch.dict(os.environ, {k: v for k, v in (self.env or {}).items()}))
            self.created_apps = []
            real_build = wiring.build_admin_app
            stack.enter_context(mock.patch.object(wiring, "build_admin_app", lambda name, tok: self.created_apps.append(real_build(name, tok)) or self.created_apps[-1]))
            try:
                self.code = runner.main(["g8_live_run.py", self.mode], self.io())
                self.error = None
            except BaseException as exc:                      # cancellation / interrupts propagate out of main by design
                self.code, self.error = None, exc
        return self


A_ORDER = [("identitytoolkit.googleapis.com", "/v1/accounts:signInWithPassword")] * 2 + [
    ("identitytoolkit.googleapis.com", "/v1/projects/%s/accounts:lookup" % t.PROJECT),
    ("iamcredentials.googleapis.com", "/v1/projects/-/serviceAccounts/%s:signBlob" % t.SIGNER),
    ("identitytoolkit.googleapis.com", "/v1/accounts:signInWithCustomToken"),
    ("www.googleapis.com", guard.CERTS_PATH),
    ("identitytoolkit.googleapis.com", "/v1/projects/%s/accounts:lookup" % t.PROJECT),
    ("www.googleapis.com", guard.CERTS_PATH)]


class HappyPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = Harness().run()

    def test_the_run_is_clean_and_exits_zero(self):
        self.assertIsNone(self.h.error)
        self.assertEqual(self.h.code, 0, self.h.out)
        self.assertTrue(self.h.out[0].startswith("stage=A clean=True stop=-"))

    def test_all_seven_steps_pass_in_order(self):
        self.assertEqual([line.split()[:2] for line in self.h.out[0].splitlines()[1:8]], [["A0%d" % n, "pass"] for n in range(1, 8)])

    def test_requests_happen_in_the_designed_order_across_all_three_layers(self):
        self.assertEqual(self.h.fixture.log, A_ORDER)

    def test_accounting_includes_the_sdk_generated_calls(self):
        self.assertIn("requests total=8 certs=2 custom_exchange=1 lookup=2 sign_blob=1 sign_in=2", self.h.out[0])
        sdk = [e for e in self.h.fixture.log if e[1].endswith("accounts:lookup") or e[1] == guard.CERTS_PATH]
        self.assertEqual(len(sdk), 4)                                        # 2 lookups + 1 SDK cert fetch + 1 G3 cert fetch, all counted

    def test_nothing_that_could_modify_an_account_was_requested(self):
        paths = [p for _, p in self.fixture_log()]
        for word in ("accounts:update", "accounts:delete", "signUp", ":batch", "sendOobCode"):
            self.assertFalse(any(word in p for p in paths), word)
        self.assertFalse(any(p.endswith("/accounts") for p in paths))        # no create
        self.assertEqual(sorted(set(self.h.fixture.methods)), ["GET", "POST"])

    def fixture_log(self):
        return self.h.fixture.log

    def test_no_secret_or_identifier_is_printed(self):
        text = "\n".join(self.h.out)
        for needle in (DUMMY_UID, DUMMY_EMAIL, DUMMY_PASSWORD, SIGNER_TOKEN, ADMIN_TOKEN, "AIza", "eyJ", t.SIGNER, "@"):
            self.assertNotIn(needle, text)

    def test_secrets_were_asked_for_hidden_and_the_signer_visible(self):
        self.assertEqual(len(self.h.hidden_asked), 6)
        self.assertTrue(all("(hidden)" in p for p in self.h.hidden_asked))
        self.assertFalse(any("password" in p.lower() or "token" in p.lower() or "key" in p.lower() for p in self.h.visible_asked))

    def test_cleanup_of_process_state_and_nothing_left_hooked(self):
        self.assertIsNone(guard._installed["urllib3"])
        self.assertIsNone(guard._installed["socket"])
        import firebase_admin
        for app in self.h.created_apps:
            with self.assertRaises(ValueError):
                firebase_admin.get_app(app.name)
        self.assertEqual(ATTEMPTS, [])


class FailClosed(unittest.TestCase):
    def tearDown(self):
        self.assertIsNone(guard._installed["urllib3"])
        self.assertIsNone(guard._installed["socket"])

    def test_cancellation_mid_run_prints_the_partial_report_cleans_up_and_propagates(self):
        def cancel():
            raise asyncio.CancelledError()
        h = Harness(Fixture(sign_blob=lambda: (cancel(), None)[1])).run()
        self.assertIsInstance(h.error, asyncio.CancelledError)
        self.assertTrue(any("stop=cancelled" in line for line in h.out))
        self.assertEqual(sum(1 for e in h.fixture.log if e[0] == "identitytoolkit.googleapis.com" and e[1].endswith("accounts:signInWithCustomToken")), 0)

    def test_keyboard_interrupt_mid_run(self):
        def interrupt():
            raise KeyboardInterrupt()
        h = Harness(Fixture(sign_blob=lambda: (interrupt(), None)[1])).run()
        self.assertIsInstance(h.error, KeyboardInterrupt)

    def test_missing_credentials_stop_with_a_fixed_code_and_no_request(self):
        for index, label in ((0, "api key"), (1, "signer token"), (2, "admin token")):
            hidden = ["AIza" + "B" * 35, SIGNER_TOKEN, ADMIN_TOKEN, DUMMY_UID, DUMMY_EMAIL, DUMMY_PASSWORD]
            hidden[index] = ""
            h = Harness(hidden=hidden).run()
            self.assertEqual(h.code, 2, label)
            self.assertTrue(h.out[-1].startswith("G8 stopped: "), (label, h.out))
            self.assertEqual(h.fixture.log, [], label)

    def test_missing_dummy_identity_stops_before_any_request(self):
        for index in (3, 4):
            hidden = ["AIza" + "B" * 35, SIGNER_TOKEN, ADMIN_TOKEN, DUMMY_UID, DUMMY_EMAIL, DUMMY_PASSWORD]
            hidden[index] = ""
            h = Harness(hidden=hidden).run()
            self.assertEqual(h.code, 2)
            self.assertEqual(h.fixture.log, [])

    def test_a_missing_signer_or_wrong_typed_confirmation_asks_for_no_secret(self):
        for visible, tail in (([None, guard.ALLOWED_PROJECT_ID, runner.PHRASES["A"], ""], "signer"), ([None, "wrong", "x", "x"], "project")):
            visible[0] = str(t._tree.tree_dir())
            h = Harness(visible=visible).run()
            self.assertEqual(h.code, 2, tail)
            self.assertEqual(h.hidden_asked, [], tail)

    def test_environment_with_a_proxy_is_refused_before_any_prompt(self):
        h = Harness(env={"HTTPS_PROXY": "http://proxy.invalid:3128"}).run()
        self.assertEqual(h.out, ["G8 stopped: proxy_environment"])
        self.assertEqual((h.hidden_asked, h.fixture.log), ([], []))

    def test_wrong_password_acceptance_is_a_failed_check_not_a_pass(self):
        h = Harness(Fixture(sign_in_wrong=lambda: (200, {"localId": DUMMY_UID}))).run()
        self.assertEqual(h.code, 1)
        self.assertIn("A02 fail", h.out[0])

    def test_unexpected_sign_in_answer_is_ambiguous_and_stops_the_rest(self):
        h = Harness(Fixture(sign_in_right=lambda: (200, {"unexpected": True}))).run()
        self.assertEqual(h.code, 1)
        self.assertIn("A01 ambiguous", h.out[0])
        self.assertEqual(len(h.fixture.log), 1)

    def test_sign_in_returning_another_uid_fails(self):
        h = Harness(Fixture(sign_in_right=lambda: (200, {"localId": "someoneelse123"}))).run()
        self.assertIn("A01 fail", h.out[0])
        self.assertEqual(len(h.fixture.log), 1)

    def test_an_unavailable_lookup_never_passes(self):
        h = Harness(Fixture(lookup=lambda: (503, {"error": {"message": "x"}}))).run()
        self.assertEqual(h.code, 1)
        self.assertNotIn("A03 pass", h.out[0])
        self.assertIn("stop=kind_quota_exhausted", h.out[0])             # the SDK's own 503 retries are charged and stop at the lookup quota
        self.assertEqual(sum(1 for _, p in h.fixture.log if p.endswith("accounts:lookup")), 2)
        self.assertLessEqual(len(h.fixture.log), 8)

    def test_lookup_returning_html_or_another_account_does_not_pass(self):
        for answer in ((200, b"<html>not json</html>"), (200, {"users": []})):
            h = Harness(Fixture(lookup=lambda a=answer: a)).run()
            self.assertEqual(h.code, 1)
            self.assertNotIn("A03 pass", h.out[0])

    def test_a_redirect_from_iam_stops_the_run(self):
        h = Harness(Fixture(sign_blob=lambda: (302, {}))).run()
        self.assertEqual(h.code, 1)
        self.assertIn("stop=redirect_refused", h.out[0])

    def test_garbage_exchange_answer_fails_and_nothing_further_runs(self):
        h = Harness(Fixture(exchange=lambda: (200, {"idToken": "not-a-jwt", "refreshToken": "x", "expiresIn": "3600"}))).run()
        self.assertEqual(h.code, 1)
        self.assertTrue("A05 fail" in h.out[0] or "stop=custom_exchange_created_or_unknown_account" in h.out[0])       # an unknown isNewUser flag is a stop
        self.assertEqual(sum(1 for _, p in h.fixture.log if p == guard.CERTS_PATH), 0)

    def test_an_account_created_by_the_exchange_is_a_critical_stop(self):
        token = lambda: (200, {"idToken": id_token(DUMMY_UID), "refreshToken": "AMf-" + "r" * 40, "expiresIn": "3600", "isNewUser": True})
        h = Harness(Fixture(exchange=token)).run()
        self.assertEqual(h.code, 1)
        self.assertIn("stop=custom_exchange_created_or_unknown_account", h.out[0])

    def test_an_id_token_signed_with_another_key_fails_signature_verification(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        forged = lambda: (200, {"idToken": id_token(DUMMY_UID, signing_key=other), "refreshToken": "AMf-" + "r" * 40, "expiresIn": "3600", "isNewUser": False})
        h = Harness(Fixture(exchange=forged)).run()
        self.assertEqual(h.code, 1)
        self.assertNotIn("A07 pass", h.out[0])
        self.assertNotIn("A06 pass", h.out[0])

    def test_a_token_for_another_uid_or_project_fails_the_uid_checks(self):
        for token in (id_token("someoneelse123"), id_token(DUMMY_UID, project="another-project")):
            doc = {"idToken": token, "refreshToken": "AMf-" + "r" * 40, "expiresIn": "3600", "isNewUser": False}
            h = Harness(Fixture(exchange=lambda d=doc: (200, d))).run()
            self.assertEqual(h.code, 1)
            self.assertNotIn("A07 pass", h.out[0])

    def test_unavailable_certificates_stop_the_run(self):
        h = Harness(Fixture(g3_certs=lambda: (503, {"error": {"message": "x"}}))).run()
        self.assertEqual(h.code, 2 if h.out[-1].startswith("G8 stopped") else 1)
        self.assertNotIn("A07 pass", "".join(h.out))


class EverythingIsLockedDown(unittest.TestCase):
    def test_every_mode_refuses_before_any_prompt(self):
        self.assertEqual((guard.LIVE_EXECUTION_APPROVED, guard.STAGE_APPROVED), (False, {"A": False, "B": False, "C": False}))
        for mode in ("stage-a", "stage-b", "cleanup"):
            out = []
            code = runner.main(["g8_live_run.py", mode], runner.IO(hidden=lambda p: 1 / 0, visible=lambda p: "x", is_tty=lambda: True, out=out.append))
            self.assertEqual((code, out), (2, ["G8 stopped: live_execution_not_approved"]))
        self.assertEqual(ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
