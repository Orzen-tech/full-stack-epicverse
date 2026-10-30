"""Offline unit and security tests of the real-Firebase identity adapter (G7). Fake Firebase / IAM only; the network is denied for the whole run.
These tests prove the adapter's LOGIC (request shapes, error mapping, retries, fail-closed configuration, no leaks). They do NOT prove how real
Firebase behaves."""
import asyncio
import base64
import contextlib
import importlib.util
import io
import json
import logging
import os
import subprocess
import sys
import types
import unittest
from unittest import mock

import _tree
from _tree import run

BACKEND = _tree.use_backend_tree()
sys.path.insert(0, str(_tree.G7 / "harness"))

import httpx  # noqa: E402

import fake_firebase as ff  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services import enc_auth_diag as diag  # noqa: E402
from app.services import enc_auth_identity as base  # noqa: E402
from app.services import enc_auth_identity_firebase as real  # noqa: E402

PROJECT = "epicverse-fake-project"
SA = "g7-signer@%s.iam.gserviceaccount.com" % PROJECT
API_KEY = "AIza" + "B" * 35


def good_settings(**overrides):
    values = {"ENC_AUTH_FIREBASE_PROJECT_ID": PROJECT, "FIREBASE_PROJECT_ID": PROJECT, "ENC_AUTH_FIREBASE_WEB_API_KEY": API_KEY,
              "ENC_AUTH_SIGNER_SERVICE_ACCOUNT": SA, "GOOGLE_APPLICATION_CREDENTIALS": ""}
    values.update(overrides)
    stack = contextlib.ExitStack()
    for name, value in values.items():
        stack.enter_context(mock.patch.object(settings, name, value))
    stack.enter_context(mock.patch.dict(os.environ, {}, clear=False))
    os.environ.pop("FIREBASE_AUTH_EMULATOR_HOST", None)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    return stack


def mock_client(handler):
    return lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=httpx.Timeout(8.0), trust_env=False, follow_redirects=False)


def respond(status, body=None, raw=None):
    def handler(request):
        content = raw if raw is not None else json.dumps(body if body is not None else {}).encode()
        return httpx.Response(status, content=content, headers={"content-type": "application/json"})
    return handler


def failing(exc):
    def handler(request):
        raise exc
    return handler


