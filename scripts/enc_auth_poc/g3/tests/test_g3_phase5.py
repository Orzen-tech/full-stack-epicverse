"""Phase 5 PREPARATION tests (offline): the IAM signBlob signer, the custom-token exchange, the disabled-by-default runner, and the guard rules for
the new request kinds. Everything runs against a fake Firebase/Google behind the real LiveTransport; no socket is opened, nothing is signed for real."""
import base64
import contextlib
import io
import json
import unittest
from unittest import mock

import _support as S
from _support import ALLOWED, make_pki, sign_rs256
import g3_custom_token as ct
import g3_guard as guard
import g3_iam_signer as sg
import g3_live_custom_token as runner
import g3_live_transport as lt
import g3_rest_client as rc
from test_g3_phase2_live import FakeFirebase, FakeConnection, REFRESH, CERTS, SIGN_IN

NOW = 1_800_000_000
SA = "g3-signer@epicverse-g3-auth-test.iam.gserviceaccount.com"
SIGN_BLOB = ("POST", guard.sign_blob_url(SA))
CUSTOM = ("POST", guard.CUSTOM_TOKEN_URL)
KEY, CERT, KID = make_pki()
HEADER = {"alg": "RS256", "kid": KID, "typ": "JWT"}


def bearer_token():
    return "AT-" + S.synthetic_password() * 2


def google_error(status, code, reason=None):
    error = {"code": status, "status": code, "message": "free text that must never be shown"}
    if reason:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
    return (status, json.dumps({"error": error}).encode())


def sign_blob_ok(length=256):
    return (200, json.dumps({"keyId": "0123456789abcdef0123456789abcdef01234567", "signedBlob": base64.b64encode(b"\x07" * length).decode()}).encode())


def approval_for_signer():
    """An approval for the unit tests of the signer and the exchange client: the phase pairs, each once, WITHOUT the order rule (that is tested separately)."""
    return guard.grant_live_approval(ALLOWED, ALLOWED, True, {SIGN_IN: 1, CERTS: 1, SIGN_BLOB: 1, CUSTOM: 1, REFRESH: 1})


def signer_over(answer, token=None):
    server = FakeFirebase({SIGN_BLOB: answer})
    approval = approval_for_signer()
    return sg.IamSignBlobSigner(SA, guard.Secret(token or bearer_token()), lt.LiveTransport(approval, server.factory)), server, approval


DATA = ct.signing_input(ct.build_claims(SA, "synthetic-uid-1", NOW))


