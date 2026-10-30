"""The REAL firebase_admin 6.4.0 SDK and the REAL G7 admin port, driven through the G8 urllib3 hook over a FAKE wire (the pool's `_make_request` is
replaced by a canned responder; no socket is opened). Proves: which requests the SDK really sends per operation (so the quotas are measured, not
assumed), that SDK-internal retries (status, connect, read) are charged one by one, that mutating requests name only the bound disposable uid, and
that SDK default timeouts (google-auth uses 120 s) are clamped."""
import io
import json
import time
import unittest
from unittest import mock

import _g8 as t
from _tree import run

import urllib3
import urllib3.connectionpool as cp
from urllib3 import exceptions as u3

import g8_guard as guard
import g8_wiring as wiring
from g8_guard import LiveSafetyStop

TOKEN = t.g3.Secret("ya29." + "a" * 40)
EMAIL = "g8-0123456789abcdef@example.com"


def charge_delete():
    egress = guard.Egress("B", t.SIGNER)
    egress.enable_mutation("delete")
    egress.bind_disposable_uid("uid1")
    t.charge(egress, "delete", "uid1")


def jwt_like():
    import base64

    def b64(document):
        return base64.urlsafe_b64encode(json.dumps(document).encode()).decode().rstrip("=")

    now = int(time.time())
    claims = {"aud": t.PROJECT, "iss": "https://securetoken.google.com/" + t.PROJECT, "sub": "uid1", "iat": now - 10, "exp": now + 3000, "auth_time": now - 10}
    return b64({"alg": "RS256", "kid": "k", "typ": "JWT"}) + "." + b64(claims) + "." + "c2ln"


class FakeWire:
    """Stands in for HTTPConnectionPool._make_request. Records (kind-relevant) facts; never touches a socket."""

    def __init__(self):
        self.calls, self.mode, self.timeouts, self.headers = [], "ok", [], []

    def __call__(self, pool, conn, method, url, *args, **kwargs):
        self.calls.append((method, pool.host, url))
        self.timeouts.append((kwargs["timeout"].connect_timeout, kwargs["timeout"].read_timeout))
        self.headers.append(dict(kwargs.get("headers") or {}))
        if self.mode == "connect":
            raise u3.NewConnectionError(conn, "x")
        if self.mode == "read":
            raise u3.ReadTimeoutError(pool, url, "x")
        status = 503 if self.mode == "503" else 200
        path = url.split("?")[0]
        if path.endswith("accounts:lookup"):
            document = {"users": [{"localId": "uid1", "email": EMAIL, "disabled": False, "validSince": "1700000000"}]}
        elif path.endswith("x509/securetoken@system.gserviceaccount.com"):
            document = {"k": "junk"}
        elif path.endswith("/accounts") or path.endswith("accounts:update"):
            document = {"localId": "uid1"}
        elif path.endswith("accounts:delete"):
            document = {"kind": "identitytoolkit#DeleteAccountResponse"}
        else:
            document = {}
        return urllib3.response.HTTPResponse(body=io.BytesIO(json.dumps(document).encode()), headers={"Content-Type": "application/json"},
                                             status=status, preload_content=False, request_method=method)


