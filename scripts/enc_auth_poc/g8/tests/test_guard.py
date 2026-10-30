"""G8 egress guard: exact allowlists, HTTPS only, bounded timeouts, no proxy, per-kind and total caps (also across threads), mutation gating,
tripping, the environment checks, the three transport layers (httpx / G3 stdlib / urllib3 + sockets in a loopback subprocess) and the
fail-closed `cap_guaranteed()`. Offline: the G7 network-denial layer is active for this process; the loopback probe runs in a subprocess that can
only reach 127.0.0.1."""
import json
import subprocess
import sys
import threading
import unittest
from unittest import mock

import _g8 as t
from _tree import ATTEMPTS, run

import httpx

import g3_guard as g3
import g8_guard as guard
import g8_wiring as wiring
from g8_guard import LiveSafetyStop

PROBE = t.HERE / "loopback_probe.py"


def stop_code(callable_):
    try:
        callable_()
    except LiveSafetyStop as stop:
        return stop.code
    return None


class Disabled(unittest.TestCase):
    def test_every_approval_constant_is_off_as_committed(self):
        self.assertIs(guard.LIVE_EXECUTION_APPROVED, False)                                 # locked down after the completed live tests
        self.assertEqual(guard.STAGE_APPROVED, {"A": False, "B": False, "C": False})
        self.assertIs(g3.LIVE_MODE_ENABLED, False)
        self.assertIs(g3.CUSTOM_TOKEN_LIVE_APPROVED, False)

    def test_the_project_is_hardcoded_and_matches_g3(self):
        self.assertEqual(guard.ALLOWED_PROJECT_ID, "epicverse-g3-auth-test")
        self.assertEqual(g3.ALLOWED_PROJECT_ID, guard.ALLOWED_PROJECT_ID)

    def test_importing_the_guard_opens_nothing_and_no_hook_is_installed_by_import(self):
        self.assertEqual(ATTEMPTS, [])
        self.assertIsNone(guard._installed["urllib3"])
        self.assertIsNone(guard._installed["socket"])


