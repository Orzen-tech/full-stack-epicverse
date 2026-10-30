"""FINAL OFFLINE READINESS CHECK of Stage B: the real runner (`g8_live_run.main stage-b`) -> real preflight, hooks, G7 adapter, firebase_admin 6.4.0, G3 client,
G8 protection and stage script, over a STATEFUL fake Firebase served at the three lowest layers (urllib3 `_make_request`, httpx transport, http.client connection),
with the network-denial layer active. The Stage B approval is patched True ONLY inside these tests and restored afterwards. Each full run takes about 8 s of real
time because the two safety waits (before the revoke, and before the fresh sign-in) are real."""
import asyncio
import base64
import json
import os
import re
import time
import unittest
import urllib.parse

import httpx
import urllib3
from unittest import mock

import _g8 as t
from _tree import ATTEMPTS

import g8_guard as guard
import g8_live_run as runner
import test_stage_a_wiring as wa
from test_stage_a_wiring import ADMIN_TOKEN, CERT_PEM, DUMMY_EMAIL, DUMMY_PASSWORD, DUMMY_UID, KEY, KID, SIGNER_TOKEN, b64

import io as _io

API_KEY = "AIza" + "B" * 35
PAD = lambda s: s + "=" * (-len(s) % 4)


class Backend:
    """A small stateful Firebase: password sign-in, Admin create/update/lookup, custom-token exchange, refresh, signBlob, certificates."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self.log, self.methods, self.bodies, self.events = [], [], [], []
        self.accounts = {DUMMY_UID: {"email": DUMMY_EMAIL, "password": DUMMY_PASSWORD, "disabled": False, "valid_since": 0}}
        self.dummy_before = dict(self.accounts[DUMMY_UID])
        self.refresh, self.created_password, self._n = {}, None, 0

    # ---- helpers -----------------------------------------------------------------------------------------------------------
    def uid_by_email(self, email):
        return next((u for u, a in self.accounts.items() if a["email"].lower() == (email or "").lower()), None)

    def token(self, uid, provider):
        now = int(time.time())
        claims = {"iss": "https://securetoken.google.com/" + t.PROJECT, "aud": t.PROJECT, "sub": uid, "user_id": uid, "iat": now, "exp": now + 3600,
                  "auth_time": now, "firebase": {"sign_in_provider": provider, "identities": {}}}
        if provider == "password":
            claims.update(email=self.accounts[uid]["email"], email_verified=False)
        signing = b64(json.dumps({"alg": "RS256", "kid": KID, "typ": "JWT"}).encode()) + "." + b64(json.dumps(claims).encode())
        return signing + "." + b64(KEY.sign(signing.encode(), wa.padding.PKCS1v15(), wa.hashes.SHA256())), now

    def session(self, uid, provider):
        token, iat = self.token(uid, provider)
        self._n += 1
        refresh = "AMf-%036d" % self._n
        self.refresh[refresh] = (uid, iat)
        return token, refresh

    def override(self, name):
        hook = self.overrides.get(name)
        return hook() if hook else None

    # ---- the whole API --------------------------------------------------------------------------------------------------------
    def handle(self, host, method, path, body):
        self.log.append((host, path))
        self.methods.append(method)
        self.bodies.append(body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else "")
        project = "/v1/projects/%s" % t.PROJECT
        doc = {}
        if path.endswith("x509/securetoken@system.gserviceaccount.com"):
            return self.override("certs") or (200, {KID: CERT_PEM})
        if path.endswith(":signBlob"):
            return self.override("sign_blob") or (200, {"signedBlob": base64.b64encode(os.urandom(256)).decode()})
        if path == "/v1/token":
            form = urllib.parse.parse_qs(body.decode())
            record = self.refresh.get(form.get("refresh_token", [""])[0])
            account = self.accounts.get(record[0]) if record else None
            if account is None:
                return 400, {"error": {"message": "INVALID_REFRESH_TOKEN"}}
            if account["disabled"]:
                return 400, {"error": {"message": "USER_DISABLED"}}
            if record[1] < account["valid_since"]:
                return 400, {"error": {"message": "TOKEN_EXPIRED"}}
            token, refresh = self.session(record[0], "password")
            return 200, {"id_token": token, "refresh_token": refresh, "expires_in": "3600", "user_id": record[0]}
        document = json.loads(body) if body else {}
        if path == "/v1/accounts:signInWithPassword":
            uid = self.uid_by_email(document.get("email"))
            account = self.accounts.get(uid)
            if account is None or account["password"] != document.get("password"):
                return 400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}}
            if account["disabled"]:
                return 400, {"error": {"message": "USER_DISABLED"}}
            token, refresh = self.session(uid, "password")
            return 200, {"localId": uid, "email": account["email"], "idToken": token, "refreshToken": refresh, "expiresIn": "3600"}
        if path == "/v1/accounts:signInWithCustomToken":
            uid = json.loads(base64.urlsafe_b64decode(PAD(document["token"].split(".")[1])))["uid"]
            new = uid not in self.accounts
            if new:
                self.accounts[uid] = {"email": uid + "@created.invalid", "password": "x", "disabled": False, "valid_since": 0}
                self.events.append("exchange_created")
            if self.accounts[uid]["disabled"]:
                return 400, {"error": {"message": "USER_DISABLED"}}
            token, refresh = self.session(uid, "custom")
            return 200, {"idToken": token, "refreshToken": refresh, "expiresIn": "3600", "isNewUser": new}
        if path == project + "/accounts:lookup":
            override = self.override("lookup")
            if override:
                return override
            wanted = document.get("localId") or []
            uids = [u for u in wanted if u in self.accounts] if wanted else [u for u in (self.uid_by_email(e) for e in document.get("email", [])) if u]
            users = [{"localId": u, "email": self.accounts[u]["email"], "disabled": self.accounts[u]["disabled"],
                      "validSince": str(self.accounts[u]["valid_since"]), "emailVerified": False} for u in uids]
            return 200, ({"users": users} if users else {"kind": "identitytoolkit#GetAccountInfoResponse"})
        if path == project + "/accounts":
            override = self.override("create")
            if override:
                return override
            if self.uid_by_email(document.get("email")):
                self.events.append("duplicate_create_refused")
                return 400, {"error": {"message": "EMAIL_EXISTS"}}
            if len(document.get("password", "")) < 6:
                return 400, {"error": {"message": "WEAK_PASSWORD"}}
            uid = "gen%020d" % (len(self.accounts) + 1)
            self.accounts[uid] = {"email": document["email"], "password": document["password"], "disabled": False, "valid_since": 0}
            self.created_password = document["password"]
            self.events.append("create")
            return 200, {"localId": uid}
        if path == project + "/accounts:update":
            uid = document["localId"]
            account = self.accounts[uid]
            if "validSince" in document:
                override = self.override("revoke")
                if override:
                    return override
                account["valid_since"] = int(document["validSince"])
                self.events.append("revoke")
            elif "password" in document:
                account["password"] = document["password"]
                self.events.append("password")
            elif "disableUser" in document:
                account["disabled"] = bool(document["disableUser"])
                self.events.append("disable" if document["disableUser"] else "enable")
            return 200, {"localId": uid}
        if path.endswith("accounts:delete"):
            self.events.append("delete")
            raise AssertionError("Stage B must never delete")
        raise AssertionError("unexpected request " + path)

    # ---- the three layers ----------------------------------------------------------------------------------------------------------
    def wire(self, pool, conn, method, url, *args, **kwargs):
        status, document = self.handle(pool.host, method, url.split("?")[0], kwargs.get("body") or b"")
        return urllib3.response.HTTPResponse(body=_io.BytesIO(json.dumps(document).encode()), headers={"Content-Type": "application/json", "Cache-Control": "no-store"},
                                             status=status, preload_content=False, request_method=method)

    def httpx_inner(self):
        backend = self

        class Inner(httpx.AsyncBaseTransport):
            def __init__(self, *a, **k):
                pass

            async def handle_async_request(self, request):
                status, document = backend.handle(request.url.host, request.method, request.url.path, request.content)
                return httpx.Response(status, json=document, request=request)

            async def aclose(self):
                pass
        return Inner

    def connection(self):
        backend = self

        class Connection:
            def __init__(self, host, port, timeout=None, context=None):
                self.host, self.sock = host, __import__("unittest.mock").mock.Mock()

            def connect(self):
                pass

            def request(self, method, target, body=None, headers=None):
                self.method, self.target, self.body = method, target, body or b""

            def getresponse(self):
                status, document = backend.handle(self.host, self.method, self.target.split("?")[0], self.body)
                payload = json.dumps(document).encode()
                from unittest import mock as m
                return m.Mock(status=status, read=m.Mock(side_effect=[payload, b""]))

            def close(self):
                pass
        return Connection

    # ---- views -----------------------------------------------------------------------------------------------------------------------
    def disposable(self):
        return [(u, a) for u, a in self.accounts.items() if u != DUMMY_UID and a["email"].startswith("g8-")]


class HarnessB(wa.Harness):
    mode, approve = "stage-b", ("B",)

    def __init__(self, backend=None, visible=None, hidden=None, env=None):
        self.backend = backend or Backend()
        P, M = guard.ALLOWED_PROJECT_ID, runner.MUTATION_PHRASES
        super().__init__(self.backend, hidden=hidden if hidden is not None else [API_KEY, SIGNER_TOKEN, ADMIN_TOKEN],
                         visible=visible if visible is not None else [str(t._tree.tree_dir()), P, runner.PHRASES["B"], M["create"], M["password"], M["revoke"], M["disable"], t.SIGNER],
                         env=env)


PLANNED_COUNTS = "requests total=34 certs=5 create=2 custom_exchange=2 lookup=10 refresh=2 sign_blob=2 sign_in=7 update=4"


class HappyPath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = HarnessB().run()
        cls.b = cls.h.backend
        cls.text = "\n".join(cls.h.out)

    def test_clean_run_all_25_checks_pass(self):
        self.assertIsNone(self.h.error)
        self.assertEqual(self.h.code, 0, self.text)
        self.assertEqual([line.split()[1] for line in self.h.out[0].splitlines()[1:26]], ["pass"] * 25)
        self.assertTrue(self.h.out[0].startswith("stage=B clean=True stop=-"))

    def test_request_accounting_matches_the_stage_b_budget_exactly_including_sdk_readbacks(self):
        self.assertIn(PLANNED_COUNTS, self.text)
        self.assertEqual(len(self.b.log), 34)
        self.assertEqual(guard.STAGE_QUOTAS["B"], {"create": 2, "sign_in": 7, "lookup": 10, "update": 4, "refresh": 2, "sign_blob": 2, "custom_exchange": 2, "certs": 5})
        self.assertLessEqual(sum(sum(q.values()) for q in guard.STAGE_QUOTAS.values()), guard.GLOBAL_REQUEST_CAP)

    def test_exactly_the_planned_mutations_happened_in_order_and_nothing_was_deleted(self):
        self.assertEqual(self.b.events, ["create", "duplicate_create_refused", "password", "revoke", "disable", "enable"])
        self.assertNotIn("delete", self.b.events)
        self.assertNotIn("exchange_created", self.b.events)

    def test_the_existing_dummy_account_was_never_touched_or_named(self):
        self.assertEqual(self.b.accounts[DUMMY_UID], self.b.dummy_before)
        joined = "\n".join(self.b.bodies)
        for needle in (DUMMY_UID, DUMMY_EMAIL, DUMMY_PASSWORD):
            self.assertNotIn(needle, joined)

    def test_one_disposable_account_exists_afterwards_enabled_with_the_new_password_and_a_revocation_time(self):
        accounts = self.b.disposable()
        self.assertEqual(len(accounts), 1)
        uid, account = accounts[0]
        self.assertRegex(account["email"], r"^g8-[0-9a-f]{16}@example\.com$")
        self.assertFalse(account["disabled"])
        self.assertNotEqual(account["password"], self.b.created_password)
        self.assertGreater(account["valid_since"], 0)

    def test_the_cleanup_handle_is_reported_and_names_the_account_that_exists(self):
        handle = re.search(r"cleanup_pending handle=(g8-[0-9a-f]{16})$", self.h.out[0], re.M).group(1)
        self.assertEqual(self.b.disposable()[0][1]["email"], handle + "@example.com")

    def test_four_separate_mutation_confirmations_and_three_hidden_prompts_only(self):
        self.assertEqual(len(self.h.hidden_asked), 3)
        self.assertEqual(sum(1 for p in self.h.visible_asked if p.startswith("Type '")), 5)
        self.assertFalse(any("dummy" in p.lower() for p in self.h.hidden_asked + self.h.visible_asked))

    def test_no_secret_identifier_or_token_is_printed(self):
        uid, account = self.b.disposable()[0]
        for needle in (uid, account["email"], account["password"], self.b.created_password, DUMMY_UID, DUMMY_EMAIL, SIGNER_TOKEN, ADMIN_TOKEN, "AIza", "eyJ", "AMf-"):
            self.assertNotIn(needle, self.text)

    def test_process_state_is_clean_and_the_network_was_never_touched(self):
        self.assertIsNone(guard._installed["urllib3"])
        self.assertIsNone(guard._installed["socket"])
        self.assertEqual(ATTEMPTS, [])

    def test_every_mode_is_refused_after_the_run(self):
        self.assertEqual((guard.LIVE_EXECUTION_APPROVED, guard.STAGE_APPROVED), (False, {"A": False, "B": False, "C": False}))
        out = []
        code = runner.main(["g8_live_run.py", "stage-a"], runner.IO(hidden=lambda p: 1 / 0, visible=lambda p: 1 / 0, is_tty=lambda: True, out=out.append))
        self.assertEqual((code, out), (2, ["G8 stopped: live_execution_not_approved"]))


class Interrupted(unittest.TestCase):
    def tearDown(self):
        self.assertIsNone(guard._installed["urllib3"])

    def test_a_cancel_after_the_account_exists_reports_the_cleanup_handle(self):
        def cancel():
            raise asyncio.CancelledError()
        h = HarnessB(Backend(revoke=cancel)).run()
        self.assertIsInstance(h.error, asyncio.CancelledError)
        text = "\n".join(h.out)
        self.assertIn("stop=cancelled", text)
        self.assertRegex(text, r"cleanup_pending handle=g8-[0-9a-f]{16}")
        self.assertEqual(len(h.backend.disposable()), 1)
        self.assertEqual(h.backend.accounts[DUMMY_UID], h.backend.dummy_before)

    def test_a_keyboard_interrupt_is_handled_the_same_way(self):
        def interrupt():
            raise KeyboardInterrupt()
        h = HarnessB(Backend(revoke=interrupt)).run()
        self.assertIsInstance(h.error, KeyboardInterrupt)
        self.assertRegex("\n".join(h.out), r"cleanup_pending handle=g8-[0-9a-f]{16}")

    def test_a_create_whose_read_back_fails_still_reports_the_handle_of_an_account_that_exists(self):
        h = HarnessB(Backend(lookup=lambda: (503, {"error": {"message": "x"}}))).run()
        text = "\n".join(h.out)
        self.assertEqual(h.code, 1)
        self.assertIn("B01 fail", text)
        self.assertEqual(len(h.backend.disposable()), 1)
        handle = re.search(r"cleanup_pending handle=(g8-[0-9a-f]{16})", text).group(1)
        self.assertEqual(h.backend.disposable()[0][1]["email"], handle + "@example.com")
        self.assertEqual(len(h.backend.log), 1 + 1 + 5)                      # certificates + create + the SDK's own 503 retries of the read-back, all counted
        self.assertEqual(h.backend.events, ["create"])

    def test_a_create_that_the_service_refuses_leaves_no_account_but_still_prints_the_handle(self):
        h = HarnessB(Backend(create=lambda: (503, {"error": {"message": "x"}}))).run()
        text = "\n".join(h.out)
        self.assertEqual(h.code, 1)
        self.assertEqual(h.backend.disposable(), [])
        self.assertIn("cleanup_pending", text)                                   # the outcome of a refused/timed-out create is unknown to the harness: cleanup finds nothing


class FailClosed(unittest.TestCase):
    def tearDown(self):
        self.assertIsNone(guard._installed["urllib3"])

    def test_a_missing_confirmation_for_any_mutation_class_asks_for_no_secret_and_sends_nothing(self):
        P, M = guard.ALLOWED_PROJECT_ID, runner.MUTATION_PHRASES
        base = [P, runner.PHRASES["B"], M["create"], M["password"], M["revoke"], M["disable"], t.SIGNER]
        for index in (2, 3, 4, 5):
            visible = [str(t._tree.tree_dir())] + base
            visible[index + 1] = "no"
            h = HarnessB(visible=visible).run()
            self.assertEqual(h.out[-1], "G8 stopped: mutation_not_confirmed")
            self.assertEqual((h.hidden_asked, h.backend.log, h.backend.events), ([], [], []))

    def test_stage_b_is_refused_when_its_own_approval_is_off(self):
        h = HarnessB()
        h.approve = ()
        h.run()
        self.assertEqual(h.out, ["G8 stopped: live_execution_not_approved"])
        self.assertEqual((h.visible_asked, h.hidden_asked, h.backend.log), ([], [], []))

    def test_an_exchange_that_would_create_an_account_is_a_critical_stop(self):
        class Creating(Backend):
            def handle(self, host, method, path, body):
                if path == "/v1/accounts:signInWithCustomToken":
                    token, refresh = self.session(next(iter(self.accounts)), "custom")
                    self.log.append((host, path))
                    self.methods.append(method)
                    return 200, {"idToken": token, "refreshToken": refresh, "expiresIn": "3600", "isNewUser": True}
                return super().handle(host, method, path, body)
        h = HarnessB(Creating()).run()
        self.assertEqual(h.code, 1)
        self.assertIn("stop=custom_exchange_created_or_unknown_account", h.out[0])


if __name__ == "__main__":
    unittest.main()