class Config(unittest.TestCase):
    def test_not_ready_by_default_and_ready_with_a_complete_consistent_configuration(self):
        self.assertFalse(real.ready())
        with good_settings():
            self.assertIsNone(real.config_problem())
            self.assertTrue(real.ready())

    def test_every_missing_or_inconsistent_setting_closes_the_backend_with_a_fixed_code(self):
        cases = [
            ({"ENC_AUTH_FIREBASE_PROJECT_ID": ""}, "project_mismatch"), ({"FIREBASE_PROJECT_ID": "another-project"}, "project_mismatch"),
            ({"ENC_AUTH_FIREBASE_PROJECT_ID": "demo-x", "FIREBASE_PROJECT_ID": "demo-x"}, "demo_project"),
            ({"GOOGLE_APPLICATION_CREDENTIALS": "C:/keys/k.json"}, "key_file_configured"),
            ({"ENC_AUTH_FIREBASE_WEB_API_KEY": ""}, "api_key_missing"), ({"ENC_AUTH_FIREBASE_WEB_API_KEY": "AIza" + "x" * 34}, "api_key_missing"),
            ({"ENC_AUTH_FIREBASE_WEB_API_KEY": API_KEY + "\n"}, "api_key_missing"), ({"ENC_AUTH_FIREBASE_WEB_API_KEY": "not-a-key"}, "api_key_missing"),
            ({"ENC_AUTH_SIGNER_SERVICE_ACCOUNT": ""}, "signer_invalid"),
            ({"ENC_AUTH_SIGNER_SERVICE_ACCOUNT": "g7-signer@epicverse-prod.iam.gserviceaccount.com"}, "signer_invalid"),
            ({"ENC_AUTH_SIGNER_SERVICE_ACCOUNT": "G7-Signer@%s.iam.gserviceaccount.com" % PROJECT}, "signer_invalid"),
            ({"ENC_AUTH_SIGNER_SERVICE_ACCOUNT": "ab@%s.iam.gserviceaccount.com" % PROJECT}, "signer_invalid"),
            ({"ENC_AUTH_SIGNER_SERVICE_ACCOUNT": SA + ".evil.example"}, "signer_invalid"),
        ]
        for overrides, expected in cases:
            with good_settings(**overrides):
                self.assertEqual(real.config_problem(), expected, overrides)
                self.assertFalse(real.ready())

    def test_an_emulator_host_or_a_key_file_in_the_environment_closes_the_backend(self):
        with good_settings():
            with mock.patch.dict(os.environ, {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"}):
                self.assertEqual(real.config_problem(), "emulator_host_set")
            with mock.patch.dict(os.environ, {"GOOGLE_APPLICATION_CREDENTIALS": "C:/keys/k.json"}):
                self.assertEqual(real.config_problem(), "key_file_configured")

    def test_unconfigured_operations_fail_closed_before_any_port_is_touched(self):
        world = ff.World(PROJECT)
        real.configure(ff.build_identity(world))
        try:
            for call in (lambda: real.verify_password("a@b.invalid", "pw"), lambda: real.get_account("u"), lambda: real.create_user("a@b.invalid", "pw"),
                         lambda: real.update_password("u", "pw"), lambda: real.revoke_refresh_tokens("u"), lambda: real.mint_custom_token("u"),
                         lambda: real.verify_id_token("t")):
                with self.assertRaises(real.NotConfigured):
                    run(call())
            self.assertEqual(sum(world.calls.values()), 0)
        finally:
            real.configure(None)

    def test_the_not_ready_cause_is_a_known_diagnostic_code(self):
        self.assertEqual(real.not_ready_cause(), "not_configured")
        for code in ("not_configured", "bad_response", "unauthenticated", "permission_denied", "service_disabled", "rate_limited"):
            self.assertIn(code, diag.CAUSES)
        self.assertTrue(issubclass(real.NotConfigured, base.NotLocal))


class Selection(unittest.TestCase):
    """Which implementation is bound, decided at import time by ENC_AUTH_IDENTITY_BACKEND (checked in fresh interpreters)."""

    def probe(self, backend):
        code = ("import sys, json; sys.path.insert(0, %r)\n"
                "import app.services.enc_auth_identity as i\n"
                "print(json.dumps({'verify': i.verify_password.__module__, 'ready': i.ready(), 'get': i.get_account.__module__,"
                " 'resolve': i.resolve_token_email.__module__, 'resolve_uses_rebound': i.resolve_token_email.__globals__['get_account'] is i.get_account}))"
                % BACKEND)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP")}
        if backend is not None:
            env["ENC_AUTH_IDENTITY_BACKEND"] = backend
        res = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr[-300:])
        return json.loads(res.stdout.strip().splitlines()[-1])

    def test_default_is_the_unchanged_emulator_adapter(self):
        got = self.probe(None)
        self.assertEqual((got["verify"], got["get"]), ("app.services.enc_auth_identity", "app.services.enc_auth_identity"))
        self.assertFalse(got["ready"])                                   # no emulator host in this environment: the emulator adapter's own guard
        self.assertEqual(self.probe("emulator")["verify"], "app.services.enc_auth_identity")

    def test_firebase_backend_rebinds_every_operation_and_the_shared_helper_follows(self):
        got = self.probe("firebase")
        self.assertEqual((got["verify"], got["get"]), ("app.services.enc_auth_identity_firebase", "app.services.enc_auth_identity_firebase"))
        self.assertFalse(got["ready"])                                   # nothing configured: closed
        self.assertEqual(got["resolve"], "app.services.enc_auth_identity")
        self.assertTrue(got["resolve_uses_rebound"], "resolve_token_email must use the selected get_account")

    def test_any_other_value_fails_closed_never_back_to_the_emulator(self):
        for value in ("Firebase", "prod", "", " firebase", "emulator ", "true"):
            code = ("import sys, asyncio; sys.path.insert(0, %r)\n"
                    "import app.services.enc_auth_identity as i\n"
                    "ok = not i.ready()\n"
                    "for call in (lambda: i.verify_password('a','b'), lambda: i.create_user('a','b'), lambda: i.mint_custom_token('u'), lambda: i.verify_id_token('t')):\n"
                    "    try: asyncio.run(call()); ok = False\n"
                    "    except i.NotLocal: pass\n"
                    "print('CLOSED' if ok else 'OPEN')" % BACKEND)
            env = {k: v for k, v in os.environ.items() if k in ("PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP")}
            env["ENC_AUTH_IDENTITY_BACKEND"] = value
            # make the EMULATOR adapter look fully usable: if a typo ever fell back to it, ready() would be True and the calls would not raise
            env.update({"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099", "FIREBASE_PROJECT_ID": "demo-fallback-probe"})
            res = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, timeout=120)
            if value == "":
                continue                                                  # an empty value means "unset" for pydantic: the default applies
            self.assertEqual(res.stdout.strip(), "CLOSED", (value, res.stderr[-200:]))