class Policy(unittest.TestCase):
    def test_every_allowed_request_maps_to_exactly_its_kind(self):
        egress = guard.Egress("B", t.SIGNER)
        for kind in ("sign_in", "custom_exchange", "refresh", "lookup", "certs", "sign_blob"):
            self.assertEqual(t.charge(egress, kind), kind)
        egress.enable_mutation("create")
        egress.enable_mutation("update")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(t.charge(egress, "create"), "create")
        self.assertEqual(t.charge(egress, "update", "uid1"), "update")

    def test_refused_shapes(self):
        cases = {
            "not_https": ("POST", "http://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + t.API_KEY),
            "host_not_allowed": ("POST", "https://identitytoolkit.googleapis.com.evil.example/v1/accounts:signInWithPassword"),
            "path_not_allowed": ("POST", "https://identitytoolkit.googleapis.com/v1/accounts:signUp?key=" + t.API_KEY),
            "authority_invalid": ("POST", "https://identitytoolkit.googleapis.com:8443/v1/accounts:lookup"),
        }
        for expected, (method, url) in cases.items():
            self.assertEqual(stop_code(lambda: guard.Egress("B", t.SIGNER).charge(method, url, b"{}", 5.0)), expected, url)

    def test_each_refusal_class(self):
        def code(method, url, **kw):
            return stop_code(lambda: guard.Egress("B", t.SIGNER).charge(method, url, kw.get("body", b"{}"), kw.get("timeout", 5.0)))
        self.assertEqual(code("POST", t.PROJ.replace(t.PROJECT, "another-project") + "/accounts:lookup"), "path_not_allowed")
        self.assertEqual(code("GET", t.PROJ + "/accounts:lookup"), "path_not_allowed")                      # wrong method
        self.assertEqual(code("POST", t.PROJ + "/accounts:lookup/"), "path_not_allowed")                    # trailing slash
        self.assertEqual(code("POST", t.PROJ + "/accounts:lookup?alt=json"), "query_not_allowed")
        self.assertEqual(code("POST", t.BASE + "/accounts:signInWithPassword?key=short"), "query_not_allowed")
        self.assertEqual(code("POST", t.BASE + "/accounts:signInWithPassword?key=" + t.API_KEY + "&x=1"), "query_not_allowed")
        self.assertEqual(code("POST", "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/other@%s.iam.gserviceaccount.com:signBlob" % t.PROJECT),
                         "path_not_allowed")                                                              # signBlob only for the configured signer
        self.assertEqual(code("POST", "https://IDENTITYTOOLKIT.googleapis.com/v1/accounts:lookup"), "authority_invalid")
        self.assertEqual(code("POST", "https://user:pw@identitytoolkit.googleapis.com/v1/accounts:lookup"), "authority_invalid")
        self.assertEqual(code("POST", t.PROJ + "/accounts:lookup#frag"), "authority_invalid")
        self.assertEqual(code("POST", "https://identitytoolkit.googleapis.com:abc/v1/accounts:lookup"), "url_invalid")
        self.assertEqual(code("POST", "https://identitytoolkit.googleapis.com:99999/v1/accounts:lookup"), "url_invalid")
        self.assertEqual(code("POST", t.PROJ + "/accounts:lookup\n"), "url_invalid")
        self.assertEqual(code("GET", "https://www.googleapis.com/robot/v1/metadata/x509/other@system.gserviceaccount.com"), "path_not_allowed")
        self.assertEqual(code("POST", "https://oauth2.googleapis.com/token"), "host_not_allowed")
        self.assertEqual(code("POST", "https://sts.googleapis.com/v1/token"), "host_not_allowed")
        self.assertEqual(code("POST", "https://iam.googleapis.com/v1/projects/x/serviceAccounts"), "host_not_allowed")
        self.assertEqual(code("POST", "https://firebase.googleapis.com/v1beta1/projects"), "host_not_allowed")

    def test_signblob_without_a_configured_signer_is_refused(self):
        self.assertEqual(stop_code(lambda: guard.Egress("A").charge(*t.URLS["sign_blob"][:2], b"{}", 5.0)), "path_not_allowed")

    def test_timeouts_must_be_numeric_positive_and_at_most_ten_seconds(self):
        for bad in (None, 0, -1, True, "5", 10.01, float("inf")):
            self.assertEqual(stop_code(lambda: guard.Egress("A", t.SIGNER).charge(*t.URLS["lookup"], b"{}", bad)), "timeout_unbounded", bad)
        self.assertEqual(guard.Egress("A", t.SIGNER).charge(*t.URLS["lookup"], b"{}", 10), "lookup")

    def test_proxied_requests_are_refused(self):
        self.assertEqual(stop_code(lambda: guard.Egress("A", t.SIGNER).charge(*t.URLS["lookup"], b"{}", 5.0, proxied=True)), "proxy_not_allowed")

    def test_a_refusal_trips_the_guard_for_every_later_request(self):
        egress = guard.Egress("A", t.SIGNER)
        self.assertEqual(stop_code(lambda: egress.charge("POST", "https://example.org/", b"", 5.0)), "host_not_allowed")
        self.assertEqual(stop_code(lambda: t.charge(egress, "lookup")), "guard_tripped")
        self.assertEqual(egress.used(), ({}, 0))
        self.assertEqual(egress.refused, 2)

    def test_quotas_and_totals_are_exact(self):
        self.assertEqual(sum(guard.STAGE_QUOTAS["A"].values()), 8)
        self.assertEqual(sum(guard.STAGE_QUOTAS["B"].values()), 34)
        self.assertEqual(sum(guard.STAGE_QUOTAS["C"].values()), 3)
        self.assertLessEqual(sum(sum(q.values()) for q in guard.STAGE_QUOTAS.values()), guard.GLOBAL_REQUEST_CAP)
        egress = guard.Egress("A", t.SIGNER)
        for _ in range(2):
            t.charge(egress, "sign_in")
        self.assertEqual(stop_code(lambda: t.charge(egress, "sign_in")), "kind_quota_exhausted")

    def test_a_kind_with_no_quota_in_the_stage_is_refused(self):
        self.assertEqual(stop_code(lambda: t.charge(guard.Egress("A", t.SIGNER), "refresh")), "kind_quota_exhausted")
        self.assertEqual(stop_code(lambda: t.charge(guard.Egress("C", t.SIGNER), "sign_in")), "kind_quota_exhausted")

    def test_the_total_cap_holds_even_if_the_quota_table_were_larger(self):
        egress = guard.Egress("A", t.SIGNER)
        egress._quota["lookup"] = 99
        t.charge(egress, "lookup")
        egress.total_cap = 1
        self.assertEqual(stop_code(lambda: t.charge(egress, "lookup")), "request_cap_reached")

    def test_concurrent_callers_cannot_exceed_a_quota(self):
        egress = guard.Egress("A", t.SIGNER)
        outcomes = []

        def worker():
            try:
                t.charge(egress, "lookup")
                outcomes.append("ok")
            except LiveSafetyStop:
                outcomes.append("refused")
        threads = [threading.Thread(target=worker) for _ in range(40)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("ok"), 2)
        self.assertEqual(egress.used()[1], 2)

    def test_the_stop_is_a_base_exception_so_an_sdk_cannot_swallow_it(self):
        self.assertTrue(issubclass(LiveSafetyStop, BaseException))
        self.assertFalse(issubclass(LiveSafetyStop, Exception))
        swallowed = []
        try:
            try:
                raise LiveSafetyStop("x")
            except Exception:
                swallowed.append(True)
        except LiveSafetyStop:
            pass
        self.assertEqual(swallowed, [])

    def test_messages_carry_only_a_fixed_code(self):
        egress = guard.Egress("A", t.SIGNER)
        with self.assertRaises(LiveSafetyStop) as stop:
            egress.charge("POST", "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=" + t.API_KEY + "&secret=hunter2", b"{}", 5.0)
        self.assertNotIn("hunter2", str(stop.exception))
        self.assertNotIn(t.API_KEY, str(stop.exception))


class Mutations(unittest.TestCase):
    def test_stage_a_cannot_enable_any_mutation(self):
        egress = guard.Egress("A", t.SIGNER)
        for kind in ("create", "update", "delete"):
            self.assertEqual(stop_code(lambda: egress.enable_mutation(kind)), "mutation_not_allowed")

    def test_stage_a_mutating_requests_are_refused_even_with_a_matching_body(self):
        for kind in ("create", "update", "delete"):
            self.assertIn(stop_code(lambda: t.charge(guard.Egress("A", t.SIGNER), kind, "uid1")), ("mutation_not_confirmed",))

    def test_stage_b_needs_the_class_enabled_and_a_bound_uid_named_in_the_body(self):
        egress = guard.Egress("B", t.SIGNER)
        self.assertEqual(stop_code(lambda: t.charge(egress, "create")), "mutation_not_confirmed")
        egress = guard.Egress("B", t.SIGNER)
        egress.enable_mutation("update")
        self.assertEqual(stop_code(lambda: t.charge(egress, "update", "uid1")), "mutation_target_not_disposable")          # no uid bound yet
        egress = guard.Egress("B", t.SIGNER)
        egress.enable_mutation("update")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(stop_code(lambda: t.charge(egress, "update", "uid2")), "mutation_target_not_disposable")
        egress = guard.Egress("B", t.SIGNER)
        egress.enable_mutation("update")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(stop_code(lambda: egress.charge(*t.URLS["update"], b"not json", 5.0)), "mutation_target_not_disposable")
        egress = guard.Egress("B", t.SIGNER)
        egress.enable_mutation("update")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(stop_code(lambda: egress.charge(*t.URLS["update"], json.dumps({"localId": ["uid1"]}).encode(), 5.0)), "mutation_target_not_disposable")
        egress = guard.Egress("B", t.SIGNER)
        egress.enable_mutation("update")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(t.charge(egress, "update", "uid1"), "update")

    def test_the_uid_can_be_bound_once_and_only_in_shape(self):
        egress = guard.Egress("B", t.SIGNER)
        for bad in ("", "has space", "a/b", None, 7, "x" * 129):
            self.assertEqual(stop_code(lambda: guard.Egress("B", t.SIGNER).bind_disposable_uid(bad)), "uid_bind_refused", bad)
        egress.bind_disposable_uid("uid1")
        self.assertEqual(stop_code(lambda: egress.bind_disposable_uid("uid2")), "uid_bind_refused")
        self.assertEqual(stop_code(lambda: guard.Egress("A", t.SIGNER).bind_disposable_uid("uid1")), "uid_bind_refused")

    def test_stage_b_has_no_delete_and_stage_c_has_only_delete(self):
        egress = guard.Egress("B", t.SIGNER)
        egress.enable_mutation("delete")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(stop_code(lambda: t.charge(egress, "delete", "uid1")), "kind_quota_exhausted")
        egress = guard.Egress("C", t.SIGNER)
        for kind in ("create", "update"):
            self.assertEqual(stop_code(lambda: egress.enable_mutation(kind)), "mutation_not_allowed")
        egress.enable_mutation("delete")
        egress.bind_disposable_uid("uid1")
        self.assertEqual(t.charge(egress, "delete", "uid1"), "delete")

    def test_unknown_stage_and_bad_signer_are_refused_at_construction(self):
        self.assertEqual(stop_code(lambda: guard.Egress("Z")), "stage_unknown")
        with self.assertRaises(g3.GuardError):
            guard.Egress("A", "sa@some-other-project.iam.gserviceaccount.com")


class Environment(unittest.TestCase):
    def test_clean_environment_passes(self):
        self.assertIsNone(guard.check_environment({}))
        self.assertIsNone(guard.check_environment({"GOOGLE_CLOUD_PROJECT": guard.ALLOWED_PROJECT_ID}))

    def test_proxy_tls_credential_and_project_variables_refuse(self):
        for name in ("HTTPS_PROXY", "http_proxy", "ALL_PROXY"):
            self.assertEqual(guard.check_environment({name: "http://proxy:3128"}), "proxy_environment")
        for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE", "SSL_CERT_DIR"):
            self.assertEqual(guard.check_environment({name: "x"}), "tls_override_environment")
        for name in ("GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_AUTH_EMULATOR_HOST", "FIREBASE_CONFIG"):
            self.assertEqual(guard.check_environment({name: "x"}), "credential_environment")
        self.assertEqual(guard.check_environment({"GOOGLE_CLOUD_PROJECT": "epicverse-production"}), "project_environment")

    def test_blank_values_do_not_count(self):
        self.assertIsNone(guard.check_environment({"HTTPS_PROXY": "  ", "SSL_CERT_FILE": ""}))


class FakeInner(httpx.AsyncBaseTransport):
    """Stands in for httpx's real transport: records what would be sent, answers from a queue, opens no socket."""
    instances = []

    def __init__(self, *args, **kwargs):
        FakeInner.instances.append(self)
        self.sent, self.answers = [], []

    async def handle_async_request(self, request):
        self.sent.append(str(request.url.host))
        status = FakeInner.status
        return httpx.Response(status, json=FakeInner.document, request=request)

    async def aclose(self):
        pass


class HttpxLayer(unittest.TestCase):
    def setUp(self):
        FakeInner.instances.clear()
        FakeInner.status, FakeInner.document = 200, {"localId": "uid1"}
        patcher = mock.patch.object(httpx, "AsyncHTTPTransport", FakeInner)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, egress, url):
        async def go():
            async with guard.guarded_client_factory(egress)() as client:
                return await client.post(url, json={})
        return run(go())

    def test_an_allowed_request_is_charged_then_forwarded(self):
        egress = guard.Egress("A", t.SIGNER)
        self.assertEqual(self.post(egress, t.URLS["lookup"][1]).status_code, 200)
        self.assertEqual(egress.used(), ({"lookup": 1}, 1))
        self.assertEqual(FakeInner.instances[0].sent, ["identitytoolkit.googleapis.com"])

    def test_a_disallowed_request_is_refused_before_anything_is_forwarded(self):
        egress = guard.Egress("A", t.SIGNER)
        with self.assertRaises(LiveSafetyStop):
            self.post(egress, "https://example.org/x")
        self.assertEqual(FakeInner.instances[0].sent, [])

    def test_a_redirect_answer_stops_the_run(self):
        FakeInner.status = 302
        egress = guard.Egress("A", t.SIGNER)
        with self.assertRaises(LiveSafetyStop) as stop:
            self.post(egress, t.URLS["lookup"][1])
        self.assertEqual(stop.exception.code, "redirect_refused")
        self.assertEqual(egress.tripped, "redirect_refused")

    def test_the_client_never_inherits_proxies_or_follows_redirects(self):
        client = guard.guarded_client_factory(guard.Egress("A", t.SIGNER))()
        self.assertFalse(client.follow_redirects)
        self.assertFalse(client._trust_env)
        self.assertEqual(client.timeout, httpx.Timeout(8.0))
        run(client.aclose())

    def test_unbounded_timeouts_cannot_be_requested(self):
        with self.assertRaises(LiveSafetyStop):
            guard.guarded_client_factory(guard.Egress("A", t.SIGNER), timeout=60)

    def test_the_g7_adapters_own_retries_are_counted_and_stop_at_the_cap(self):
        FakeInner.status, FakeInner.document = 503, {}
        egress = guard.Egress("A", t.SIGNER)
        port = t.real.HttpxIdentityToolkit(t.API_KEY, guard.guarded_client_factory(egress))
        identity = t.real.FirebaseIdentity(port, None, None, t.SIGNER)
        from app.core.config import settings
        with mock.patch.object(settings, "ENC_AUTH_RETRY_MAX", 2):
            with self.assertRaises(LiveSafetyStop) as stop:
                run(identity.verify_password("a@example.com", "x" * 12))
        self.assertEqual(stop.exception.code, "kind_quota_exhausted")
        self.assertEqual(sum(len(instance.sent) for instance in FakeInner.instances), 2)         # the third attempt never reached the transport
        self.assertEqual(egress.used(), ({"sign_in": 2}, 2))

    def test_with_retries_off_one_attempt_is_made(self):
        FakeInner.status, FakeInner.document = 503, {}
        egress = guard.Egress("A", t.SIGNER)
        identity = t.real.FirebaseIdentity(t.real.HttpxIdentityToolkit(t.API_KEY, guard.guarded_client_factory(egress)), None, None, t.SIGNER)
        from app.core.config import settings
        with mock.patch.object(settings, "ENC_AUTH_RETRY_MAX", 0):
            with self.assertRaises(Exception) as failure:
                run(identity.verify_password("a@example.com", "x" * 12))
        self.assertNotIsInstance(failure.exception, LiveSafetyStop)
        self.assertEqual(egress.used(), ({"sign_in": 1}, 1))


