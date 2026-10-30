"""REST client tests (offline). The transport is a FAKE; the network-denial layer is active for the whole run. Covers hosts/redirects/proxy
settings, safe classification, no retries, size limits, redaction of credentials and tokens, and runtime-only credential collection."""
import contextlib
import io
import json
import logging
import pickle
import unittest

import _support as S
import g3_guard as guard
import g3_rest_client as rc
from g3_guard import GuardError, Secret

KEY = S.synthetic_api_key()
SIGN_IN_URL, REFRESH_URL = rc.SIGN_IN_URL, rc.REFRESH_URL


def config():
    return guard.load_config(S.ALLOWED, KEY, env={})


class FakeTransport:
    """Records every request; answers from a queue of (status, body, url) or raises."""

    def __init__(self, *answers):
        self.answers, self.requests = list(answers), []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else (200, b"{}", request.url)
        if isinstance(answer, Exception):
            raise answer
        status, body, url = answer
        return rc.TransportResponse(status, body if isinstance(body, bytes) else json.dumps(body).encode(), url or request.url)


def error(code, status=400):
    return (status, {"error": {"code": status, "message": code}}, None)


def sign_in_ok(id_token="id-" + "a" * 40, refresh="rt-" + "b" * 40):
    return (200, {"idToken": id_token, "refreshToken": refresh, "expiresIn": "3600", "localId": "uid-1", "email": "ignored@example.invalid"}, None)


def client(*answers):
    transport = FakeTransport(*answers)
    return rc.FirebaseRestClient(config(), transport), transport


class Construction(unittest.TestCase):
    def test_an_unguarded_config_is_refused(self):
        with self.assertRaises(GuardError):
            rc.FirebaseRestClient({"project_id": S.ALLOWED}, FakeTransport())
        with self.assertRaises(GuardError):
            rc.FirebaseRestClient(guard.GuardedConfig("epicverse-prod", Secret(KEY)), FakeTransport())

    def test_a_non_callable_transport_is_refused(self):
        with self.assertRaises(GuardError):
            rc.FirebaseRestClient(config(), "https://identitytoolkit.googleapis.com")

    def test_the_project_is_rechecked_on_every_call_before_a_request_exists(self):
        c, transport = client()
        object.__setattr__(c._config, "project_id", "epicverse-prod")           # tampering after construction
        with self.assertRaises(GuardError):
            c.sign_in(S.synthetic_email(), S.synthetic_password())
        self.assertEqual(transport.requests, [])

    def test_the_real_transport_cannot_be_created_while_live_mode_is_disabled(self):
        with self.assertRaises(guard.LiveModeDisabled):
            rc.HttpxTransport()


class RequestShape(unittest.TestCase):
    def test_sign_in_request_is_https_to_the_approved_host_with_safe_settings(self):
        c, t = client(sign_in_ok())
        email, password = S.synthetic_email(), S.synthetic_password()
        c.sign_in(email, password)
        (r,) = t.requests
        self.assertEqual((r.method, r.url, r.content_type), ("POST", SIGN_IN_URL, "application/json"))
        self.assertIs(r.follow_redirects, False)                                  # G
        self.assertIs(r.trust_env, False)                                         # H
        self.assertEqual((r.connect_timeout, r.read_timeout), (guard.CONNECT_TIMEOUT_SECONDS, guard.READ_TIMEOUT_SECONDS))
        self.assertEqual(r.max_response_bytes, guard.MAX_RESPONSE_BYTES)
        self.assertNotIn("key=", r.url)                                           # the API key is a separate parameter, never inside the URL
        self.assertEqual([name for name, _ in r.params], ["key"])
        self.assertEqual(json.loads(r.body), {"email": email, "password": password, "returnSecureToken": True})
        for text in (repr(r), str(r)):
            self.assertNotIn(password, text)
            self.assertNotIn(email, text)
            self.assertNotIn(KEY, text)

    def test_refresh_request_goes_to_the_token_service_form_encoded(self):
        c, t = client((200, {"id_token": "i" * 30, "refresh_token": "r" * 30, "expires_in": "3600", "user_id": "uid-1"}, None))
        c.refresh(Secret("rt-" + "c" * 40))
        (r,) = t.requests
        self.assertEqual((r.url, r.content_type), (REFRESH_URL, "application/x-www-form-urlencoded"))
        self.assertTrue(r.body.startswith(b"grant_type=refresh_token&refresh_token="))

    def test_every_request_passes_the_guard_before_it_is_sent(self):
        c, t = client(sign_in_ok(), sign_in_ok())
        c.sign_in(S.synthetic_email(), S.synthetic_password())
        for request in t.requests:
            guard.assert_request_safe(request)