class RestPort(unittest.TestCase):
    def sign_in(self, handler, email="User@Example.invalid", password="Pw-secret-123"):
        return run(real.HttpxIdentityToolkit(API_KEY, mock_client(handler)).sign_in(email, password))

    def test_request_shape(self):
        seen = {}

        def handler(request):
            seen.update(url=str(request.url), method=request.method, body=json.loads(request.content), headers=dict(request.headers))
            return httpx.Response(200, json={"localId": "uid-1", "idToken": "x"})
        self.assertEqual(self.sign_in(handler), ("uid-1", None))
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["url"], real.IDENTITY_TOOLKIT_URL + "?key=" + API_KEY)
        self.assertEqual(seen["body"], {"email": "User@Example.invalid", "password": "Pw-secret-123", "returnSecureToken": True})
        self.assertNotIn("authorization", seen["headers"])

    def test_credential_answers_are_returned_as_codes_and_free_text_is_dropped(self):
        for status, message, expected in ((400, "INVALID_LOGIN_CREDENTIALS", "INVALID_LOGIN_CREDENTIALS"), (400, "INVALID_PASSWORD", "INVALID_PASSWORD"),
                                          (400, "EMAIL_NOT_FOUND", "EMAIL_NOT_FOUND"), (400, "USER_DISABLED", "USER_DISABLED"),
                                          (400, "TOO_MANY_ATTEMPTS_TRY_LATER : Try again later.", "TOO_MANY_ATTEMPTS_TRY_LATER"),
                                          (403, "PERMISSION_DENIED", "PERMISSION_DENIED"), (400, "API_KEY_INVALID", "API_KEY_INVALID"), (400, "", "UNKNOWN")):
            got = self.sign_in(respond(status, {"error": {"code": status, "message": message}}))
            self.assertEqual(got, (None, expected))

    def test_service_trouble_is_a_port_error_with_a_fixed_cause_not_a_credential_answer(self):
        for status, cause in ((502, "http_502"), (503, "http_503"), (504, "http_504"), (500, "http_5xx"), (599, "http_5xx")):
            with self.assertRaises(real.PortError) as raised:
                self.sign_in(respond(status, {"error": {"message": "BACKEND_ERROR"}}))
            self.assertEqual((raised.exception.kind, raised.exception.cause), ("unavailable", cause))

    def test_malformed_oversized_and_redirect_answers_are_rejected(self):
        for handler in (respond(200, raw=b"not json"), respond(200, {"idToken": "x"}), respond(200, {"localId": ""}), respond(200, {"localId": 5}),
                        respond(200, raw=b"[]"), respond(200, raw=b"x" * (real.MAX_RESPONSE_BYTES + 1)), respond(302, {}), respond(301, {}),
                        respond(100 + 100, raw=b"{" * 10)):
            with self.assertRaises(real.PortError) as raised:
                self.sign_in(handler)
            self.assertIn(raised.exception.cause, ("bad_response", "unexpected"))

    def test_transport_failures_become_fixed_causes(self):
        request = httpx.Request("POST", real.IDENTITY_TOOLKIT_URL)
        for exc, cause in ((httpx.ConnectError("secret-detail", request=request), "connect"), (httpx.ReadTimeout("secret-detail", request=request), "read_timeout"),
                           (httpx.ConnectTimeout("secret-detail", request=request), "connect_timeout"), (httpx.RemoteProtocolError("secret-detail", request=request), "reset")):
            with self.assertRaises(real.PortError) as raised:
                self.sign_in(failing(exc))
            self.assertEqual(raised.exception.cause, cause)
            self.assertNotIn("secret-detail", str(raised.exception) + repr(raised.exception))
            self.assertIsNone(raised.exception.__cause__)

    def test_the_port_prints_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.sign_in(respond(400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}))
            self.sign_in(respond(200, {"localId": "uid-1"}))
            with self.assertRaises(real.PortError):
                self.sign_in(respond(503, {}))
        self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_a_malformed_api_key_cannot_build_the_port(self):
        for key in ("", None, "AIza" + "x" * 34, "AIza" + "x" * 36, API_KEY + " ", " " + API_KEY, 5):
            with self.assertRaises(real.NotConfigured):
                real.HttpxIdentityToolkit(key)

    def test_the_default_client_is_pinned_down(self):
        client = real._default_client()
        try:
            self.assertFalse(client.trust_env)
            self.assertFalse(client.follow_redirects)
            self.assertEqual(client.timeout.read, 8.0)
        finally:
            run(client.aclose())

    def test_tls_verification_is_on_in_the_default_client(self):
        import inspect
        source = inspect.getsource(real._default_client)
        self.assertIn("verify=True", source)
        self.assertNotIn("verify=False", source)
        client = real._default_client()
        try:
            context = getattr(getattr(getattr(client._transport, "_pool", None), "_ssl_context", None), "verify_mode", None)
            if context is not None:
                import ssl
                self.assertEqual(context, ssl.CERT_REQUIRED)
        finally:
            run(client.aclose())

    def test_the_api_key_never_reaches_a_log_even_at_debug_level(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler, root = Capture(), logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        try:
            self.sign_in(respond(400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}))
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        self.assertNotIn(API_KEY, "\n".join(records))
        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)


