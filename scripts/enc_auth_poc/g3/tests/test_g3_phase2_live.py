"""Phase 2 offline tests of the live path: approval gate, live transport (with a FAKE connection class - no socket is ever opened), and the
complete live-run flow against a fake Firebase. The network-denial layer is active throughout and must record nothing."""
import contextlib
import http.client
import io
import json
import logging
import socket
import ssl
import unittest

import _support as S
from _support import ALLOWED, make_pki, sign_rs256
import g3_guard as guard
import g3_live_run as live
import g3_live_transport as lt
import g3_rest_client as rc

NOW = 1_800_000_000
KEY, CERT, KID = make_pki()
HEADER = {"alg": "RS256", "kid": KID, "typ": "JWT"}
SIGN_IN = ("POST", guard.SIGN_IN_URL)
REFRESH = ("POST", guard.REFRESH_URL)
CERTS = ("GET", guard.CERTS_URL)


class FakeSock:
    def __init__(self):
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value


class FakeFirebase:
    """Plays Firebase. `answers` maps (method, url) -> (status, body) or an exception to raise from connect()/getresponse()."""

    def __init__(self, answers):
        self.answers, self.connections = answers, []

    def factory(self, host, port, timeout=None, context=None):
        conn = FakeConnection(self, host, port, timeout, context)
        self.connections.append(conn)
        return conn


class FakeResponse:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, n=-1):
        chunk, self._body = self._body[:n], self._body[n:]
        return chunk


class FakeConnection:
    def __init__(self, server, host, port, timeout, context):
        self.server, self.host, self.port, self.timeout, self.context = server, host, port, timeout, context
        self.sock, self.closed, self.sent = None, False, None

    def connect(self):
        self.sock = FakeSock()

    def request(self, method, target, body=None, headers=None):
        self.sent = (method, target, body, headers)

    def getresponse(self):
        method, target = self.sent[0], self.sent[1]
        key = (method, "https://" + self.host + target.split("?")[0])
        answer = self.server.answers[key]
        if isinstance(answer, BaseException):
            raise answer
        return FakeResponse(*answer)

    def close(self):
        self.closed = True


def good_answers(mutate=None):
    id_a = sign_rs256(KEY, HEADER, auth_time=NOW - 120, iat=NOW - 60, exp=NOW + 3540)
    id_b = sign_rs256(KEY, HEADER, auth_time=NOW - 120, iat=NOW - 30, exp=NOW + 3570)
    answers = {
        SIGN_IN: (200, json.dumps({"idToken": id_a, "refreshToken": "RT-" + S.synthetic_password(), "expiresIn": "3600", "localId": "synthetic-uid-1",
                                   "email": "g3-user@example.invalid"}).encode()),
        REFRESH: (200, json.dumps({"id_token": id_b, "refresh_token": "RT2-" + S.synthetic_password(), "expires_in": "3600",
                                   "user_id": "synthetic-uid-1"}).encode()),
        CERTS: (200, json.dumps({KID: CERT}).encode()),
    }
    if mutate:
        mutate(answers)
    return answers


def approved():
    return guard.grant_live_approval(ALLOWED, ALLOWED, True)


def request(method, url, key=None, body=b"{}"):
    params = (("key", guard.Secret(key or S.synthetic_api_key())),) if method == "POST" else ()
    return rc.TransportRequest(method, url, params, body if method == "POST" else b"", "application/json" if method == "POST" else "",
                               5.0, 10.0, 65536)