class Classification(unittest.TestCase):
    def outcome(self, answer, op="sign_in"):
        c, _ = client(answer)
        if op == "sign_in":
            return c.sign_in(S.synthetic_email(), S.synthetic_password())
        return c.refresh(Secret("rt-" + "d" * 40))

    def test_success_returns_redacted_tokens(self):
        result = self.outcome(sign_in_ok())
        self.assertEqual((result.outcome, result.http_class), (rc.OK, "2xx"))
        self.assertEqual((result.tokens.uid, result.tokens.expires_in), ("uid-1", 3600))
        self.assertTrue(result.tokens.id_token.reveal().startswith("id-"))

    def test_wrong_password_and_unknown_email_responses(self):
        self.assertEqual(self.outcome(error("INVALID_PASSWORD")).outcome, rc.WRONG_CREDENTIALS)
        self.assertEqual(self.outcome(error("INVALID_LOGIN_CREDENTIALS")).outcome, rc.WRONG_CREDENTIALS)   # what enumeration protection ON returns
        self.assertEqual(self.outcome(error("EMAIL_NOT_FOUND")).outcome, rc.UNKNOWN_EMAIL)                  # what it returns with protection OFF
        self.assertEqual(self.outcome(error("INVALID_PASSWORD : extra text a user typed")).outcome, rc.WRONG_CREDENTIALS)

    def test_disabled_and_throttled(self):
        self.assertEqual(self.outcome(error("USER_DISABLED")).outcome, rc.USER_DISABLED)
        self.assertEqual(self.outcome(error("TOO_MANY_ATTEMPTS_TRY_LATER")).outcome, rc.THROTTLED)

    def test_W_unexpected_errors_fail_safely_and_are_never_credential_results(self):
        for status, code in ((400, "API_KEY_INVALID"), (400, "OPERATION_NOT_ALLOWED"), (400, "PASSWORD_LOGIN_DISABLED"), (401, "UNAUTHENTICATED"),
                             (403, "PERMISSION_DENIED"), (403, "API_KEY_SERVICE_BLOCKED"), (429, "QUOTA_EXCEEDED"), (400, "something odd"),
                             (400, ""), (404, "NOT_FOUND")):
            result = self.outcome(error(code, status))
            self.assertEqual(result.outcome, rc.SERVICE_ERROR, (status, code))
            self.assertNotIn(result.outcome, (rc.WRONG_CREDENTIALS, rc.UNKNOWN_EMAIL, rc.USER_DISABLED))
        for status in (500, 502, 503, 504):
            self.assertEqual(self.outcome(error("ANYTHING", status)).outcome, rc.SERVICE_ERROR)
        self.assertEqual(self.outcome((418, {}, None)).outcome, rc.SERVICE_ERROR)
        self.assertEqual(self.outcome((100, {}, None)).outcome, rc.UNEXPECTED_ERROR)

    def test_refresh_classification(self):
        self.assertEqual(self.outcome(error("INVALID_REFRESH_TOKEN"), "refresh").outcome, rc.TOKEN_INVALID)
        self.assertEqual(self.outcome(error("TOKEN_EXPIRED"), "refresh").outcome, rc.TOKEN_INVALID)
        self.assertEqual(self.outcome(error("USER_DISABLED"), "refresh").outcome, rc.USER_DISABLED)
        self.assertEqual(self.outcome(error("USER_NOT_FOUND"), "refresh").outcome, rc.USER_NOT_FOUND)
        self.assertEqual(self.outcome(error("INVALID_PASSWORD"), "refresh").outcome, rc.SERVICE_ERROR)     # a sign-in code means nothing here

    def test_malformed_answers_are_bad_responses(self):
        for body in (b"not json", b"", b"[]", b'"text"', b"null", b'{"a":1,"a":2}', b"\xff\xfe", b"{" * 5000):
            self.assertEqual(self.outcome((200, body, None)).outcome, rc.BAD_RESPONSE, body[:10])
        for doc in ({}, {"idToken": 5}, {"idToken": "x", "refreshToken": "y", "expiresIn": "abc", "localId": "u"},
                    {"idToken": "x", "refreshToken": "y", "expiresIn": "3600"}, {"idToken": "x" * 5000, "refreshToken": "y", "expiresIn": "3600", "localId": "u"}):
            self.assertEqual(self.outcome((200, doc, None)).outcome, rc.BAD_RESPONSE)

    def test_V_oversized_responses_are_rejected(self):
        big = b'{"padding":"' + b"a" * (guard.MAX_RESPONSE_BYTES + 10) + b'"}'
        result = self.outcome((200, big, None))
        self.assertEqual((result.outcome, result.cause), (rc.BAD_RESPONSE, "too_large"))
        self.assertEqual(self.outcome((400, big, None)).outcome, rc.BAD_RESPONSE)

    def test_G_redirects_are_not_followed_or_accepted(self):
        for status in (301, 302, 303, 307, 308):
            result = self.outcome((status, {}, None))
            self.assertEqual(result.outcome, rc.REDIRECT_REJECTED)
        result = self.outcome((200, {"idToken": "x"}, "https://evil.example/v1/token"))      # a transport that followed a redirect elsewhere
        self.assertEqual(result.outcome, rc.REDIRECT_REJECTED)

    def test_transport_failures_are_classified_without_their_text(self):
        for cause in ("connect", "timeout", "reset", "tls", "dns", "too_large", "something-else"):
            c, _ = client(rc.TransportFailure(cause))
            result = c.sign_in(S.synthetic_email(), S.synthetic_password())
            self.assertEqual(result.outcome, rc.TRANSPORT_ERROR)
            self.assertIn(result.cause, rc.TRANSPORT_CAUSES)
        c, _ = client(RuntimeError("secret-text-that-must-not-appear"))
        result = c.sign_in(S.synthetic_email(), S.synthetic_password())
        self.assertEqual((result.outcome, result.cause), (rc.TRANSPORT_ERROR, "other"))
        self.assertNotIn("secret-text", json.dumps(result.diagnostic()))

    def test_a_transport_returning_the_wrong_type_is_a_bad_response(self):
        c = rc.FirebaseRestClient(config(), lambda request: "oops")
        self.assertEqual(c.sign_in(S.synthetic_email(), S.synthetic_password()).outcome, rc.BAD_RESPONSE)