class SignBlobPort(unittest.TestCase):
    class Tokens(real.AccessTokenProvider):
        def __init__(self, value="ya-access-token-for-testing-0123456789", fail=False):
            self.value, self.fail = value, fail

        async def access_token(self):
            if self.fail:
                raise RuntimeError("secret-detail")
            return self.value

    def port(self, handler, **kw):
        with good_settings():
            return real.IamSignBlobPort(SA, self.Tokens(**kw), mock_client(handler))

    def test_request_shape_and_success(self):
        seen = {}

        def handler(request):
            seen.update(url=str(request.url), headers=dict(request.headers), body=json.loads(request.content))
            return httpx.Response(200, json={"keyId": "0123456789abcdef", "signedBlob": base64.b64encode(b"\x05" * 256).decode()})
        data = b"header.payload"
        self.assertEqual(run(self.port(handler).sign_blob(data)), b"\x05" * 256)
        self.assertEqual(seen["url"], "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/%s:signBlob" % SA)
        self.assertEqual(seen["headers"]["authorization"], "Bearer ya-access-token-for-testing-0123456789")
        self.assertEqual(base64.b64decode(seen["body"]["payload"]), data)
        self.assertNotIn("key=", seen["url"])

    def test_google_errors_become_fixed_causes(self):
        def error(status, code, reason=None):
            body = {"error": {"code": status, "status": code, "message": "free text that must never be shown"}}
            if reason:
                body["error"]["details"] = [{"reason": reason}]
            return respond(status, body)
        for handler, cause in ((error(401, "UNAUTHENTICATED"), "unauthenticated"), (error(403, "PERMISSION_DENIED"), "permission_denied"),
                               (error(403, "PERMISSION_DENIED", "SERVICE_DISABLED"), "service_disabled"), (error(429, "RESOURCE_EXHAUSTED"), "rate_limited"),
                               (error(503, "UNAVAILABLE"), "http_503"), (error(500, "INTERNAL"), "http_5xx"), (error(418, "TEAPOT"), "unexpected")):
            with self.assertRaises(real.PortError) as raised:
                run(self.port(handler).sign_blob(b"a.b"))
            self.assertEqual(raised.exception.cause, cause)
            self.assertNotIn("free text", str(raised.exception))

    def test_malformed_signatures_and_redirects_are_rejected(self):
        for body in ({}, {"signedBlob": 5}, {"signedBlob": "not base64!"}, {"signedBlob": base64.b64encode(b"x" * 10).decode()},
                     {"signedBlob": base64.b64encode(b"x" * 2000).decode()}):
            with self.assertRaises(real.PortError) as raised:
                run(self.port(respond(200, body)).sign_blob(b"a.b"))
            self.assertEqual(raised.exception.cause, "bad_response")
        with self.assertRaises(real.PortError):
            run(self.port(respond(302, {})).sign_blob(b"a.b"))

    def test_token_provider_trouble_and_bad_tokens(self):
        for kwargs in ({"fail": True}, {"value": "short"}, {"value": "has space " * 5}, {"value": None}):
            with self.assertRaises(real.PortError) as raised:
                run(self.port(respond(200, {}), **kwargs).sign_blob(b"a.b"))
            self.assertEqual(raised.exception.cause, "unauthenticated")
            self.assertNotIn("secret-detail", str(raised.exception))

    def test_a_signer_outside_the_configured_project_cannot_be_built(self):
        with good_settings():
            for account in ("g7-signer@epicverse-prod.iam.gserviceaccount.com", "", None, SA + "x", "UPPER@%s.iam.gserviceaccount.com" % PROJECT):
                with self.assertRaises(real.NotConfigured):
                    real.IamSignBlobPort(account, self.Tokens(), mock_client(respond(200, {})))

    def test_the_bearer_token_never_appears_in_errors(self):
        with self.assertRaises(real.PortError) as raised:
            run(self.port(respond(403, {"error": {"status": "PERMISSION_DENIED"}})).sign_blob(b"a.b"))
        self.assertNotIn("ya-access-token", str(raised.exception) + repr(raised.exception))


class GoogleAuthProvider(unittest.TestCase):
    class Creds:
        def __init__(self):
            self.valid, self.token, self.refreshed = False, "ya-fake-access-token-0123456789", 0

        def refresh(self, request):
            self.refreshed += 1
            self.valid = True

    def test_asks_only_for_the_cloud_platform_scope_refreshes_once_and_caches(self):
        creds, scopes = self.Creds(), []
        fake_auth = types.SimpleNamespace(default=lambda scopes: scopes_append(scopes) or (creds, "project"))

        def scopes_append(value):
            scopes.append(value)
        provider = real.GoogleAuthTokenProvider(google_auth=fake_auth, request_factory=lambda: object())
        self.assertEqual(run(provider.access_token()), "ya-fake-access-token-0123456789")
        self.assertEqual(run(provider.access_token()), "ya-fake-access-token-0123456789")
        self.assertEqual((scopes, creds.refreshed), ([["https://www.googleapis.com/auth/cloud-platform"]], 1))

    def test_failures_are_a_fixed_error(self):
        def boom(scopes):
            raise RuntimeError("secret-detail: /home/user/.config/gcloud/application_default_credentials.json")
        with self.assertRaises(real.PortError) as raised:
            run(real.GoogleAuthTokenProvider(google_auth=types.SimpleNamespace(default=boom), request_factory=lambda: object()).access_token())
        self.assertEqual(raised.exception.cause, "unauthenticated")
        self.assertNotIn("secret-detail", str(raised.exception))