class Approval(unittest.TestCase):
    def test_live_mode_constant_stays_off_and_there_is_no_global_toggle(self):
        self.assertIs(guard.LIVE_MODE_ENABLED, False)
        with self.assertRaises(guard.LiveModeDisabled):
            guard.require_live_mode()

    def test_an_approval_cannot_be_built_directly(self):
        with self.assertRaises(guard.LiveModeDisabled):
            guard.LiveApproval(ALLOWED)
        with self.assertRaises(guard.LiveModeDisabled):
            guard.LiveApproval(ALLOWED, _key=object())

    def test_approval_needs_an_interactive_session_and_the_exact_project_id_typed(self):
        for typed in (None, "", "yes", "y", "epicverse-prod", ALLOWED + " ", ALLOWED.upper(), ALLOWED + "\n", [ALLOWED]):
            with self.assertRaises(guard.LiveModeDisabled, msg=repr(typed)):
                guard.grant_live_approval(ALLOWED, typed, True)
        for interactive in (False, None, 1, "yes"):
            with self.assertRaises(guard.LiveModeDisabled):
                guard.grant_live_approval(ALLOWED, ALLOWED, interactive)
        with self.assertRaises(guard.GuardError):
            guard.grant_live_approval("epicverse-prod", "epicverse-prod", True)

    def test_each_approved_request_can_be_used_once_and_nothing_else(self):
        a = approved()
        for method, url in (SIGN_IN, REFRESH, CERTS):
            a.consume(method, url)
        self.assertEqual(a.used, 3)
        for method, url in (SIGN_IN, REFRESH, CERTS):
            with self.assertRaises(guard.LiveModeDisabled):
                a.consume(method, url)
        self.assertEqual(a.used, 3)

    def test_unapproved_requests_are_refused(self):
        a = approved()
        for method, url in (("GET", guard.SIGN_IN_URL), ("POST", guard.CERTS_URL), ("DELETE", guard.REFRESH_URL), ("POST", "https://evil.example/v1/token"),
                            ("POST", "http://securetoken.googleapis.com/v1/token"), ("GET", "https://www.googleapis.com/oauth2/v1/certs"),
                            ("POST", "https://oauth2.googleapis.com/token"), ("POST", "https://iamcredentials.googleapis.com/v1/x:signBlob"),
                            ("POST", "https://identitytoolkit.googleapis.com/v1/accounts:delete"), ("POST", "https://identitytoolkit.googleapis.com/v1/accounts:update")):
            with self.assertRaises(guard.GuardError, msg=url):
                a.consume(method, url)
        self.assertEqual(a.used, 0)

    def test_a_closed_approval_is_useless(self):
        a = approved()
        a.close()
        with self.assertRaises(guard.LiveModeDisabled):
            a.consume(*SIGN_IN)