class G3Layer(unittest.TestCase):
    class Connection:
        opened = []

        def __init__(self, host, port, timeout=None, context=None):
            G3Layer.Connection.opened.append(host)
            self.sock = mock.Mock()

        def connect(self):
            pass

        def request(self, *args, **kwargs):
            pass

        def getresponse(self):
            body = json.dumps({"error": {"message": "TOKEN_EXPIRED"}}).encode()
            return mock.Mock(status=400, read=mock.Mock(side_effect=[body, b""]))

        def close(self):
            pass

    def setUp(self):
        G3Layer.Connection.opened.clear()

    def client(self, egress):
        import g3_rest_client as rc
        transport = guard.g3_transport(egress)
        transport._factory = G3Layer.Connection
        return rc.FirebaseRestClient(g3.load_config(t.PROJECT, t.API_KEY, {}), transport)

    def test_the_g3_client_runs_on_the_g8_budget(self):
        egress = guard.Egress("B", t.SIGNER)
        result = self.client(egress).refresh("r" * 30)
        self.assertEqual(result.error_code, "TOKEN_EXPIRED")
        self.assertEqual(egress.used(), ({"refresh": 1}, 1))
        self.assertEqual(G3Layer.Connection.opened, ["securetoken.googleapis.com"])

    def test_g3_requests_beyond_the_stage_quota_never_connect(self):
        egress = guard.Egress("A", t.SIGNER)
        with self.assertRaises(LiveSafetyStop):
            self.client(egress).refresh("r" * 30)                    # Stage A has no refresh quota
        self.assertEqual(G3Layer.Connection.opened, [])

    def test_the_g3_guard_still_applies_first(self):
        import g3_rest_client as rc
        egress = guard.Egress("B", t.SIGNER)
        transport = guard.g3_transport(egress)
        bad = rc.TransportRequest("POST", "https://example.org/x", (), b"", "application/json", 5.0, 10.0, 1000)
        with self.assertRaises(g3.GuardError):
            transport(bad)
        self.assertEqual(egress.used(), ({}, 0))