class SdkPathCase(unittest.TestCase):
    def setUp(self):
        self.wire = FakeWire()
        self._original = cp.HTTPConnectionPool._make_request
        wire = self.wire
        cp.HTTPConnectionPool._make_request = lambda pool, conn, method, url, *a, **k: wire(pool, conn, method, url, *a, **k)
        self.addCleanup(setattr, cp.HTTPConnectionPool, "_make_request", self._original)
        self.addCleanup(guard.uninstall_urllib3_hook)
        self.app = None

    def stage_b(self, *mutations, bind=True):
        egress = guard.Egress("B", t.SIGNER)
        for kind in mutations:
            egress.enable_mutation(kind)
        if bind:
            egress.bind_disposable_uid("uid1")
        guard.install_urllib3_hook(egress)
        self.egress = egress
        self.app = wiring.build_admin_app("g8-test-%d" % id(self), TOKEN)
        self.addCleanup(wiring.delete_app, self.app)
        self.port = t.real.FirebaseAdminPort(t.PROJECT, guard.ADMIN_HTTP_TIMEOUT_SECONDS, app=self.app)
        self.ops = wiring.FirebaseAccountOps(self.app)
        return egress

    def stage_a(self):
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        self.egress = egress
        self.app = wiring.build_admin_app("g8-test-a-%d" % id(self), TOKEN)
        self.addCleanup(wiring.delete_app, self.app)
        self.port = t.real.FirebaseAdminPort(t.PROJECT, guard.ADMIN_HTTP_TIMEOUT_SECONDS, app=self.app)
        return egress

    def kinds(self):
        return list(self.egress.sequence)

    # ---- which requests does each operation REALLY send? ------------------------------------------------------------------
    def test_get_user_is_one_lookup(self):
        self.stage_b()
        self.assertEqual(run(self.port.get_user("uid1"))["uid"], "uid1")
        self.assertEqual(self.kinds(), ["lookup"])

    def test_create_user_is_a_create_plus_a_read_back(self):
        self.stage_b("create")
        self.assertEqual(run(self.port.create_user(EMAIL, "Start-Passw0rd-1")), "uid1")
        self.assertEqual(self.kinds(), ["create", "lookup"])

    def test_set_password_is_an_update_plus_a_read_back(self):
        self.stage_b("update")
        run(self.port.set_password("uid1", "Second-Passw0rd-2"))
        self.assertEqual(self.kinds(), ["update", "lookup"])

    def test_revoke_is_one_update(self):
        self.stage_b("update")
        run(self.port.revoke("uid1"))
        self.assertEqual(self.kinds(), ["update"])

    def test_disable_is_an_update_plus_a_read_back(self):
        self.stage_b("update")
        run(self.ops.set_disabled("uid1", True))
        self.assertEqual(self.kinds(), ["update", "lookup"])

    def test_cleanup_stage_find_is_one_lookup_and_delete_is_one_delete_and_stage_b_has_no_delete_quota(self):
        egress = guard.Egress("C", t.SIGNER)
        egress.enable_mutation("delete")
        egress.bind_disposable_uid("uid1")
        guard.install_urllib3_hook(egress)
        self.egress = egress
        self.app = wiring.build_admin_app("g8-test-c-%d" % id(self), TOKEN)
        self.addCleanup(wiring.delete_app, self.app)
        ops = wiring.FirebaseAccountOps(self.app)
        self.assertEqual(run(ops.find_uid(EMAIL)), "uid1")
        run(ops.delete_user("uid1"))
        self.assertEqual(self.kinds(), ["lookup", "delete"])
        with self.assertRaises(LiveSafetyStop):                       # Stage B can never delete: its delete quota is zero
            guard.Egress("B", t.SIGNER).enable_mutation("delete") or charge_delete()

    def test_verifying_an_id_token_fetches_the_certificates_first(self):
        self.stage_b()
        self.assertIsNone(run(self.port.verify_token(jwt_like())))              # the junk certificate cannot verify the signature
        self.assertEqual(self.kinds(), ["certs"])

    # ---- SDK-internal retries are charged one by one --------------------------------------------------------------------------
    def test_status_retries_inside_the_sdk_are_each_charged_and_the_cap_stops_them(self):
        self.stage_a()
        self.wire.mode = "503"
        with self.assertRaises(LiveSafetyStop) as stop:
            run(self.port.get_user("dummyuid00000001"))
        self.assertEqual(stop.exception.code, "kind_quota_exhausted")
        self.assertEqual(len(self.wire.calls), 2)                               # the SDK wanted up to 5 attempts; only the 2 allowed lookups left the process
        self.assertEqual(self.egress.used()[0].get("lookup"), 2)

    def test_without_a_cap_problem_the_sdk_status_retries_are_all_counted(self):
        self.stage_b()
        self.wire.mode = "503"
        with self.assertRaises(Exception) as failure:
            run(self.port.get_user("uid1"))
        self.assertNotIsInstance(failure.exception, LiveSafetyStop)
        self.assertEqual(len(self.wire.calls), 5)                               # 1 + status=4 retries of firebase_admin's DEFAULT_RETRY_CONFIG
        self.assertEqual(self.egress.used()[0].get("lookup"), 5)

    def test_a_connect_retry_inside_urllib3_is_charged(self):
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        self.egress = egress
        self.app = wiring.build_admin_app("g8-test-connect", TOKEN)
        self.addCleanup(wiring.delete_app, self.app)
        self.wire.mode = "connect"
        with self.assertRaises(Exception):
            run(t.real.FirebaseAdminPort(t.PROJECT, 8.0, app=self.app).get_user("uid1"))
        self.assertEqual(len(self.wire.calls), 2)                               # the first attempt and urllib3's one connect retry, both through the hook
        self.assertEqual(egress.used()[1], 2)

    def test_a_read_retry_inside_urllib3_is_charged(self):
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        self.egress = egress
        self.app = wiring.build_admin_app("g8-test-read", TOKEN)
        self.addCleanup(wiring.delete_app, self.app)
        self.wire.mode = "read"
        with self.assertRaises(Exception):
            run(t.real.FirebaseAdminPort(t.PROJECT, 8.0, app=self.app).get_user("uid1"))
        self.assertEqual(len(self.wire.calls), 2)
        self.assertEqual(egress.used()[1], 2)

    # ---- mutation targets, headers, timeouts -------------------------------------------------------------------------------------
    def test_an_update_for_another_uid_is_refused_before_it_is_sent(self):
        self.stage_b("update")
        with self.assertRaises(LiveSafetyStop) as stop:
            run(self.port.set_password("someoneelse", "Second-Passw0rd-2"))
        self.assertEqual(stop.exception.code, "mutation_target_not_disposable")
        self.assertEqual(self.wire.calls, [])

    def test_a_delete_for_another_uid_is_refused_and_the_guard_trips(self):
        self.stage_b("delete")
        with self.assertRaises(LiveSafetyStop):
            run(self.ops.delete_user("someoneelse"))
        with self.assertRaises(LiveSafetyStop) as stop:
            run(self.ops.delete_user("uid1"))
        self.assertEqual(stop.exception.code, "guard_tripped")
        self.assertEqual(self.wire.calls, [])

    def test_stage_a_refuses_every_mutating_request_at_the_transport(self):
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        self.egress = egress
        self.app = wiring.build_admin_app("g8-test-a", TOKEN)
        self.addCleanup(wiring.delete_app, self.app)
        port = t.real.FirebaseAdminPort(t.PROJECT, 8.0, app=self.app)
        for call in (lambda: port.set_password("dummyuid00000001", "Whatever-Passw0rd-1"), lambda: port.revoke("dummyuid00000001"),
                     lambda: port.create_user(EMAIL, "Whatever-Passw0rd-1")):
            with self.assertRaises(LiveSafetyStop):
                run(call())
        self.assertEqual(self.wire.calls, [])

    def test_the_bearer_token_goes_only_to_the_admin_requests_and_never_to_the_certificate_download(self):
        self.stage_b()
        run(self.port.get_user("uid1"))
        run(self.port.verify_token(jwt_like()))
        lookup_headers, cert_headers = self.wire.headers[0], self.wire.headers[1]
        self.assertTrue(any(k.lower() == "authorization" and v == "Bearer " + TOKEN.reveal() for k, v in lookup_headers.items()))
        self.assertFalse(any(k.lower() == "authorization" for k in cert_headers))

    def test_timeouts_reaching_the_wire_are_always_bounded_including_google_auths_120_seconds(self):
        self.stage_b()
        run(self.port.get_user("uid1"))
        run(self.port.verify_token(jwt_like()))
        for connect, read in self.wire.timeouts:
            self.assertLessEqual(connect, guard.CONNECT_TIMEOUT_SECONDS)
            self.assertLessEqual(read, guard.MAX_TIMEOUT_SECONDS)
            self.assertGreater(read, 0)

    def test_no_application_default_credentials_are_looked_up_when_the_admin_app_is_built(self):
        with mock.patch("google.auth.default", side_effect=AssertionError("ADC lookup")):
            self.stage_b()
            run(self.port.get_user("uid1"))
        self.assertIsNone(self.app.credential.get_credential().refresh_token)


if __name__ == "__main__":
    unittest.main()