class GuardRules(unittest.TestCase):
    def test_service_accounts_must_belong_to_the_test_project(self):
        self.assertEqual(guard.validate_service_account(SA), SA)
        for bad in ("g3-signer@epicverse-prod.iam.gserviceaccount.com", "g3-signer@epicverse-g3-auth-test.gserviceaccount.com", "G3-signer@" + SA.split("@")[1],
                    "ab@" + SA.split("@")[1], "x" * 31 + "@" + SA.split("@")[1], "1signer@" + SA.split("@")[1], "g3-signer-@" + SA.split("@")[1],
                    "g3_signer@" + SA.split("@")[1], SA + ".evil.example", "a@b@" + SA.split("@")[1], "", None, 5):
            with self.assertRaises(guard.GuardError, msg=repr(bad)):
                guard.validate_service_account(bad)

    def test_the_phase5_pairs_are_exact(self):
        self.assertTrue(guard.is_custom_token_pair(*SIGN_BLOB))
        self.assertTrue(guard.is_custom_token_pair(*CUSTOM))
        for method, url in (("GET", guard.CUSTOM_TOKEN_URL), ("POST", guard.CUSTOM_TOKEN_URL + "?x=1"), ("POST", guard.CUSTOM_TOKEN_URL + "/"),
                            ("POST", guard.sign_blob_url(SA) + "?alt=json"), ("GET", guard.sign_blob_url(SA)),
                            ("POST", guard.SIGN_BLOB_PREFIX + "other@epicverse-prod.iam.gserviceaccount.com" + guard.SIGN_BLOB_SUFFIX),
                            ("POST", guard.SIGN_BLOB_PREFIX + SA + ":signJwt"), ("POST", guard.SIGN_BLOB_PREFIX + SA + ":generateAccessToken"),
                            ("POST", "https://iamcredentials.googleapis.com/v1/projects/other/serviceAccounts/" + SA + ":signBlob"),
                            ("POST", "http://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/" + SA + ":signBlob"),
                            ("POST", "https://iam.googleapis.com/v1/projects/-/serviceAccounts/" + SA + ":signBlob"),
                            ("POST", "https://iamcredentials.googleapis.com.evil.example/v1/projects/-/serviceAccounts/" + SA + ":signBlob"),
                            ("POST", guard.SIGN_BLOB_PREFIX + SA + " " + guard.SIGN_BLOB_SUFFIX), ("POST", None)):
            self.assertFalse(guard.is_custom_token_pair(method, url), (method, str(url)[:60]))

    def test_the_phase5_approval_is_refused_while_the_phase_is_not_approved(self):
        self.assertIs(guard.CUSTOM_TOKEN_LIVE_APPROVED, False)
        with self.assertRaises(guard.LiveModeDisabled):
            guard.grant_custom_token_approval(ALLOWED, ALLOWED, True, SA)
        self.assertIs(guard.LIVE_MODE_ENABLED, False)

    def test_the_phase_approval_needs_the_exact_project_id_typed_and_an_interactive_session(self):
        with mock.patch.object(guard, "CUSTOM_TOKEN_LIVE_APPROVED", True):
            for typed, interactive in (("", True), ("yes", True), (ALLOWED.upper(), True), (ALLOWED, False), (None, True)):
                with self.assertRaises(guard.LiveModeDisabled):
                    guard.grant_custom_token_approval(ALLOWED, typed, interactive, SA)
            with self.assertRaises(guard.GuardError):
                guard.grant_custom_token_approval(ALLOWED, ALLOWED, True, "x@epicverse-prod.iam.gserviceaccount.com")

    def test_the_phase2_to_4_approvals_cannot_use_the_new_requests(self):
        for approval in (guard.grant_live_approval(ALLOWED, ALLOWED, True), guard.grant_live_approval(ALLOWED, ALLOWED, True, guard.ERROR_TEST_BUDGET)):
            for pair in (SIGN_BLOB, CUSTOM):
                with self.assertRaises(guard.LiveModeDisabled):
                    approval.consume(*pair)
        with self.assertRaises(guard.LiveModeDisabled):
            guard.grant_live_approval(ALLOWED, ALLOWED, True, {("POST", "https://oauth2.googleapis.com/token"): 1})

    def test_the_bearer_token_is_allowed_only_on_signblob_in_the_right_shape(self):
        secret = guard.Secret(bearer_token())
        good = rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "application/json", 5.0, 10.0, 65536, bearer=secret)
        guard.assert_request_safe(good)
        for bad in (rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "application/json", 5.0, 10.0, 65536),                       # no bearer
                    rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "application/json", 5.0, 10.0, 65536, bearer="plain string"),
                    rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "application/json", 5.0, 10.0, 65536, bearer=guard.Secret("short")),
                    rc.TransportRequest("POST", guard.sign_blob_url(SA), (("key", guard.Secret(S.synthetic_api_key())),), b"{}", "application/json", 5.0, 10.0, 65536, bearer=secret),
                    rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "text/plain", 5.0, 10.0, 65536, bearer=secret),
                    rc.TransportRequest("POST", guard.SIGN_IN_URL, (("key", guard.Secret(S.synthetic_api_key())),), b"{}", "application/json", 5.0, 10.0, 65536, bearer=secret),
                    rc.TransportRequest("GET", guard.CERTS_URL, (), b"", "", 5.0, 10.0, 65536, bearer=secret),
                    rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "application/json", 5.0, 10.0, 65536, follow_redirects=True, bearer=secret)):
            with self.assertRaises(guard.GuardError):
                guard.assert_request_safe(bad)

    def test_a_bearer_never_appears_in_the_request_repr(self):
        token = bearer_token()
        request = rc.TransportRequest("POST", guard.sign_blob_url(SA), (), b"{}", "application/json", 5.0, 10.0, 65536, bearer=guard.Secret(token))
        self.assertNotIn(token, repr(request) + str(request))