class RealWire(unittest.TestCase):
    """urllib3 hook and socket backstop against a loopback server, in a subprocess that can only reach 127.0.0.1."""

    @classmethod
    def setUpClass(cls):
        done = subprocess.run([sys.executable, "-B", "-I", str(PROBE)], capture_output=True, text=True, timeout=120)
        if done.returncode != 0:
            raise RuntimeError("probe failed: " + done.stderr[-400:])
        cls.r = json.loads(done.stdout)

    def test_the_sdks_own_status_retries_are_stopped_by_the_cap_at_the_server(self):
        self.assertEqual(self.r["status_retries"]["outcome"], "kind_quota_exhausted")
        self.assertEqual((self.r["status_retries"]["server_hits"], self.r["status_retries"]["charged"]), (2, 2))

    def test_without_a_cap_problem_charged_equals_what_the_server_saw_including_retries(self):
        wide = self.r["status_retries_unbounded_by_cap"]
        self.assertEqual((wide["server_hits"], wide["charged"]), (5, 5))                # 1 attempt + status=4 retries of firebase_admin's retry config

    def test_each_new_connection_is_counted_and_never_exceeds_the_requests(self):
        closed, alive = self.r["sequential_closed"], self.r["keep_alive"]
        self.assertEqual((closed["server_hits"], closed["charged"], closed["connections"]), (3, 3, 3))
        self.assertEqual((alive["server_hits"], alive["charged"], alive["connections"]), (3, 3, 1))
        self.assertLessEqual(alive["connections"], alive["charged"])

    def test_a_redirect_is_never_followed(self):
        redirect = self.r["redirect"]
        self.assertEqual((redirect["outcome"], redirect["server_hits"]), ("redirect_refused", 1))

    def test_other_hosts_and_https_to_the_probe_port_are_refused_before_any_name_resolution(self):
        for name, code in (("other_host", "not_https"), ("https_to_loopback", "authority_invalid")):
            self.assertEqual((self.r[name]["outcome"], self.r[name]["resolutions"], self.r[name]["server_hits"]), (code, 0, 0), name)

    def test_a_request_without_any_timeout_is_bounded_not_forwarded_unbounded(self):
        self.assertEqual((self.r["no_timeout"]["outcome"], self.r["no_timeout"]["server_hits"]), ("returned", 1))

    def test_after_a_trip_even_a_valid_request_is_refused(self):
        self.assertEqual((self.r["after_trip"]["outcome"], self.r["after_trip"]["server_hits"]), ("guard_tripped", 0))

    def test_the_socket_backstop_refuses_unresolved_addresses_and_foreign_names(self):
        self.assertEqual(self.r["backstop_direct"]["outcome"], "connect_address_not_resolved")
        self.assertEqual(self.r["backstop_dns"]["outcome"], "dns_host_not_allowed")
        self.assertEqual(self.r["backstop_dns"]["resolutions"], 0)

    def test_cap_guaranteed_needs_every_layer_and_retries_off(self):
        self.assertEqual(self.r["cap_guaranteed_installed"], [True, []])
        self.assertEqual(self.r["cap_guaranteed_retry_on"], [False, ["adapter_retries_not_disabled"]])
        self.assertEqual(self.r["cap_guaranteed_uninstalled"], [False, ["urllib3_hook_missing", "socket_backstop_missing"]])