class TlsAndTransportSettings(unittest.TestCase):
    def test_tls_verification_is_on(self):
        ctx = lt.make_tls_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        self.assertGreaterEqual(ctx.minimum_version, ssl.TLSVersion.TLSv1_2)

    def test_the_transport_cannot_be_created_without_an_approval(self):
        for bad in (None, object(), True, "approved"):
            with self.assertRaises(guard.LiveModeDisabled):
                lt.LiveTransport(bad)

    def test_a_request_uses_https_port_443_verified_context_and_split_timeouts(self):
        server = FakeFirebase(good_answers())
        transport = lt.LiveTransport(approved(), server.factory)
        key = S.synthetic_api_key()
        response = transport(request("POST", guard.SIGN_IN_URL, key))
        conn = server.connections[0]
        self.assertEqual((conn.host, conn.port, conn.timeout), ("identitytoolkit.googleapis.com", 443, 5.0))
        self.assertIs(conn.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(conn.sock.timeout, 10.0)
        method, target, body, headers = conn.sent
        self.assertEqual((method, target.split("?")[0]), ("POST", "/v1/accounts:signInWithPassword"))
        self.assertEqual(target.split("?")[1], "key=" + key)
        self.assertEqual(headers["Connection"], "close")
        self.assertTrue(conn.closed)
        self.assertEqual((response.status, response.url), (200, guard.SIGN_IN_URL))

    def test_the_certificate_request_has_no_key_no_body_and_no_content_type(self):
        server = FakeFirebase(good_answers())
        lt.LiveTransport(approved(), server.factory)(request("GET", guard.CERTS_URL))
        method, target, body, headers = server.connections[0].sent
        self.assertEqual((method, target, body), ("GET", "/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com", None))
        self.assertNotIn("Content-Type", headers)
        self.assertNotIn("key=", target)

    def test_nothing_is_connected_when_the_request_or_approval_is_refused(self):
        server = FakeFirebase(good_answers())
        transport = lt.LiveTransport(approved(), server.factory)
        bad = rc.TransportRequest("POST", "https://evil.example/v1/token", (("key", guard.Secret(S.synthetic_api_key())),), b"{}", "application/json",
                                  5.0, 10.0, 65536)
        with self.assertRaises(guard.GuardError):
            transport(bad)
        redirecting = rc.TransportRequest("POST", guard.SIGN_IN_URL, (("key", guard.Secret(S.synthetic_api_key())),), b"{}", "application/json",
                                          5.0, 10.0, 65536, follow_redirects=True)
        with self.assertRaises(guard.GuardError):
            transport(redirecting)
        transport(request("POST", guard.SIGN_IN_URL))
        with self.assertRaises(guard.LiveModeDisabled):                   # the second sign-in (a retry) is refused BEFORE a connection exists
            transport(request("POST", guard.SIGN_IN_URL))
        self.assertEqual(len(server.connections), 1)

    def test_oversized_responses_are_cut_off(self):
        server = FakeFirebase({SIGN_IN: (200, b"a" * (guard.MAX_RESPONSE_BYTES + 1))})
        with self.assertRaises(rc.TransportFailure) as raised:
            lt.LiveTransport(approved(), server.factory)(request("POST", guard.SIGN_IN_URL))
        self.assertEqual(raised.exception.cause, "too_large")
        self.assertTrue(server.connections[0].closed)

    def test_redirect_answers_are_returned_unfollowed_and_the_client_rejects_them(self):
        server = FakeFirebase({SIGN_IN: (302, b"")})
        config = guard.load_config(ALLOWED, S.synthetic_api_key(), env={})
        client = rc.FirebaseRestClient(config, lt.LiveTransport(approved(), server.factory))
        result = client.sign_in(S.synthetic_email(), S.synthetic_password())
        self.assertEqual(result.outcome, rc.REDIRECT_REJECTED)
        self.assertEqual(len(server.connections), 1)

    def test_transport_exceptions_become_fixed_causes_without_their_text(self):
        secret = "SECRET-" + S.synthetic_password()
        cases = ((ssl.SSLCertVerificationError(secret), "tls"), (ssl.SSLError(secret), "tls"), (socket.gaierror(secret), "dns"),
                 (socket.timeout(secret), "timeout"), (TimeoutError(secret), "timeout"), (ConnectionRefusedError(secret), "connect"),
                 (ConnectionResetError(secret), "reset"), (BrokenPipeError(secret), "reset"), (http.client.RemoteDisconnected(secret), "reset"),
                 (OSError(secret), "other"))
        for exc, cause in cases:
            server = FakeFirebase({SIGN_IN: exc})
            with self.assertRaises(rc.TransportFailure) as raised:
                lt.LiveTransport(approved(), server.factory)(request("POST", guard.SIGN_IN_URL))
            self.assertEqual(raised.exception.cause, cause, type(exc).__name__)
            self.assertNotIn(secret, str(raised.exception) + repr(raised.exception))
            self.assertIsNone(raised.exception.__cause__)
            self.assertTrue(server.connections[0].closed)

    def test_source_never_weakens_tls_or_follows_redirects(self):
        text = __import__("pathlib").Path(lt.__file__).read_text(encoding="utf-8")
        for forbidden in ("CERT_NONE", "_create_unverified_context", "check_hostname = False", "verify=False", "HTTPConnection(", "urlopen", "ProxyHandler",
                          "getproxies", "os.environ"):
            self.assertNotIn(forbidden, text)


class FullFlow(unittest.TestCase):
    def setUp(self):
        live._RAN.clear()
        self.lines = []
        self.password, self.api_key, self.email = S.synthetic_password(), S.synthetic_api_key(), "g3-user@example.invalid"

    def execute(self, answers=None, typed=ALLOWED, interactive=True, env=None, key=None, server=None):
        server = server or FakeFirebase(answers or good_answers())
        values = iter([self.email, self.password, self.api_key if key is None else key])
        prompts = []

        def input_fn(prompt):
            prompts.append(prompt)
            if isinstance(typed, BaseException):
                raise typed
            return typed

        code = live.run(interactive=interactive, getpass_fn=lambda prompt: prompts.append(prompt) or next(values), input_fn=input_fn,
                        out=self.lines.append, transport_factory=lambda approval: lt.LiveTransport(approval, server.factory), clock=lambda: NOW,
                        env={} if env is None else env)
        self.server, self.prompts = server, prompts
        return code

    def text(self):
        return "\n".join(self.lines)

    def test_the_whole_run_succeeds_with_exactly_three_requests_and_verified_tokens(self):
        code = self.execute()
        out = self.text()
        self.assertEqual(code, 0, out)
        self.assertEqual([(c.sent[0], c.host) for c in self.server.connections],
                         [("POST", "identitytoolkit.googleapis.com"), ("POST", "securetoken.googleapis.com"), ("GET", "www.googleapis.com")])
        for needle in ("A sign-in: outcome=ok", "B refresh: outcome=ok", "C certificates: outcome=ok", "A: signature_verified=True", "B: signature_verified=True",
                       "uid_unchanged=True", "auth_time_preserved=True", "email_verified=false", "sign_in_provider=password", "requests used: 3 of 3",
                       "live approval closed", "A: response_email_matches_input=True", "A: sub_matches_response_uid=True"):
            self.assertIn(needle, out)
        self.assertIn(ALLOWED, out)                                                      # the confirmation names the project
        for op in live.APPROVED_OPERATIONS:
            self.assertIn(op, out)

    def test_nothing_secret_is_ever_printed_logged_or_left_in_the_output(self):
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler, root = Capture(), logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        answers = good_answers()
        stdout, stderr = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.execute(answers)
        finally:
            root.removeHandler(handler)
        everything = "\n".join([self.text(), stdout.getvalue(), stderr.getvalue()] + records)
        secrets_ = [self.password, self.api_key, self.email, "synthetic-uid-1", KID, CERT[:60]]
        for blob in answers.values():
            body = json.loads(blob[1])
            secrets_ += [v for k, v in body.items() if k in ("idToken", "refreshToken", "id_token", "refresh_token")]
        for secret in secrets_:
            self.assertNotIn(secret, everything)

    def test_not_interactive_means_nothing_is_asked_and_nothing_is_sent(self):
        code = self.execute(interactive=False)
        self.assertEqual((code, self.prompts, self.server.connections), (2, [], []))

    def test_cancellation_sends_nothing(self):
        for typed in ("", "no", "y", "yes", "epicverse-prod", ALLOWED.upper(), EOFError(), KeyboardInterrupt()):
            live._RAN.clear()
            self.lines.clear()
            code = self.execute(typed=typed)
            self.assertEqual(code, 3, repr(typed))
            self.assertEqual(self.server.connections, [], repr(typed))
            self.assertIn("cancelled", self.text())
            self.assertIn("live approval closed" if False else "cancelled", self.text())

    def test_a_second_run_in_the_same_process_is_refused(self):
        self.assertEqual(self.execute(), 0)
        self.lines.clear()
        self.assertEqual(self.execute(), 2)
        self.assertEqual(self.server.connections, [])

    def test_conflicting_environment_stops_before_any_prompt(self):
        for env in ({"GOOGLE_APPLICATION_CREDENTIALS": "x"}, {"FIREBASE_CONFIG": "{}"}, {"GOOGLE_CLOUD_PROJECT": "epicverse-prod"},
                    {"FIREBASE_AUTH_EMULATOR_HOST": "127.0.0.1:9099"}):
            live._RAN.clear()
            code = self.execute(env=env)
            self.assertEqual((code, self.prompts, self.server.connections), (2, [], []))

    def test_missing_or_malformed_api_key_stops_before_confirmation(self):
        for key in ("", "not-a-key", self.api_key[:-1]):
            live._RAN.clear()
            self.assertEqual(self.execute(key=key), 2)
            self.assertEqual(self.server.connections, [])
            self.assertEqual(len(self.prompts), 3)                                      # only the three hidden prompts; no confirmation requested

    def test_a_failed_sign_in_skips_refresh_and_certificates(self):
        def wrong_password(answers):
            answers[SIGN_IN] = (400, json.dumps({"error": {"code": 400, "message": "INVALID_LOGIN_CREDENTIALS"}}).encode())
        code = self.execute(good_answers(wrong_password))
        self.assertEqual(code, 1)
        self.assertEqual(len(self.server.connections), 1)
        self.assertIn("outcome=wrong_credentials", self.text())
        self.assertIn("skipped", self.text())

    def test_unexpected_firebase_errors_are_reported_by_category_only(self):
        for status, message, outcome in ((403, "PERMISSION_DENIED : secret detail", "service_error"), (400, "API_KEY_INVALID", "service_error"),
                                         (503, "UNAVAILABLE", "service_error"), (400, "TOO_MANY_ATTEMPTS_TRY_LATER", "throttled")):
            live._RAN.clear()
            self.lines.clear()

            def fail(answers, status=status, message=message):
                answers[SIGN_IN] = (status, json.dumps({"error": {"code": status, "message": message}}).encode())
            self.assertEqual(self.execute(good_answers(fail)), 1)
            self.assertIn("outcome=" + outcome, self.text())
            self.assertNotIn("secret detail", self.text())
            self.assertEqual(len(self.server.connections), 1)

    def test_a_refresh_failure_still_allows_certificates_but_the_run_reports_failure(self):
        def refresh_fails(answers):
            answers[REFRESH] = (400, json.dumps({"error": {"code": 400, "message": "INVALID_REFRESH_TOKEN"}}).encode())
        code = self.execute(good_answers(refresh_fails))
        self.assertEqual(code, 1)
        self.assertEqual(len(self.server.connections), 3)
        self.assertIn("B refresh: outcome=token_invalid", self.text())
        self.assertIn("A: signature_verified=True", self.text())

    def test_unavailable_certificates_stop_signature_verification_and_trust_nothing(self):
        def no_certs(answers):
            answers[CERTS] = (503, b"{}")
        code = self.execute(good_answers(no_certs))
        self.assertEqual(code, 1)
        self.assertIn("signature verification is NOT possible", self.text())
        self.assertNotIn("signature_verified=True", self.text())

    def test_certificate_failures_of_every_kind_stop_verification(self):
        for answer in ((200, b"not json"), (200, b"{}"), (302, b""), (200, json.dumps({KID: "garbage"}).encode()), (200, b"x" * 70000)):
            live._RAN.clear()
            self.lines.clear()

            def bad(answers, answer=answer):
                answers[CERTS] = answer
            self.assertEqual(self.execute(good_answers(bad)), 1)
            self.assertNotIn("signature_verified=True", self.text())

    def test_tokens_with_bad_signatures_or_claims_are_reported_as_failed(self):
        other_key, _, _ = make_pki()
        for label, token in (("signature", sign_rs256(other_key, HEADER)), ("project", sign_rs256(KEY, HEADER, project="epicverse-prod")),
                             ("audience", sign_rs256(KEY, HEADER, aud="epicverse-prod")), ("expired", sign_rs256(KEY, HEADER, iat=NOW - 9000, exp=NOW - 10, auth_time=NOW - 9100)),
                             ("algorithm", "e30.e30.e30")):
            live._RAN.clear()
            self.lines.clear()

            def bad(answers, token=token):
                body = json.loads(answers[SIGN_IN][1])
                body["idToken"] = token
                answers[SIGN_IN] = (200, json.dumps(body).encode())
            self.assertEqual(self.execute(good_answers(bad)), 1, label)
            self.assertIn("A: signature/claims verification FAILED", self.text(), label)

    def test_a_forged_token_for_the_wrong_project_is_never_summarised(self):
        def bad(answers):
            body = json.loads(answers[SIGN_IN][1])
            body["idToken"] = sign_rs256(KEY, HEADER, project="epicverse-prod")
            answers[SIGN_IN] = (200, json.dumps(body).encode())
        self.execute(good_answers(bad))
        self.assertNotIn("A: signature_verified=True", self.text())

    def test_unexpected_exceptions_are_reported_without_details(self):
        server = FakeFirebase(good_answers())
        server.factory = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SECRET-detail"))
        self.assertEqual(self.execute(server=server), 1)
        self.assertNotIn("SECRET-detail", self.text())
        self.assertIn("live approval closed", self.text())

    def test_the_request_budget_cannot_be_exceeded_even_by_misuse(self):
        a = approved()
        server = FakeFirebase(good_answers())
        t = lt.LiveTransport(a, server.factory)
        for method, url in (SIGN_IN, REFRESH, CERTS):
            t(request(method, url))
        for method, url in (SIGN_IN, REFRESH, CERTS):
            with self.assertRaises(guard.LiveModeDisabled):
                t(request(method, url))
        self.assertEqual(len(server.connections), 3)


class NoRealNetwork(unittest.TestCase):
    def test_no_socket_or_name_resolution_was_attempted_by_any_test(self):
        self.assertEqual(S.ATTEMPTS, [])

    def test_the_default_connection_class_is_never_instantiated_in_tests(self):
        # LiveTransport(approval) without a factory would use http.client.HTTPSConnection; the denial layer proves no socket opens
        transport = lt.LiveTransport(approved())
        with self.assertRaises(S.NetworkDenied):
            transport(request("GET", guard.CERTS_URL))
        del S.ATTEMPTS[:]


if __name__ == "__main__":
    unittest.main()