def fake_sdk():
    """A stand-in for firebase_admin.auth / exceptions with the real class hierarchy relationships."""
    class FirebaseError(Exception):
        pass

    class UnavailableError(FirebaseError):
        pass

    class DeadlineExceededError(FirebaseError):
        pass

    class InvalidArgumentError(FirebaseError):
        pass

    class InvalidIdTokenError(Exception):
        pass

    class ExpiredIdTokenError(InvalidIdTokenError):
        pass

    class RevokedIdTokenError(InvalidIdTokenError):
        pass

    class UserDisabledError(InvalidIdTokenError):
        pass

    class UserNotFoundError(Exception):
        pass

    class EmailAlreadyExistsError(Exception):
        pass

    auth = types.SimpleNamespace(UserNotFoundError=UserNotFoundError, EmailAlreadyExistsError=EmailAlreadyExistsError, InvalidIdTokenError=InvalidIdTokenError,
                                 ExpiredIdTokenError=ExpiredIdTokenError, RevokedIdTokenError=RevokedIdTokenError, UserDisabledError=UserDisabledError,
                                 calls=[], behaviour={})
    exceptions = types.SimpleNamespace(FirebaseError=FirebaseError, UnavailableError=UnavailableError, DeadlineExceededError=DeadlineExceededError,
                                       InvalidArgumentError=InvalidArgumentError)

    def make(name, result=None):
        def function(*args, **kwargs):
            auth.calls.append((name, args, kwargs))
            outcome = auth.behaviour.get(name, result)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        return function
    for name in ("get_user", "create_user", "update_user", "revoke_refresh_tokens", "verify_id_token"):
        setattr(auth, name, make(name))
    return auth, exceptions


class AdminPort(unittest.TestCase):
    def setUp(self):
        self.auth, self.exc = fake_sdk()
        self.app = object()
        self.port = real.FirebaseAdminPort(PROJECT, 8.0, self.auth, self.exc, self.app)

    def test_get_user(self):
        self.auth.behaviour["get_user"] = types.SimpleNamespace(uid="u1", email="a@b.invalid", disabled=False)
        self.assertEqual(run(self.port.get_user("u1")), {"uid": "u1", "email": "a@b.invalid", "disabled": False})
        self.auth.behaviour["get_user"] = types.SimpleNamespace(uid="u1", email=None, disabled=1)
        self.assertEqual(run(self.port.get_user("u1")), {"uid": "u1", "email": "", "disabled": True})
        self.auth.behaviour["get_user"] = self.auth.UserNotFoundError()
        self.assertIsNone(run(self.port.get_user("u1")))
        self.assertTrue(all(call[2].get("app") is self.app for call in self.auth.calls), "the private app must be passed on every call")

    def test_errors_become_fixed_kinds_and_causes(self):
        cases = ((self.exc.UnavailableError("secret-detail"), ("unavailable", "sdk_unavailable")), (self.exc.DeadlineExceededError("x"), ("unavailable", "sdk_timeout")),
                 (self.exc.InvalidArgumentError("x"), ("invalid_argument", "sdk_error")), (ValueError("x"), ("invalid_argument", "sdk_error")),
                 (self.exc.FirebaseError("x"), ("unavailable", "sdk_error")), (RuntimeError("secret-detail"), ("unavailable", "sdk_error")))
        for exc, expected in cases:
            self.auth.behaviour["get_user"] = exc
            with self.assertRaises(real.PortError) as raised:
                run(self.port.get_user("u"))
            self.assertEqual((raised.exception.kind, raised.exception.cause), expected)
            self.assertNotIn("secret-detail", str(raised.exception))

    def test_create_user_always_creates_an_unverified_account(self):
        self.auth.behaviour["create_user"] = types.SimpleNamespace(uid="new-uid")
        self.assertEqual(run(self.port.create_user("a@b.invalid", "Pw-123456")), "new-uid")
        name, args, kwargs = self.auth.calls[-1]
        self.assertEqual((kwargs["email"], kwargs["email_verified"]), ("a@b.invalid", False))
        for exc, kind in ((self.auth.EmailAlreadyExistsError(), "email_exists"), (self.exc.InvalidArgumentError("PASSWORD_DOES_NOT_MEET_REQUIREMENTS"), "invalid_argument")):
            self.auth.behaviour["create_user"] = exc
            with self.assertRaises(real.PortError) as raised:
                run(self.port.create_user("a@b.invalid", "pw"))
            self.assertEqual(raised.exception.kind, kind)

    def test_set_password_and_revoke(self):
        run(self.port.set_password("u1", "New-Pw-123"))
        run(self.port.revoke("u1"))
        self.assertEqual([c[0] for c in self.auth.calls], ["update_user", "revoke_refresh_tokens"])
        self.assertEqual(self.auth.calls[0][2]["password"], "New-Pw-123")
        self.auth.behaviour["update_user"] = self.auth.UserNotFoundError()
        with self.assertRaises(real.PortError) as raised:
            run(self.port.set_password("u1", "x"))
        self.assertEqual(raised.exception.kind, "not_found")

    def test_verify_token_always_checks_revocation_and_treats_bad_sessions_as_none(self):
        self.auth.behaviour["verify_id_token"] = {"uid": "u1"}
        self.assertEqual(run(self.port.verify_token("tok")), {"uid": "u1"})
        self.assertTrue(self.auth.calls[-1][2]["check_revoked"] is True)
        for exc in (self.auth.RevokedIdTokenError(), self.auth.ExpiredIdTokenError(), self.auth.UserDisabledError(), self.auth.InvalidIdTokenError(), ):
            self.auth.behaviour["verify_id_token"] = exc
            self.assertIsNone(run(self.port.verify_token("tok")), type(exc).__name__)
        self.auth.behaviour["verify_id_token"] = self.exc.UnavailableError("x")
        with self.assertRaises(real.PortError) as raised:
            run(self.port.verify_token("tok"))
        self.assertEqual(raised.exception.cause, "sdk_unavailable")