class CapGuaranteed(unittest.TestCase):
    def tearDown(self):
        guard.uninstall_all()

    def test_not_guaranteed_when_nothing_is_installed(self):
        ok, reasons = guard.cap_guaranteed(env={}, retry_max=0)
        self.assertFalse(ok)
        self.assertIn("urllib3_hook_missing", reasons)
        self.assertIn("socket_backstop_missing", reasons)

    def test_guaranteed_only_with_both_layers_validated_versions_no_retries_and_a_clean_environment(self):
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        guard.install_socket_backstop(egress)
        self.assertEqual(guard.cap_guaranteed(env={}, retry_max=0), (True, []))
        self.assertEqual(guard.cap_guaranteed(env={}, retry_max=1)[1], ["adapter_retries_not_disabled"])
        self.assertEqual(guard.cap_guaranteed(env={}, retry_max=None)[1], ["adapter_retries_not_disabled"])
        self.assertEqual(guard.cap_guaranteed(env={"HTTPS_PROXY": "x"}, retry_max=0)[1], ["proxy_environment"])
        versions = dict(guard._versions(), urllib3="3.0.0")
        self.assertEqual(guard.cap_guaranteed(env={}, versions=versions, retry_max=0)[1], ["library_not_validated_urllib3"])
        versions = dict(guard._versions(), firebase_admin="7.0.0")
        self.assertEqual(guard.cap_guaranteed(env={}, versions=versions, retry_max=0)[1], ["library_not_validated_firebase_admin"])
        versions = dict(guard._versions(), httpx=None)
        self.assertEqual(guard.cap_guaranteed(env={}, versions=versions, retry_max=0)[1], ["library_not_validated_httpx"])

    def test_not_guaranteed_if_the_hook_was_replaced_after_installation(self):
        import urllib3.connectionpool as cp
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        guard.install_socket_backstop(egress)
        hooked = cp.HTTPConnectionPool._make_request
        cp.HTTPConnectionPool._make_request = lambda *a, **k: None
        try:
            self.assertIn("urllib3_hook_missing", guard.cap_guaranteed(env={}, retry_max=0)[1])
        finally:
            cp.HTTPConnectionPool._make_request = hooked

    def test_installing_twice_is_refused_and_uninstall_restores_the_originals(self):
        import socket
        import urllib3.connectionpool as cp
        before = (cp.HTTPConnectionPool._make_request, socket.getaddrinfo, socket.socket.connect)
        egress = guard.Egress("A", t.SIGNER)
        guard.install_urllib3_hook(egress)
        guard.install_socket_backstop(egress)
        self.assertEqual(stop_code(lambda: guard.install_urllib3_hook(egress)), "hook_already_installed")
        self.assertEqual(stop_code(lambda: guard.install_socket_backstop(egress)), "hook_already_installed")
        guard.uninstall_all()
        self.assertEqual((cp.HTTPConnectionPool._make_request, socket.getaddrinfo, socket.socket.connect), before)

    def test_the_timeout_clamp(self):
        from urllib3.util.timeout import Timeout
        for given in (None, Timeout(connect=120, read=120), Timeout(total=500), Timeout()):
            bounded = guard.bounded_timeout(given)
            self.assertLessEqual(bounded.connect_timeout, guard.CONNECT_TIMEOUT_SECONDS)
            self.assertLessEqual(bounded.read_timeout, guard.MAX_TIMEOUT_SECONDS)
        self.assertEqual(guard.bounded_timeout(Timeout(connect=1, read=2)).read_timeout, 2)