class Signer(unittest.TestCase):
    def test_a_successful_call_returns_the_signature_and_sends_a_bearer_without_a_key_parameter(self):
        token = bearer_token()
        signer, server, approval = signer_over(sign_blob_ok(), token)
        signature = signer.sign(DATA)
        self.assertEqual((len(signature), signer.key_id_returned), (256, True))
        method, target, body, headers = server.connections[0].sent
        self.assertEqual((method, target), ("POST", "/v1/projects/-/serviceAccounts/" + SA + ":signBlob"))
        self.assertEqual(headers["Authorization"], "Bearer " + token)
        self.assertNotIn("key=", target)
        self.assertEqual(base64.b64decode(json.loads(body)["payload"]), DATA)
        self.assertEqual((server.connections[0].host, server.connections[0].port), ("iamcredentials.googleapis.com", 443))
        self.assertEqual(approval.used, 1)

    def test_only_signblob_gets_an_authorization_header(self):
        server = FakeFirebase({SIGN_BLOB: sign_blob_ok(), REFRESH: (200, b"{}"), CERTS: (200, b"{}")})
        transport = lt.LiveTransport(approval_for_signer(), server.factory)
        sg.IamSignBlobSigner(SA, guard.Secret(bearer_token()), transport).sign(DATA)
        transport(rc.TransportRequest("GET", guard.CERTS_URL, (), b"", "", 5.0, 10.0, 65536))
        self.assertIn("Authorization", server.connections[0].sent[3])
        self.assertNotIn("Authorization", server.connections[1].sent[3])

    def test_google_errors_become_fixed_categories_without_their_text(self):
        for answer, category in ((google_error(401, "UNAUTHENTICATED"), "unauthenticated"), (google_error(403, "PERMISSION_DENIED"), "permission_denied"),
                                 (google_error(403, "PERMISSION_DENIED", "SERVICE_DISABLED"), "service_disabled"), (google_error(404, "NOT_FOUND"), "not_found"),
                                 (google_error(400, "INVALID_ARGUMENT"), "invalid_argument"), (google_error(400, "FAILED_PRECONDITION"), "failed_precondition"),
                                 (google_error(429, "RESOURCE_EXHAUSTED"), "rate_limited"), (google_error(503, "UNAVAILABLE"), "unavailable"),
                                 (google_error(500, "WHATEVER"), "unavailable"), (google_error(418, "TEAPOT"), "unexpected")):
            signer, server, _ = signer_over(answer)
            with self.assertRaises(sg.SignerError) as raised:
                signer.sign(DATA)
            self.assertEqual(raised.exception.category, category)
            self.assertNotIn("free text", str(raised.exception) + repr(raised.exception))
            self.assertEqual(len(server.connections), 1)

    def test_malformed_responses_are_rejected(self):
        good_key = "0123456789abcdef0123456789abcdef01234567"
        for body in ({}, {"signedBlob": 5, "keyId": good_key}, {"signedBlob": "not base64!!", "keyId": good_key}, {"signedBlob": base64.b64encode(b"x" * 10).decode(), "keyId": good_key},
                     {"signedBlob": base64.b64encode(b"x" * 2000).decode(), "keyId": good_key}, {"signedBlob": base64.b64encode(b"x" * 256).decode()},
                     {"signedBlob": base64.b64encode(b"x" * 256).decode(), "keyId": "bad key!"}, {"signedBlob": base64.b64encode(b"x" * 256).decode(), "keyId": 5},
                     [1, 2]):
            signer, _, _ = signer_over((200, json.dumps(body).encode()))
            with self.assertRaises(sg.SignerError) as raised:
                signer.sign(DATA)
            self.assertEqual(raised.exception.category, "bad_response")
        for raw in (b"not json", b"", b'{"a":1,"a":2}'):
            signer, _, _ = signer_over((200, raw))
            with self.assertRaises(sg.SignerError):
                signer.sign(DATA)
        self.assertFalse(signer.key_id_returned)

    def test_redirects_oversized_answers_and_transport_failures(self):
        signer, _, _ = signer_over((302, b""))
        with self.assertRaises(sg.SignerError) as raised:
            signer.sign(DATA)
        self.assertEqual(raised.exception.category, "redirect")
        signer, _, _ = signer_over((200, b"a" * (guard.MAX_RESPONSE_BYTES + 5)))
        with self.assertRaises(sg.SignerError) as raised:
            signer.sign(DATA)
        self.assertEqual(raised.exception.category, "transport")                  # the live transport cuts oversized answers off
        for failure in (rc.TransportFailure("tls"), RuntimeError("secret detail")):
            signer = sg.IamSignBlobSigner(SA, guard.Secret(bearer_token()), lambda request, failure=failure: (_ for _ in ()).throw(failure))
            with self.assertRaises(sg.SignerError) as raised:
                signer.sign(DATA)
            self.assertEqual(raised.exception.category, "transport")
            self.assertNotIn("secret detail", str(raised.exception))

    def test_an_answer_from_a_different_url_is_rejected(self):
        signer = sg.IamSignBlobSigner(SA, guard.Secret(bearer_token()), lambda request: rc.TransportResponse(200, b"{}", "https://evil.example/x"))
        with self.assertRaises(sg.SignerError) as raised:
            signer.sign(DATA)
        self.assertEqual(raised.exception.category, "redirect")

    def test_construction_and_input_checks(self):
        transport = lambda request: None
        for account in ("g3-signer@epicverse-prod.iam.gserviceaccount.com", "", None):
            with self.assertRaises(guard.GuardError):
                sg.IamSignBlobSigner(account, guard.Secret(bearer_token()), transport)
        for token in (None, "plain", guard.Secret("short"), guard.Secret("has space " * 5), guard.Secret(5)):
            with self.assertRaises(sg.SignerError):
                sg.IamSignBlobSigner(SA, token, transport)
        with self.assertRaises(guard.GuardError):
            sg.IamSignBlobSigner(SA, guard.Secret(bearer_token()), "not callable")
        signer = sg.IamSignBlobSigner(SA, guard.Secret(bearer_token()), transport)
        for bad in (None, "text", b"", b"nodot", b"a.b.c" * 10, b"x" * 5000):
            with self.assertRaises(sg.SignerError):
                signer.sign(bad)

    def test_the_approval_is_single_use_and_a_second_call_is_loud(self):
        signer, server, _ = signer_over(sign_blob_ok())
        signer.sign(DATA)
        with self.assertRaises(guard.LiveModeDisabled):
            signer.sign(DATA)
        self.assertEqual(len(server.connections), 1)

    def test_nothing_secret_leaks_from_failures(self):
        token = bearer_token()
        signer, _, _ = signer_over(google_error(403, "PERMISSION_DENIED"), token)
        with self.assertRaises(sg.SignerError) as raised:
            signer.sign(DATA)
        text = str(raised.exception) + repr(raised.exception) + repr(signer)
        for secret in (token, base64.b64encode(DATA).decode(), SA):
            self.assertNotIn(secret, text)

    def test_the_signer_plugs_into_the_custom_token_builder(self):
        signer, _, _ = signer_over(sign_blob_ok())
        token = ct.mint_with(signer, SA, "synthetic-uid-1", NOW)
        self.assertEqual(len(token.split(".")), 3)