class CustomToken(unittest.TestCase):
    def setUp(self):
        self.world = ff.World(PROJECT)
        self.identity = ff.build_identity(self.world, clock=lambda: 1_800_000_000)

    def test_signing_input_matches_the_g3_builder_that_was_proven_against_real_firebase(self):
        spec = importlib.util.spec_from_file_location("g3_custom_token_ref", _tree.POC / "g3" / "g3_custom_token.py")
        sys.path.insert(0, str(_tree.POC / "g3"))
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(str(_tree.POC / "g3"))
        for uid in ("a", "synthetic-uid-1", "x" * 128, "Uid_with-symbols.123"):
            account = "g7-signer@epicverse-g3-auth-test.iam.gserviceaccount.com"      # the G3 builder only accepts the G3 test project's accounts
            ours = real.custom_token_signing_input(account, uid, 1_800_000_000)
            reference = module.signing_input(module.build_claims(account, uid, 1_800_000_000))
            self.assertEqual(ours, reference)

    def test_minted_token_verifies_and_carries_the_uid_and_no_email_claims(self):
        with good_settings():
            token = run(self.identity.mint_custom_token("uid-42"))
        claims = self.world.verify_custom_token(token)
        self.assertEqual((claims["uid"], claims["iss"], claims["sub"], claims["aud"]), ("uid-42", self.world.service_account, self.world.service_account, real.CUSTOM_TOKEN_AUDIENCE))
        self.assertEqual(claims["exp"] - claims["iat"], 3600)
        self.assertFalse({"email", "email_verified", "claims"} & set(claims))

    def test_bad_uids_are_refused_without_calling_the_signer(self):
        for uid in ("", None, 5, "x" * 129, "has space", "tab\t", "\u00e9", "line\nbreak"):
            with self.assertRaises(base.IdentityUnavailable):
                run(self.identity.mint_custom_token(uid))
        self.assertEqual(self.world.calls["iam.sign_blob"], 0)

    def test_signing_failures_are_never_retried_and_use_fixed_causes(self):
        for kind, cause in (("unavailable", "permission_denied"), ("unavailable", "service_disabled"), ("unavailable", "unauthenticated"),
                            ("unavailable", "http_503"), ("unavailable", "bad_response")):
            self.world.reset_counters()
            self.world.fault("iam.sign_blob", kind, cause, times=5)
            with self.assertRaises(base.IdentityUnavailable) as raised:
                run(self.identity.mint_custom_token("uid-1"))
            self.assertEqual((raised.exception.stage, raised.exception.cause), ("mint", cause))
            self.assertEqual(self.world.calls["iam.sign_blob"], 1, cause)

    def test_a_malformed_signature_from_the_signer_is_refused(self):
        class Short(ff.FakeSigner):
            async def sign_blob(self, data):
                return b"short"
        identity = real.FirebaseIdentity(ff.FakeRest(self.world), ff.FakeAdmin(self.world), Short(self.world), self.world.service_account)
        with self.assertRaises(base.IdentityUnavailable) as raised:
            run(identity.mint_custom_token("uid-1"))
        self.assertEqual(raised.exception.cause, "bad_response")