class SocketBackstopLogic(unittest.TestCase):
    def test_dns_only_for_allowed_hosts_and_connect_caps(self):
        egress = guard.Egress("A", t.SIGNER)
        for host in sorted(guard.ALLOWED_HOSTS):
            egress.allow_dns(host)                                    # (the cap on lookups is the stage total, 8)
        self.assertEqual(stop_code(lambda: egress.allow_dns("example.org")), "dns_host_not_allowed")
        self.assertEqual(stop_code(lambda: egress.allow_dns("identitytoolkit.googleapis.com")), "guard_tripped")

    def test_connections_are_capped_at_the_stage_total_and_loopback_is_capped_separately(self):
        egress = guard.Egress("C", t.SIGNER)
        for _ in range(3):
            egress.allow_connect(False)
        self.assertEqual(stop_code(lambda: egress.allow_connect(False)), "connection_cap_reached")
        egress = guard.Egress("C", t.SIGNER)
        for _ in range(guard.LOOPBACK_CONNECT_CAP):
            egress.allow_connect(True)
        self.assertEqual(stop_code(lambda: egress.allow_connect(True)), "loopback_cap_reached")


class DnsNameTypes(unittest.TestCase):
    """Regression: the first live Stage A attempt stopped with dns_host_not_allowed after one sign-in. anyio/httpcore resolve names as BYTES."""

    def tearDown(self):
        guard.uninstall_all()

    def test_allow_dns_accepts_the_exact_allowed_names_as_str_or_ascii_bytes_only(self):
        for host in sorted(guard.ALLOWED_HOSTS):
            for form in (host, host.encode("ascii"), bytearray(host.encode("ascii"))):
                egress = guard.Egress("A", t.SIGNER)
                egress.allow_dns(form)
                self.assertIsNone(egress.tripped)

    def test_allow_dns_still_refuses_everything_else_in_either_type(self):
        bad = ["example.org", "identitytoolkit.googleapis.com.", "IDENTITYTOOLKIT.googleapis.com", "evil.identitytoolkit.googleapis.com",
               "identitytoolkit.googleapis.com.evil.example", "googleapis.com", "localhost", "127.0.0.1", "", None, 7, "іdentitytoolkit.googleapis.com"]
        for host in bad:
            for form in (host, host.encode("utf-8") if isinstance(host, str) else host):
                self.assertEqual(stop_code(lambda: guard.Egress("A", t.SIGNER).allow_dns(form)), "dns_host_not_allowed", repr(form))

    def test_the_real_httpx_httpcore_anyio_stack_resolves_an_allowed_name_through_the_backstop(self):
        import socket
        seen = []

        def recorder(host, *args, **kwargs):
            seen.append(type(host).__name__)
            raise OSError("offline fixture: no resolution")
        egress = guard.Egress("A", t.SIGNER)
        saved = socket.getaddrinfo
        socket.getaddrinfo = recorder
        try:
            guard.install_socket_backstop(egress)
            async def go():
                async with guard.guarded_client_factory(egress)() as client:
                    return await client.post(t.URLS["lookup"][1], json={})
            with self.assertRaises(httpx.ConnectError):                      # the offline resolver refused, NOT the G8 guard
                run(go())
        finally:
            guard.uninstall_all()
            socket.getaddrinfo = saved
        self.assertEqual(len(seen), 1, seen)                                 # the name passed the guard and reached the (fake) resolver
        self.assertIsNone(egress.tripped)
        self.assertEqual(egress.used(), ({"lookup": 1}, 1))

    def test_a_foreign_name_through_the_same_stack_is_still_refused_before_the_resolver(self):
        import socket
        seen = []
        saved = socket.getaddrinfo
        socket.getaddrinfo = lambda host, *a, **k: seen.append(host) or (_ for _ in ()).throw(OSError("x"))
        egress = guard.Egress("A", t.SIGNER)
        try:
            guard.install_socket_backstop(egress)
            from g8_guard import LiveSafetyStop as stop

            async def go():
                import anyio
                await anyio.connect_tcp("example.org", 443)
            with self.assertRaises(stop) as caught:
                run(go())
            self.assertEqual(caught.exception.code, "dns_host_not_allowed")
        finally:
            guard.uninstall_all()
            socket.getaddrinfo = saved
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()