class NoRetries(unittest.TestCase):
    def test_U_exactly_one_request_for_every_kind_of_failure(self):
        for answer in (error("INVALID_PASSWORD"), error("ANY", 503), (302, {}, None), rc.TransportFailure("timeout"), (200, b"junk", None),
                       error("TOO_MANY_ATTEMPTS_TRY_LATER"), RuntimeError("x")):
            c, t = client(answer, sign_in_ok())
            c.sign_in(S.synthetic_email(), S.synthetic_password())
            self.assertEqual(len(t.requests), 1, answer)
        c, t = client(error("ANY", 503), (200, {}, None))
        c.refresh(Secret("rt-" + "e" * 40))
        self.assertEqual(len(t.requests), 1)


class Inputs(unittest.TestCase):
    def test_bad_inputs_are_rejected_before_any_request_without_echoing_them(self):
        c, t = client()
        secret = "pw-" + S.synthetic_password()
        for email in (None, "", "a", "no-at-sign", "a@@b.invalid", "@x.invalid", "x@", "a b@x.invalid", "a@x.invalid\n", "x" * 250 + "@a.invalid", 5):
            with self.assertRaises(rc.InputRejected) as raised:
                c.sign_in(email, secret)
            if isinstance(email, str) and len(email) > 8:                              # shorter values would match ordinary words by chance
                self.assertNotIn(email, str(raised.exception))
        for password in (None, "", "x" * 5000, "a\x00b", 7, b"bytes"):
            with self.assertRaises(rc.InputRejected) as raised:
                c.sign_in(S.synthetic_email(), password)
            self.assertNotIn("x" * 20, str(raised.exception))
        for token in (None, "", "has space", "x" * 5000, "a\nb", 3):
            with self.assertRaises(rc.InputRejected):
                c.refresh(token)
        self.assertEqual(t.requests, [])

    def test_credentials_are_collected_with_hidden_prompts_only(self):
        asked = []
        password, key = S.synthetic_password(), S.synthetic_api_key()
        answers = iter([password, key])
        email, got_password, got_key = rc.collect_runtime_credentials(lambda prompt: asked.append(prompt) or next(answers),
                                                                      lambda prompt: asked.append(prompt) or "g3-user@example.invalid")
        self.assertEqual(len(asked), 3)
        self.assertEqual((got_password.reveal(), got_key.reveal(), email), (password, key, "g3-user@example.invalid"))
        for text in (repr(got_password), str(got_key), "%s" % got_password):
            self.assertNotIn(password, text)
            self.assertNotIn(key, text)