class Contract(unittest.TestCase):
    """The adapter's operations against the fake world - the same contract the gateway relies on for the emulator adapter."""

    def setUp(self):
        self.world = ff.World(PROJECT)
        self.uid = self.world.add_account("user@example.invalid", "Right-Pw-123")
        self.identity = ff.build_identity(self.world)

    # ---- password check -------------------------------------------------------------------------------------------------------
    def test_verify_password_results(self):
        self.assertEqual(run(self.identity.verify_password("user@example.invalid", "Right-Pw-123")), (self.uid, None))
        self.assertEqual(run(self.identity.verify_password("USER@example.invalid", "Right-Pw-123")), (self.uid, None))
        self.assertEqual(run(self.identity.verify_password("user@example.invalid", "wrong")), (None, "INVALID_LOGIN_CREDENTIALS"))

    def test_unknown_email_and_wrong_password_are_indistinguishable(self):
        wrong = run(self.identity.verify_password("user@example.invalid", "wrong"))
        unknown = run(self.identity.verify_password("nobody@example.invalid", "wrong"))
        self.assertEqual(wrong, unknown)
        self.assertEqual(base.map_rest_error(wrong[1]), base.map_rest_error(unknown[1]))

    def test_disabled_account_and_throttling_are_reported_as_such(self):
        self.world.accounts[self.uid]["disabled"] = True
        self.assertEqual(run(self.identity.verify_password("user@example.invalid", "Right-Pw-123")), (None, "USER_DISABLED"))
        self.world.accounts[self.uid]["disabled"] = False
        self.world.fault("rest.sign_in", throttle=True)
        code = run(self.identity.verify_password("user@example.invalid", "Right-Pw-123"))[1]
        self.assertEqual(base.classify_rest_error(code), "throttled")

    def test_only_transient_trouble_is_retried_and_at_most_twice(self):
        self.world.fault("rest.sign_in", "unavailable", "http_503", times=1)
        self.assertEqual(run(self.identity.verify_password("user@example.invalid", "Right-Pw-123")), (self.uid, None))
        self.assertEqual(self.world.calls["rest.sign_in"], 2)
        self.world.reset_counters()
        self.world.fault("rest.sign_in", "unavailable", "http_503", times=10)
        with self.assertRaises(base.IdentityUnavailable) as raised:
            run(self.identity.verify_password("user@example.invalid", "Right-Pw-123"))
        self.assertEqual((raised.exception.stage, raised.exception.cause), ("verify_password", "http_503"))
        self.assertEqual(self.world.calls["rest.sign_in"], 3)                                  # 1 attempt + 2 retries
        self.world.reset_counters()
        self.world.fault("rest.sign_in", "unavailable", "unexpected", times=10)
        with self.assertRaises(base.IdentityUnavailable):
            run(self.identity.verify_password("user@example.invalid", "Right-Pw-123"))
        self.assertEqual(self.world.calls["rest.sign_in"], 1, "a non-transient cause is not retried")

    def test_credential_answers_are_never_retried(self):
        run(self.identity.verify_password("user@example.invalid", "wrong"))
        self.assertEqual(self.world.calls["rest.sign_in"], 1)

    # ---- account lookup -------------------------------------------------------------------------------------------------------------
    def test_get_account(self):
        account = run(self.identity.get_account(self.uid))
        self.assertEqual((account.uid, account.email, account.disabled), (self.uid, "user@example.invalid", False))
        self.assertIsNone(run(self.identity.get_account("missing")))
        self.world.accounts[self.uid]["disabled"] = True
        self.assertTrue(run(self.identity.get_account(self.uid)).disabled)

    def test_get_account_retries_transient_trouble_only(self):
        self.world.fault("admin.get_user", "unavailable", "sdk_unavailable", times=1)
        self.assertEqual(run(self.identity.get_account(self.uid)).uid, self.uid)
        self.assertEqual(self.world.calls["admin.get_user"], 2)
        self.world.reset_counters()
        self.world.fault("admin.get_user", "unavailable", "sdk_error", times=10)
        with self.assertRaises(base.IdentityUnavailable):
            run(self.identity.get_account(self.uid))
        self.assertEqual(self.world.calls["admin.get_user"], 1)

    # ---- creation / password change / revocation -----------------------------------------------------------------------------------------
    def test_create_user_makes_an_unverified_account(self):
        uid = run(self.identity.create_user("new@example.invalid", "Brand-New-1"))
        self.assertEqual(self.world.accounts[uid]["email_verified"], False)
        self.assertEqual(self.world.accounts[uid]["password"], "Brand-New-1")

    def test_create_user_failures(self):
        with self.assertRaises(base.EmailExists):
            run(self.identity.create_user("USER@example.invalid", "Brand-New-1"))
        with self.assertRaises(base.IdentityRejected):
            run(self.identity.create_user("weak@example.invalid", "short"))
        self.world.reset_counters()
        self.world.fault("admin.create_user", "unavailable", "sdk_unavailable", times=5)
        with self.assertRaises(base.IdentityUnavailable) as raised:
            run(self.identity.create_user("late@example.invalid", "Brand-New-1"))
        self.assertEqual(raised.exception.stage, "create_user")
        self.assertEqual(self.world.calls["admin.create_user"], 1, "account creation is never retried")

    def test_update_password_and_revoke(self):
        run(self.identity.update_password(self.uid, "Changed-Pw-9"))
        self.assertEqual(self.world.accounts[self.uid]["password"], "Changed-Pw-9")
        with self.assertRaises(base.IdentityRejected):
            run(self.identity.update_password(self.uid, "short"))
        self.assertEqual(self.world.accounts[self.uid]["password"], "Changed-Pw-9", "a refused change must not alter the password")
        self.world.reset_counters()
        self.world.fault("admin.set_password", "unavailable", "sdk_timeout", times=5)
        with self.assertRaises(base.IdentityUnavailable) as raised:
            run(self.identity.update_password(self.uid, "Another-Pw-9"))
        self.assertEqual((raised.exception.stage, raised.exception.cause), ("update_password", "sdk_timeout"))
        self.assertEqual(self.world.calls["admin.set_password"], 1, "a password update is never retried")
        self.world.reset_counters()
        self.world.fault("admin.revoke", "unavailable", "sdk_unavailable", times=5)
        with self.assertRaises(base.IdentityUnavailable):
            run(self.identity.revoke_refresh_tokens(self.uid))
        self.assertEqual(self.world.calls["admin.revoke"], 1)

    # ---- ID tokens -----------------------------------------------------------------------------------------------------------------------
    def test_verify_id_token_outcomes(self):
        token = self.world.id_token_for(self.uid)
        self.assertEqual(run(self.identity.verify_id_token(token))["uid"], self.uid)
        self.assertIsNone(run(self.identity.verify_id_token("never-issued")))
        self.assertIsNone(run(self.identity.verify_id_token(self.world.id_token_for(self.uid, ttl=-10))))                  # expired
        self.world.accounts[self.uid]["disabled"] = True
        self.assertIsNone(run(self.identity.verify_id_token(token)))                                                      # disabled
        self.world.accounts[self.uid]["disabled"] = False
        self.assertIsNotNone(run(self.identity.verify_id_token(token)))

    def test_a_revoked_session_stops_working_but_a_newer_one_does_not(self):
        old = self.world.id_token_for(self.uid, issued_at=self.world.clock() - 10)
        run(self.identity.revoke_refresh_tokens(self.uid))
        self.assertIsNone(run(self.identity.verify_id_token(old)))
        fresh = self.world.id_token_for(self.uid)
        self.assertIsNotNone(run(self.identity.verify_id_token(fresh)))

    def test_verify_id_token_retries_transient_trouble(self):
        token = self.world.id_token_for(self.uid)
        self.world.fault("admin.verify_token", "unavailable", "sdk_unavailable", times=1)
        self.assertIsNotNone(run(self.identity.verify_id_token(token)))
        self.assertEqual(self.world.calls["admin.verify_token"], 2)
        self.world.reset_counters()
        self.world.fault("admin.verify_token", "unavailable", "sdk_unavailable", times=10)
        with self.assertRaises(base.IdentityUnavailable) as raised:
            run(self.identity.verify_id_token(token))
        self.assertEqual((raised.exception.stage, raised.exception.cause), ("verify_token", "sdk_unavailable"))
        self.assertEqual(self.world.calls["admin.verify_token"], 3)

    # ---- no leaks ------------------------------------------------------------------------------------------------------------------------------
    def test_nothing_secret_reaches_exceptions_output_or_diagnostics(self):
        secrets_ = ["Right-Pw-123", "Changed-Pw-9", "user@example.invalid", API_KEY, "fake-id-"]
        out, err, texts = io.StringIO(), io.StringIO(), []
        token = self.world.id_token_for(self.uid)
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            for operation in ("rest.sign_in", "admin.get_user", "admin.create_user", "admin.set_password", "admin.revoke", "admin.verify_token", "iam.sign_blob"):
                for kind, cause in (("unavailable", "sdk_error"), ("unavailable", "http_503"), ("invalid_argument", "sdk_error"), ("email_exists", "sdk_error")):
                    self.world.fault(operation, kind, cause, times=1)
                    for call in (lambda: self.identity.verify_password("user@example.invalid", "Right-Pw-123"), lambda: self.identity.get_account(self.uid),
                                 lambda: self.identity.create_user("user@example.invalid", "Right-Pw-123"), lambda: self.identity.update_password(self.uid, "Changed-Pw-9"),
                                 lambda: self.identity.revoke_refresh_tokens(self.uid), lambda: self.identity.mint_custom_token(self.uid),
                                 lambda: self.identity.verify_id_token(token)):
                        try:
                            run(call())
                        except Exception as exc:
                            texts += [str(exc), repr(exc), getattr(exc, "stage", ""), getattr(exc, "cause", "")]
                    self.world.clear_faults()
        texts += [out.getvalue(), err.getvalue(), json.dumps(diag.snapshot())]
        everything = "\n".join(texts)
        for secret in secrets_:
            self.assertNotIn(secret, everything)

    def test_every_cause_the_adapter_can_raise_is_a_known_diagnostic_code(self):
        causes = {"sdk_error", "sdk_unavailable", "sdk_timeout", "http_502", "http_503", "http_504", "http_5xx", "bad_response", "unauthenticated",
                  "permission_denied", "service_disabled", "rate_limited", "unexpected", "connect", "read_timeout", "connect_timeout", "reset", "dns"}
        self.assertTrue(causes <= diag.CAUSES, causes - diag.CAUSES)


class NoNetwork(unittest.TestCase):
    def test_no_socket_or_name_resolution_was_attempted(self):
        self.assertEqual(_tree.ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