class CustomTokenExchange(unittest.TestCase):
    def client(self, answer, approval=None):
        server = FakeFirebase({CUSTOM: answer})
        config = guard.load_config(ALLOWED, S.synthetic_api_key(), env={})
        return rc.FirebaseRestClient(config, lt.LiveTransport(approval or approval_for_signer(), server.factory)), server

    def custom(self):
        return guard.Secret(ct.mint_with(type("Fake", (ct.Signer,), {"sign": lambda self, d: b"\x01" * 256})(), SA, "synthetic-uid-1", NOW))

    def ok(self, **extra):
        return (200, json.dumps({"kind": "identitytoolkit#VerifyCustomTokenResponse", "idToken": "i" * 40, "refreshToken": "r" * 40, "expiresIn": "3600", **extra}).encode())

    def test_success_reports_whether_an_account_was_created(self):
        for extra, expected in (({"isNewUser": False}, False), ({"isNewUser": True}, True), ({}, None)):
            client, _ = self.client(self.ok(**extra))
            result = client.sign_in_with_custom_token(self.custom())
            self.assertEqual((result.outcome, result.tokens.is_new_user, result.tokens.uid), (rc.OK, expected, None))

    def test_bad_inputs_and_answers(self):
        client, server = self.client(self.ok())
        for bad in (None, "", "x" * 10, "no.dots", "a.b", "a.b.c.d", "a b.c.d", guard.Secret("short"), 5):
            with self.assertRaises(rc.InputRejected):
                client.sign_in_with_custom_token(bad)
        self.assertEqual(server.connections, [])
        for body in ({}, {"idToken": "x"}, {"idToken": "i" * 40, "refreshToken": "r" * 40, "expiresIn": "abc"},
                     {"idToken": "i" * 40, "refreshToken": "r" * 40, "expiresIn": "3600", "isNewUser": "yes"}):
            client, _ = self.client((200, json.dumps(body).encode()))
            self.assertEqual(client.sign_in_with_custom_token(self.custom()).outcome, rc.BAD_RESPONSE)

    def test_error_codes_are_classified_and_sanitised(self):
        for code, outcome in (("INVALID_CUSTOM_TOKEN", rc.TOKEN_INVALID), ("CREDENTIAL_MISMATCH", rc.TOKEN_INVALID), ("USER_DISABLED", rc.USER_DISABLED),
                              ("TOO_MANY_ATTEMPTS_TRY_LATER", rc.THROTTLED), ("API_KEY_INVALID", rc.SERVICE_ERROR), ("OPERATION_NOT_ALLOWED", rc.SERVICE_ERROR)):
            client, _ = self.client((400, json.dumps({"error": {"code": 400, "message": code + " : secret text"}}).encode()))
            d = client.sign_in_with_custom_token(self.custom()).detail()
            self.assertEqual((d["outcome"], d["http_status"]), (outcome, 400), code)
            self.assertIn(d["error_code"], rc.KNOWN_ERROR_CODES | {"unrecognized"})
            self.assertNotIn("secret", json.dumps(d))

    def test_redirects_are_rejected(self):
        client, _ = self.client((302, b""))
        self.assertEqual(client.sign_in_with_custom_token(self.custom()).outcome, rc.REDIRECT_REJECTED)

    def test_the_exchange_is_impossible_without_a_phase5_approval(self):
        server = FakeFirebase({CUSTOM: self.ok()})
        config = guard.load_config(ALLOWED, S.synthetic_api_key(), env={})
        for approval in (guard.grant_live_approval(ALLOWED, ALLOWED, True), guard.grant_live_approval(ALLOWED, ALLOWED, True, guard.ERROR_TEST_BUDGET)):
            client = rc.FirebaseRestClient(config, lt.LiveTransport(approval, server.factory))
            with self.assertRaises(guard.LiveModeDisabled):
                client.sign_in_with_custom_token(self.custom())
        self.assertEqual(server.connections, [])

    def test_the_exchange_is_single_use(self):
        client, server = self.client(self.ok())
        client.sign_in_with_custom_token(self.custom())
        with self.assertRaises(guard.LiveModeDisabled):
            client.sign_in_with_custom_token(self.custom())
        self.assertEqual(len(server.connections), 1)