class Redaction(unittest.TestCase):
    def test_L_M_nothing_secret_reaches_output_logs_errors_reprs_or_diagnostics(self):
        email, password = S.synthetic_email(), S.synthetic_password()
        id_token, refresh = "ID." + S.synthetic_password() + ".sig", "RT-" + S.synthetic_password()
        secrets_ = [email, password, id_token, refresh, KEY]
        out, err = io.StringIO(), io.StringIO()
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        root = logging.getLogger()
        handler, old_level = Capture(), root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        texts = []
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                answers = [sign_in_ok(id_token, refresh), error("INVALID_PASSWORD"), error("EMAIL_NOT_FOUND"), error("ANY", 503), (302, {}, None),
                           rc.TransportFailure("reset"), RuntimeError(email + password), (200, b"garbage " + password.encode(), None),
                           (200, {"idToken": id_token}, None)]
                c, t = client(*answers)
                for _ in answers:
                    result = c.sign_in(email, password)
                    texts += [repr(result), str(result), json.dumps(result.diagnostic()), repr(result.tokens), str(result.tokens)]
                for request in t.requests:
                    texts += [repr(request), str(request)]
                with self.assertRaises(rc.InputRejected) as raised:
                    c.sign_in("bad" + password, password)
                texts.append(str(raised.exception) + repr(raised.exception))
                with self.assertRaises(GuardError) as guard_error:
                    guard.validate_environment({"GOOGLE_APPLICATION_CREDENTIALS": password})
                texts.append(str(guard_error.exception))
                texts += [repr(c._config), repr(Secret(refresh)), repr(rc.Tokens(id_token, refresh, 1, "u"))]
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        everything = "\n".join(texts + [out.getvalue(), err.getvalue()] + records)
        for secret in secrets_:
            self.assertNotIn(secret, everything)
        self.assertEqual(out.getvalue() + err.getvalue(), "")                  # the client prints nothing at all

    def test_tokens_cannot_be_pickled_and_diagnostics_have_a_fixed_shape(self):
        result = client(sign_in_ok())[0].sign_in(S.synthetic_email(), S.synthetic_password())
        with self.assertRaises(TypeError):
            pickle.dumps(result.tokens.id_token)
        self.assertEqual(set(result.diagnostic()), {"operation", "outcome", "http_class", "cause"})


class NoNetwork(unittest.TestCase):
    def test_S_T_the_transport_is_always_the_fake_and_nothing_tried_to_use_the_network(self):
        self.assertEqual(S.ATTEMPTS, [])
        c, t = client(sign_in_ok())
        c.sign_in(S.synthetic_email(), S.synthetic_password())
        self.assertEqual(len(t.requests), 1)
        self.assertEqual(S.ATTEMPTS, [])

    def test_a_client_built_with_a_denied_transport_cannot_reach_the_network(self):
        import socket

        def sneaky(request):
            socket.create_connection(("identitytoolkit.googleapis.com", 443))

        c = rc.FirebaseRestClient(config(), sneaky)
        with self.assertRaises(S.NetworkDenied):                                    # NetworkDenied is a BaseException: the client's `except Exception` cannot hide it
            c.sign_in(S.synthetic_email(), S.synthetic_password())
        del S.ATTEMPTS[:]


if __name__ == "__main__":
    unittest.main()