class DisabledRunner(unittest.TestCase):
    def setUp(self):
        runner._RAN.clear()
        self.lines, self.secret_prompts, self.confirmations = [], [], []

    def go(self, **overrides):
        server = overrides.pop("server", FakeFirebase({}))
        values = iter(overrides.pop("values", ["g3-user@example.invalid", S.synthetic_password(), S.synthetic_api_key(), "synthetic-uid-1", SA, bearer_token()]))
        replies = iter(overrides.pop("replies", ["", ALLOWED]))
        args = dict(interactive=True, getpass_fn=lambda p: self.secret_prompts.append(p) or next(values), input_fn=lambda p: self.confirmations.append(p) or next(replies),
                    out=self.lines.append, transport_factory=lambda a: lt.LiveTransport(a, server.factory), clock=lambda: NOW, env={})
        args.update(overrides)
        self.server = server
        return runner.run(**args)

    def test_it_is_disabled_by_default_and_asks_for_no_secret(self):
        self.assertIs(guard.CUSTOM_TOKEN_LIVE_APPROVED, False)
        for reply in ("", "yes", "enable one five-request run", runner.ENABLING_PHRASE + " ", "ENABLE"):
            runner._RAN.clear()
            self.assertEqual(self.go(replies=[reply, ALLOWED]), 4, repr(reply))
            self.assertEqual((self.secret_prompts, self.server.connections), ([], []))
        self.assertIn("not enabled for this run", "\n".join(self.lines))

    def test_cancelling_the_enabling_prompt_with_eof_or_interrupt_is_also_disabled(self):
        for exc in (EOFError(), KeyboardInterrupt()):
            runner._RAN.clear()

            def refuse(prompt, exc=exc):
                raise exc
            self.assertEqual(self.go(input_fn=refuse), 4)
            self.assertEqual((self.secret_prompts, self.server.connections), ([], []))

    def test_not_interactive_environment_conflicts_and_reruns_are_refused_first(self):
        self.assertEqual(self.go(interactive=False), 2)
        runner._RAN.clear()
        self.assertEqual(self.go(env={"GOOGLE_APPLICATION_CREDENTIALS": "x"}), 2)
        runner._RAN.clear()
        self.assertEqual(self.go(), 4)
        self.assertEqual(self.go(), 2)
        self.assertEqual((self.secret_prompts, self.server.connections), ([], []))

    def test_the_real_entry_point_refuses_without_a_terminal(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(runner.main(), 2)                                  # stdin is not a terminal under the test runner
        self.assertIn("interactive terminal", out.getvalue())


class FiveRequestRunner(unittest.TestCase):
    """The runner's whole flow, enabled the way the real run is enabled (the typed phrase), against a fake Google/Firebase."""

    def setUp(self):
        runner._RAN.clear()
        self.lines = []
        self.uid, self.email, self.password = "synthetic-uid-1", "g3-user@example.invalid", S.synthetic_password()
        self.api_key, self.bearer = S.synthetic_api_key(), bearer_token()
        self.rt1, self.rt2 = "RT-" + S.synthetic_password(), "RT2-" + S.synthetic_password()

    PASSWORD_CLAIMS = dict(firebase={"identities": {}, "sign_in_provider": "password"})
    CUSTOM_CLAIMS = dict(firebase={"identities": {}, "sign_in_provider": "custom"}, email=..., email_verified=...)

    def answers(self, mutate=None):
        a = {
            SIGN_IN: (200, json.dumps({"idToken": sign_rs256(KEY, HEADER, **self.PASSWORD_CLAIMS), "refreshToken": self.rt1, "expiresIn": "3600",
                                       "localId": self.uid, "email": self.email}).encode()),
            CERTS: (200, json.dumps({KID: CERT}).encode()),
            SIGN_BLOB: sign_blob_ok(),
            CUSTOM: (200, json.dumps({"idToken": sign_rs256(KEY, HEADER, **self.CUSTOM_CLAIMS), "refreshToken": self.rt1, "expiresIn": "3600",
                                      "isNewUser": False}).encode()),
            REFRESH: (200, json.dumps({"id_token": sign_rs256(KEY, HEADER, iat=NOW - 30, exp=NOW + 3570, **self.CUSTOM_CLAIMS), "refresh_token": self.rt2,
                                       "expires_in": "3600", "user_id": self.uid}).encode()),
        }
        if mutate:
            mutate(a)
        return a

    def go(self, answers=None, replies=None, values=None, interactive=True, env=None):
        server = FakeFirebase(answers or self.answers())
        it = iter(values or [self.email, self.password, self.api_key, self.uid, SA, self.bearer])
        rep = iter(replies or [runner.ENABLING_PHRASE, ALLOWED])

        def confirm(prompt):
            value = next(rep)
            if isinstance(value, BaseException):
                raise value
            return value
        code = runner.run(interactive=interactive, getpass_fn=lambda p: next(it), input_fn=confirm, out=self.lines.append,
                          transport_factory=lambda a: lt.LiveTransport(a, server.factory), clock=lambda: NOW, env={} if env is None else env)
        self.server = server
        return code

    def text(self):
        return "\n".join(self.lines)

    def hosts(self):
        return [(c.sent[0], c.host) for c in self.server.connections]

    FULL = [("POST", "identitytoolkit.googleapis.com"), ("GET", "www.googleapis.com"), ("POST", "iamcredentials.googleapis.com"),
            ("POST", "identitytoolkit.googleapis.com"), ("POST", "securetoken.googleapis.com")]

    # ---- the happy path -----------------------------------------------------------------------------------------------------
    def test_full_flow_is_five_requests_in_the_fixed_order_and_reports_only_booleans(self):
        code = self.go()
        out = self.text()
        self.assertEqual(code, 0, out)
        self.assertEqual(self.hosts(), self.FULL)
        for needle in ("1 password sign-in: outcome=ok", "2 certificates: outcome=ok", "verified_login_uid_matches_typed_uid=True", "response_uid_matches_verified_sub=True",
                       "login token: signature_verified=True", "sign_in_provider=password", "claim_email_matches_input=True", "3 signBlob: ok key_id_returned=True",
                       "4 custom-token sign-in: outcome=ok", "is_new_user=False", "custom-token token: signature_verified=True", "sign_in_provider=custom",
                       "email_claim_present=False", "email_verified=absent", "g6_helper_needed=True", "compare login-vs-custom:", "email_claim_presence_unchanged=False",
                       "5 refresh: outcome=ok", "refreshed token: signature_verified=True uid_unchanged=True", "compare custom-vs-refreshed:", "auth_time_preserved=True",
                       "requests used: 5 of 5"):
            self.assertIn(needle, out)

    def test_nothing_secret_is_printed_and_credentials_go_only_where_they_belong(self):
        self.go()
        out = self.text()
        for secret in (self.uid, self.email, self.password, self.api_key, self.bearer, SA, self.rt1, self.rt2, KID, CERT[:50]):
            self.assertNotIn(secret, out)
        bodies = [c.sent[2].decode() if isinstance(c.sent[2], bytes) else "" for c in self.server.connections]
        self.assertIn(self.password, bodies[0])
        self.assertTrue(all(self.password not in b for b in bodies[1:]))
        self.assertIn("Authorization", self.server.connections[2].sent[3])
        self.assertTrue(all("Authorization" not in c.sent[3] for i, c in enumerate(self.server.connections) if i != 2))
        self.assertNotIn(self.bearer, " ".join(bodies))

    def test_the_custom_token_is_signed_for_the_verified_uid_and_carries_no_email_claim(self):
        self.go()
        payload = json.loads(base64.urlsafe_b64decode(json.loads(self.server.connections[2].sent[2])["payload"].encode()).decode().split(".")[1] + "==") if False else None
        data = base64.b64decode(json.loads(self.server.connections[2].sent[2])["payload"])
        claims = json.loads(base64.urlsafe_b64decode(data.decode().split(".")[1] + "=" * (-len(data.decode().split(".")[1]) % 4)))
        self.assertEqual((claims["uid"], claims["iss"], claims["sub"], claims["aud"]), (self.uid, SA, SA, ct.AUDIENCE))
        self.assertFalse({"email", "email_verified", "claims"} & set(claims))

    # ---- UID safety -----------------------------------------------------------------------------------------------------------
    def test_a_uid_mismatch_stops_before_anything_is_signed_or_exchanged(self):
        code = self.go(values=[self.email, self.password, self.api_key, "some-other-uid", SA, self.bearer])
        self.assertEqual(code, 1)
        self.assertEqual(self.hosts(), self.FULL[:2])
        out = self.text()
        self.assertIn("verified_login_uid_matches_typed_uid=False", out)
        self.assertIn("NO custom token is signed or exchanged", out)
        self.assertIn("requests used: 2 of 5", out)
        self.assertNotIn("iamcredentials", " ".join(h for _, h in self.hosts()))

    def test_a_login_response_uid_that_disagrees_with_the_verified_subject_stops(self):
        def lying(a):
            body = json.loads(a[SIGN_IN][1])
            body["localId"] = "a-different-uid"
            a[SIGN_IN] = (200, json.dumps(body).encode())
        self.assertEqual(self.go(self.answers(lying)), 1)
        self.assertEqual(len(self.server.connections), 2)
        self.assertIn("response_uid_matches_verified_sub=False", self.text())

    def test_an_invalid_or_unverifiable_login_token_never_establishes_the_uid(self):
        other, _, _ = make_pki()
        cases = (("bad signature", sign_rs256(other, HEADER, **self.PASSWORD_CLAIMS)), ("wrong issuer", sign_rs256(KEY, HEADER, iss="https://securetoken.google.com/epicverse-prod", **self.PASSWORD_CLAIMS)),
                 ("wrong audience", sign_rs256(KEY, HEADER, aud="epicverse-prod", **self.PASSWORD_CLAIMS)),
                 ("expired", sign_rs256(KEY, HEADER, iat=NOW - 9000, exp=NOW - 10, auth_time=NOW - 9100, **self.PASSWORD_CLAIMS)),
                 ("bad auth_time", sign_rs256(KEY, HEADER, auth_time="yesterday", **self.PASSWORD_CLAIMS)),
                 ("unknown kid", sign_rs256(KEY, {"alg": "RS256", "kid": "unknown-kid", "typ": "JWT"}, **self.PASSWORD_CLAIMS)),
                 ("algorithm none", "e30.e30."), ("garbage", "not-a-token"))
        for label, token in cases:
            runner._RAN.clear()
            self.lines.clear()

            def bad(a, token=token):
                body = json.loads(a[SIGN_IN][1])
                body["idToken"] = token
                a[SIGN_IN] = (200, json.dumps(body).encode())
            self.assertEqual(self.go(self.answers(bad)), 1, label)
            self.assertEqual(self.hosts(), self.FULL[:2], label)
            self.assertIn("the uid is NOT established", self.text(), label)
            self.assertNotIn("3 signBlob", self.text(), label)

    def test_signing_cannot_precede_the_uid_verification_even_by_misuse(self):
        a = guard.grant_custom_token_approval(ALLOWED, ALLOWED, True, SA, enabled_for_this_run=True)
        with self.assertRaises(guard.LiveModeDisabled):
            a.consume(*SIGN_BLOB)                                   # signBlob first: out of order
        a.consume(*SIGN_IN)
        with self.assertRaises(guard.LiveModeDisabled):
            a.consume(*SIGN_BLOB)                                   # signBlob before the certificates / uid verification
        with self.assertRaises(guard.LiveModeDisabled):
            a.consume(*CUSTOM)

    def test_missing_or_failing_login_and_certificates_fail_closed(self):
        for label, change, requests, needle in (
                ("wrong password", {SIGN_IN: (400, json.dumps({"error": {"code": 400, "message": "INVALID_LOGIN_CREDENTIALS"}}).encode())}, 1, "1 failed: nothing else"),
                ("certs 503", {CERTS: (503, b"{}")}, 2, "uid is NOT established"),
                ("certs junk", {CERTS: (200, b"junk")}, 2, "uid is NOT established"),
                ("certs bad pem", {CERTS: (200, json.dumps({KID: "garbage"}).encode())}, 2, "verification FAILED"),
                ("certs redirect", {CERTS: (302, b"")}, 2, "uid is NOT established"),
                ("certs wrong kid", {CERTS: (200, json.dumps({"other-kid": CERT}).encode())}, 2, "kid_unknown")):
            runner._RAN.clear()
            self.lines.clear()
            self.assertEqual(self.go(self.answers(lambda a, change=change: a.update(change))), 1, label)
            self.assertEqual(len(self.server.connections), requests, label)
            self.assertIn(needle, self.text(), label)

    # ---- signing and exchange ----------------------------------------------------------------------------------------------------
    def test_iam_failures_stop_without_changing_anything(self):
        for answer, category in ((google_error(403, "PERMISSION_DENIED", "SERVICE_DISABLED"), "service_disabled"), (google_error(403, "PERMISSION_DENIED"), "permission_denied"),
                                 (google_error(401, "UNAUTHENTICATED"), "unauthenticated"), (google_error(404, "NOT_FOUND"), "not_found")):
            runner._RAN.clear()
            self.lines.clear()
            self.assertEqual(self.go(self.answers(lambda a, answer=answer: a.update({SIGN_BLOB: answer}))), 1)
            self.assertEqual(len(self.server.connections), 3)
            self.assertIn("category=" + category, self.text())
            self.assertIn("remaining requests are skipped", self.text())

    def test_unexpected_custom_token_responses_stop_before_refresh(self):
        for label, answer in (("rejected", (400, json.dumps({"error": {"code": 400, "message": "INVALID_CUSTOM_TOKEN"}}).encode())),
                              ("missing fields", (200, json.dumps({"idToken": "x"}).encode())), ("junk", (200, b"junk")), ("redirect", (302, b"")),
                              ("bad flag", (200, json.dumps({"idToken": "i" * 40, "refreshToken": "r" * 40, "expiresIn": "3600", "isNewUser": "no"}).encode()))):
            runner._RAN.clear()
            self.lines.clear()
            self.assertEqual(self.go(self.answers(lambda a, answer=answer: a.update({CUSTOM: answer}))), 1, label)
            self.assertEqual(len(self.server.connections), 4, label)
            self.assertNotIn("5 refresh", self.text(), label)

    def test_is_new_user_true_is_a_critical_failure_and_stops_before_refresh(self):
        def created(a):
            body = json.loads(a[CUSTOM][1])
            body["isNewUser"] = True
            a[CUSTOM] = (200, json.dumps(body).encode())
        self.assertEqual(self.go(self.answers(created)), 1)
        self.assertEqual(len(self.server.connections), 4)
        out = self.text()
        self.assertIn("CRITICAL TEST-DATA SAFETY FAILURE", out)
        self.assertNotIn("5 refresh", out)
        self.assertIn("requests used: 4 of 5", out)

    def test_a_custom_token_session_for_another_uid_or_with_a_bad_signature_stops_before_refresh(self):
        other, _, _ = make_pki()
        for label, token, needle in (("other uid", sign_rs256(KEY, HEADER, sub="another-uid", user_id="another-uid", **self.CUSTOM_CLAIMS), "different uid"),
                                     ("bad signature", sign_rs256(other, HEADER, **self.CUSTOM_CLAIMS), "verification FAILED")):
            runner._RAN.clear()
            self.lines.clear()

            def bad(a, token=token):
                body = json.loads(a[CUSTOM][1])
                body["idToken"] = token
                a[CUSTOM] = (200, json.dumps(body).encode())
            self.assertEqual(self.go(self.answers(bad)), 1, label)
            self.assertEqual(len(self.server.connections), 4, label)
            self.assertIn(needle, self.text(), label)
            self.assertNotIn("5 refresh", self.text(), label)

    def test_refresh_failures_are_reported_after_five_requests(self):
        other, _, _ = make_pki()
        for label, answer in (("rejected", (400, json.dumps({"error": {"code": 400, "message": "INVALID_REFRESH_TOKEN"}}).encode())),
                              ("bad token", (200, json.dumps({"id_token": sign_rs256(other, HEADER, **self.CUSTOM_CLAIMS), "refresh_token": "r" * 40, "expires_in": "3600",
                                                              "user_id": self.uid}).encode()))):
            runner._RAN.clear()
            self.lines.clear()
            self.assertEqual(self.go(self.answers(lambda a, answer=answer: a.update({REFRESH: answer}))), 1, label)
            self.assertEqual(len(self.server.connections), 5, label)

    def test_a_missing_email_claim_in_the_custom_session_is_reported_not_assumed(self):
        def with_email(a):
            claims = dict(self.CUSTOM_CLAIMS, email=self.email, email_verified=True)
            body = json.loads(a[CUSTOM][1])
            body["idToken"] = sign_rs256(KEY, HEADER, **claims)
            a[CUSTOM] = (200, json.dumps(body).encode())
        self.go(self.answers(with_email))
        out = self.text()
        self.assertIn("custom-token token: claim_email_matches_input=True", out)
        self.assertIn("email_verified=true", out)
        self.assertIn("g6_helper_needed=False", out)

    # ---- budget, order and limits ---------------------------------------------------------------------------------------------------
    def test_the_five_request_budget_and_order_are_enforced(self):
        a = guard.grant_custom_token_approval(ALLOWED, ALLOWED, True, SA, enabled_for_this_run=True)
        for pair in (SIGN_IN, CERTS, SIGN_BLOB, CUSTOM, REFRESH):
            a.consume(*pair)
        self.assertEqual(a.used, 5)
        for pair in (SIGN_IN, CERTS, SIGN_BLOB, CUSTOM, REFRESH, ("POST", guard.SIGN_IN_URL)):
            with self.assertRaises(guard.LiveModeDisabled):
                a.consume(*pair)
        self.assertEqual(a.used, 5)
        b = guard.grant_custom_token_approval(ALLOWED, ALLOWED, True, SA, enabled_for_this_run=True)
        with self.assertRaises(guard.LiveModeDisabled) as raised:
            b.consume(*CERTS)
        self.assertEqual(raised.exception.reason, "request_out_of_order")
        with self.assertRaises(guard.GuardError):
            b.consume("POST", "https://oauth2.googleapis.com/token")
        self.assertEqual(b.used, 0)

    def test_an_order_must_match_the_budget_exactly(self):
        pairs = {SIGN_IN: 1, CERTS: 1}
        for order in ((SIGN_IN,), (SIGN_IN, SIGN_IN), (SIGN_IN, CERTS, CERTS), (CERTS, REFRESH)):
            with self.assertRaises(guard.LiveModeDisabled):
                guard.grant_live_approval(ALLOWED, ALLOWED, True, pairs, order)
        with self.assertRaises(guard.LiveModeDisabled):
            guard.grant_live_approval(ALLOWED, ALLOWED, True, {SIGN_IN: 2}, (SIGN_IN, SIGN_IN))

    # ---- cancellation and bad input ---------------------------------------------------------------------------------------------------
    def test_cancellation_sends_nothing(self):
        for replies in ([runner.ENABLING_PHRASE, ""], [runner.ENABLING_PHRASE, "no"], [runner.ENABLING_PHRASE, ALLOWED.upper()],
                        [runner.ENABLING_PHRASE, EOFError()], [runner.ENABLING_PHRASE, KeyboardInterrupt()]):
            runner._RAN.clear()
            self.lines.clear()
            self.assertEqual(self.go(replies=replies), 3, repr(replies[1]))
            self.assertEqual(self.server.connections, [])

    def test_bad_input_is_rejected_before_any_confirmation_or_request(self):
        good = [self.email, self.password, self.api_key, self.uid, SA, self.bearer]
        for index, bad in ((0, "no-at-sign"), (1, ""), (2, "not-a-key"), (3, ""), (3, "x" * 200), (3, "has space"), (4, "g3@epicverse-prod.iam.gserviceaccount.com"),
                           (4, "not-an-account"), (5, "short")):
            runner._RAN.clear()
            values = list(good)
            values[index] = bad
            self.assertEqual(self.go(values=values), 2, (index, bad[:10]))
            self.assertEqual(self.server.connections, [])

    def test_environment_conflicts_stop_everything_first(self):
        for env in ({"GOOGLE_APPLICATION_CREDENTIALS": "x"}, {"GOOGLE_CLOUD_PROJECT": "epicverse-prod"}, {"FIREBASE_CONFIG": "{}"}):
            runner._RAN.clear()
            self.assertEqual(self.go(env=env), 2)
            self.assertEqual(self.server.connections, [])

    def test_a_second_run_in_one_process_is_refused(self):
        self.assertEqual(self.go(), 0)
        self.assertEqual(self.go(), 2)

    def test_unexpected_exceptions_are_reported_without_details(self):
        server = FakeFirebase(self.answers())
        server.factory = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("SECRET-detail"))
        it, rep = iter([self.email, self.password, self.api_key, self.uid, SA, self.bearer]), iter([runner.ENABLING_PHRASE, ALLOWED])
        code = runner.run(interactive=True, getpass_fn=lambda p: next(it), input_fn=lambda p: next(rep), out=self.lines.append,
                          transport_factory=lambda a: lt.LiveTransport(a, server.factory), clock=lambda: NOW, env={})
        self.assertEqual(code, 1)
        self.assertNotIn("SECRET-detail", self.text())

    def test_the_runner_source_has_no_key_adc_or_gcloud_fallbacks(self):
        text = __import__("pathlib").Path(runner.__file__).read_text(encoding="utf-8")
        for word in ("private_key", "load_pem_private_key", "ApplicationDefault", "gcloud", "firebase_admin", "service_account.Credentials", "GOOGLE_APPLICATION_CREDENTIALS"):
            self.assertNotIn(word, text)


class NoNetwork(unittest.TestCase):
    def test_no_socket_attempt(self):
        self.assertEqual(S.ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
