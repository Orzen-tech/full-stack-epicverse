"""G6b integration tests (2/3): the transient-503 hardening - correlation ids, stage/cause categories, bounded retries for idempotent
reads only, and fault injection (dropped relay connections, unavailable relay, delayed/paused emulator, PostgreSQL down, pool saturation,
identity 502/503/504 and 400/401 answers, partial password change).

The fault injector is the lab's fixed-target relay (counters + canned replies), process suspension of the LOCAL emulator, and `docker stop`
of the lab's own Postgres. Nothing leaves the machine. These tests show that the gateway FAILS CLOSED with a safe, consistent
"unavailable" answer; they do NOT identify the cause of the one unexplained transient 503 seen in a G6a run (no evidence either way).
"""
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import (Gateway, LabTestCase, auth, claims, forged_token, make_user, new_password, session_from, unique)  # noqa: E402

UNAVAILABLE = {"error": "AUTH_UNAVAILABLE"}
SAFE_CAUSES = {"disabled", "no_hash_secret", "not_local", "bad_keys", "connect", "refused", "reset", "dns", "connect_timeout", "read_timeout",
               "transport", "http_502", "http_503", "http_504", "http_5xx", "http_4xx", "sdk_unavailable", "sdk_timeout", "sdk_error", "pool",
               "db_unavailable", "db_timeout", "timeout", "capacity", "unexpected"}


def setUpModule():
    support.start(["main", "retry0", "deadline", "poolq", "nodb"])


def tearDownModule():
    support.stop()


def cause(head):
    return head.get("X-Enc-Cause") or head.get("x-enc-cause")


def request_id(head):
    return head.get("X-Enc-Request-Id") or head.get("x-enc-request-id")


def pwf_global(g):
    return int(g.sql("SELECT coalesce(sum(hits),0) FROM enc_auth_limits WHERE scope='pwf_global'"))


class Base(LabTestCase):
    def setUp(self):
        support.LAB.relay_reset()
        self.user = make_user()

    def tearDown(self):
        support.LAB.relay_reset()
        support.LAB.resume_emulator()

    def login(self, g=None):
        return (g or Gateway()).login(self.user["email"], self.user["password"])


class RelayFaults(Base):
    def test_a_dropped_first_relay_connection_is_retried_and_the_login_succeeds(self):
        g = Gateway()
        before = pwf_global(g)
        g.diag_reset()
        support.LAB.relay_faults(drop_first=1)
        status, body, head = self.login(g)
        self.assertEqual(status, 200)
        stats = support.LAB.relay_stats()
        self.assertEqual(stats["dropped"], 1)
        counts = g.diag()["gateway"]["counts"]
        self.assertEqual(counts.get("retry|verify_password|reset"), 1)
        self.assertEqual(counts.get("retry_success|verify_password|-"), 1)
        self.assertEqual(pwf_global(g), before)                         # a successful login (after a retry) is not a counted failure
        self.assertRegex(request_id(head), r"^[0-9a-f]{12}$")

    def test_without_retries_the_same_fault_is_a_safe_503_and_never_a_credential_error(self):
        g = Gateway("retry0")
        before = pwf_global(g)
        support.LAB.relay_faults(drop_first=1)
        status, body, head = self.login(g)
        self.assertEqual((status, body), (503, UNAVAILABLE))
        self.assertEqual(cause(head), "reset")
        self.assertNotIn("custom_token", body)
        self.assertEqual(pwf_global(g), before)                         # a transport failure is NOT a wrong-password attempt
        self.assertEqual(self.login(g)[0], 200)                         # and the very next try works

    def test_a_briefly_unavailable_relay_recovers_within_the_retry_budget(self):
        g = Gateway()
        support.LAB.relay_faults(refuse_for_s=0.15)
        started = time.monotonic()
        status, _, _ = self.login(g)
        self.assertEqual(status, 200)
        self.assertLess(time.monotonic() - started, 3.0)

    def test_a_relay_that_stays_unavailable_gives_503_after_at_most_two_retries_inside_the_budget(self):
        g = Gateway()
        before = pwf_global(g)
        g.diag_reset()
        support.LAB.relay_faults(refuse_for_s=30)
        started = time.monotonic()
        status, body, head = self.login(g)
        elapsed = time.monotonic() - started
        self.assertEqual((status, body), (503, UNAVAILABLE))
        self.assertIn(cause(head), SAFE_CAUSES)
        counts = g.diag()["gateway"]["counts"]
        self.assertEqual(sum(v for k, v in counts.items() if k.startswith("retry|")), 2)       # exactly the two allowed retries
        self.assertLess(elapsed, 5.0)
        self.assertEqual(pwf_global(g), before)
        self.assertEqual(support.LAB.relay_stats()["refused"], 3)                               # 1 attempt + 2 retries reached the relay
        support.LAB.relay_reset()
        self.assertEqual(self.login(g)[0], 200)

    def test_a_dropped_keepalive_connection_to_the_identity_service_is_survived(self):
        g = Gateway()
        session = session_from(self.login(g)[1]["custom_token"])
        support.LAB.relay_kill()                                       # like a relay restart: every live connection is cut
        status, body, _ = g.call("reauth", {"email": self.user["email"], "password": self.user["password"]}, bearer=session["idToken"])
        self.assertEqual(status, 200, body)

    def test_the_email_routes_answer_503_not_403_when_the_identity_service_is_unreachable(self):
        g = Gateway()
        self.assertEqual(g.sync_user(forged_token(self.user["uid"]), self.user["uid"], self.user["email"])[0], 200)
        support.LAB.relay_kill()
        support.LAB.relay_faults(refuse_for_s=30)
        status, out, _ = g.bearer_post("/auth/email/verify-request", forged_token(self.user["uid"]), json_body={})
        self.assertEqual(status, 503)                                  # unavailable, NOT "forbidden"
        support.LAB.relay_reset()
        self.assertEqual(g.bearer_post("/auth/email/verify-request", forged_token(self.user["uid"]), json_body={})[0], 200)


class IdentityAnswers(Base):
    def reply(self, status, message=None, count=1, match="signInWithPassword"):
        body = '{"error":{"code":%d,"message":"%s"}}' % (status, message or "SERVICE")
        support.LAB.relay_faults(reply={"status": status, "body": body, "count": count, "match": match})

    def test_identity_502_503_504_once_is_retried_and_succeeds(self):
        for code in (502, 503, 504):
            support.LAB.relay_reset()
            g = Gateway()
            before = pwf_global(g)
            self.reply(code)
            self.assertEqual(self.login(g)[0], 200, code)
            self.assertEqual(support.LAB.relay_stats()["replied"], 1)
            self.assertEqual(pwf_global(g), before)

    def test_identity_502_503_504_every_time_is_a_safe_503_with_the_status_as_cause(self):
        for code in (502, 503, 504):
            support.LAB.relay_reset()
            g = Gateway()
            before = pwf_global(g)
            self.reply(code, count=10)
            status, body, head = self.login(g)
            self.assertEqual((status, body, cause(head)), (503, UNAVAILABLE, "http_%d" % code))
            self.assertEqual(support.LAB.relay_stats()["replied"], 3)       # 1 attempt + 2 retries, then it gives up
            self.assertEqual(pwf_global(g), before)                         # never counted as a wrong password
            support.LAB.relay_reset()

    def test_identity_503_with_retries_disabled_is_not_retried(self):
        g = Gateway("retry0")
        self.reply(503, count=10)
        status, _, head = self.login(g)
        self.assertEqual((status, cause(head)), (503, "http_503"))
        self.assertEqual(support.LAB.relay_stats()["replied"], 1)

    def test_identity_400_credential_answer_is_not_retried_and_is_counted_as_a_failure(self):
        g = Gateway()
        before = pwf_global(g)
        self.reply(400, "INVALID_PASSWORD", count=10)
        status, body, _ = self.login(g)
        self.assertEqual((status, body), (400, {"error": "invalid-credential"}))
        self.assertEqual(support.LAB.relay_stats()["replied"], 1)           # exactly one attempt: no retry for a credential answer
        self.assertEqual(pwf_global(g), before + 1)

    def test_identity_400_or_401_that_is_not_about_credentials_is_a_service_error_not_retried_not_counted(self):
        for code, message in ((400, "API_KEY_INVALID"), (401, "UNAUTHENTICATED"), (403, "PERMISSION_DENIED")):
            support.LAB.relay_reset()
            g = Gateway()
            before = pwf_global(g)
            self.reply(code, message, count=10)
            status, body, head = self.login(g)
            self.assertEqual((status, body, cause(head)), (503, UNAVAILABLE, "http_4xx"), code)
            self.assertEqual(support.LAB.relay_stats()["replied"], 1, code)     # never retried
            self.assertEqual(pwf_global(g), before, code)                       # a permission problem is not a wrong password
            support.LAB.relay_reset()

    def test_identity_throttle_is_a_429_and_not_counted(self):
        g = Gateway()
        before = pwf_global(g)
        self.reply(400, "TOO_MANY_ATTEMPTS_TRY_LATER", count=1)
        status, body, head = self.login(g)
        self.assertEqual((status, body), (429, {"error": "too-many-requests"}))
        self.assertIn("Retry-After", {k.title() for k in head})
        self.assertEqual(pwf_global(g), before)


class Timeouts(Base):
    def test_a_delayed_identity_service_is_slow_but_correct_and_not_retried(self):
        g = Gateway()
        g.diag_reset()
        support.LAB.relay_faults(delay_ms=2500)
        started = time.monotonic()
        status, _, _ = self.login(g)
        self.assertEqual(status, 200)
        self.assertGreaterEqual(time.monotonic() - started, 2.4)
        self.assertFalse([k for k in g.diag()["gateway"]["counts"] if k.startswith("retry|")])

    def test_a_paused_emulator_hits_the_request_deadline_with_a_safe_503_and_recovers(self):
        g = Gateway("deadline")                                         # 4 s overall deadline on this variant
        before = pwf_global(g)
        support.LAB.pause_emulator()
        try:
            started = time.monotonic()
            status, body, head = self.login(g)
            elapsed = time.monotonic() - started
        finally:
            support.LAB.resume_emulator()
        self.assertEqual((status, body, cause(head)), (503, UNAVAILABLE, "timeout"))
        self.assertLess(elapsed, 7.0)
        self.assertEqual(pwf_global(g), before)                         # the interrupted attempt was given back, not counted
        self.assertEqual(self.login(g)[0], 200)                         # after the emulator resumes, the same credentials work

    def test_a_paused_emulator_with_the_default_deadline_ends_as_read_timeout_not_retried(self):
        g = Gateway()
        before = pwf_global(g)
        support.LAB.pause_emulator()
        try:
            started = time.monotonic()
            status, body, head = self.login(g)
            elapsed = time.monotonic() - started
        finally:
            support.LAB.resume_emulator()
        self.assertEqual((status, body), (503, UNAVAILABLE))
        self.assertEqual(cause(head), "read_timeout")
        self.assertLess(elapsed, 14.0)                                  # one 8 s attempt, no retry of a hanging call
        self.assertEqual(pwf_global(g), before)

    def test_a_signup_that_runs_out_of_time_before_the_account_exists_creates_nothing(self):
        g = Gateway("deadline")
        email, password = unique("slow"), new_password()
        proof = g.proof(email)
        g.set_faults(slow_create_user_s=6)
        try:
            status, body, head = g.call("signup", {"email": email, "password": password}, proof=proof)
        finally:
            g.clear_faults()
        self.assertEqual((status, body, cause(head)), (503, UNAVAILABLE, "timeout"))
        with self.assertRaises(auth.UserNotFoundError):
            auth.get_user_by_email(email, app=support.ADMIN)
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims"), "0")
        self.assertEqual(g.call("signup", {"email": email, "password": password}, proof=proof)[0], 200)   # the proof was not burned


class DatabaseFaults(Base):
    def test_postgres_down_gives_503_with_a_db_cause_never_a_credential_error_and_recovers(self):
        g = Gateway()
        envelope = g.seal("login", {"email": self.user["email"], "password": self.user["password"]})
        support.LAB.stop_postgres()
        statuses = []
        try:
            status, body, head = g.request("POST", "/auth/enc/login", envelope.encode(), {"Content-Type": "application/json"})
            statuses.append((status, cause(head)))
            status2, body2, head2 = g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})
            statuses.append((status2, cause(head2)))
        finally:
            support.LAB.start_postgres()
        for status, c in statuses:
            self.assertEqual(status, 503)
            self.assertIn(c, {"db_unavailable", "db_timeout", "pool"})
        self.assertEqual(body, UNAVAILABLE)
        deadline, ok = time.time() + 40, False
        while time.time() < deadline and not ok:                         # the pool reconnects by itself
            ok = self.login(g)[0] == 200
            if not ok:
                time.sleep(1)
        self.assertTrue(ok, "gateway did not recover after PostgreSQL came back")

    def test_database_pool_saturation_is_a_pool_cause_503_and_ends_when_connections_are_free(self):
        g = Gateway("poolq")                                             # waits at most 1 s for a connection
        self.assertEqual(g.post_json("/__g6b/hold_pool", {"seconds": 6})[0], 200)
        started = time.monotonic()
        status, body, head = g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})
        self.assertEqual((status, body, cause(head)), (503, UNAVAILABLE, "pool"))
        self.assertLess(time.monotonic() - started, 4.0)
        time.sleep(6)
        self.assertEqual(g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})[0], 200)

    def test_an_unreachable_database_host_is_a_db_cause_503(self):
        g = Gateway("nodb")
        status, body, head = g.request("POST", "/auth/enc/nonce", b'{"op":"login"}', {"Content-Type": "application/json"})
        self.assertEqual((status, body), (503, UNAVAILABLE))
        self.assertIn(cause(head), {"db_unavailable", "db_timeout", "pool"})


class NoAutomaticRetryOfSideEffects(Base):
    """Account creation, password update, revocation and token minting are never repeated automatically."""

    def setUp(self):
        super().setUp()
        self.g = Gateway()
        self.session = session_from(self.login(self.g)[1]["custom_token"])
        self.g.diag_reset()

    def tearDown(self):
        self.g.clear_faults()
        super().tearDown()

    def test_create_user_is_attempted_exactly_once_even_when_it_fails(self):
        email = unique("once")
        proof = self.g.proof(email)
        self.g.set_faults(create_user=True)
        status, body, head = self.g.call("signup", {"email": email, "password": new_password()}, proof=proof)
        self.assertEqual((status, body), (503, UNAVAILABLE))
        self.assertEqual(self.g.diag()["calls"]["create_user"], 1)

    def test_a_failed_password_update_is_attempted_once_and_reported_as_unknown(self):
        self.g.set_faults(update_password=True)
        new = new_password()
        status, body, head = self.g.call("change_password", {"email": self.user["email"], "password": self.user["password"], "new_password": new},
                                         bearer=self.session["idToken"])
        self.assertEqual((status, body), (503, {**UNAVAILABLE, "password_changed": "unknown"}))
        self.assertEqual(cause(head), "sdk_error")
        self.assertEqual(self.g.diag()["calls"]["update_password"], 1)
        self.g.clear_faults()
        self.assertEqual(self.login(self.g)[0], 200)                    # nothing changed

    def test_a_lost_update_response_is_unknown_not_unchanged_and_the_password_really_changed(self):
        self.g.set_faults(update_then_fail=True)
        new = new_password()
        status, body, _ = self.g.call("change_password", {"email": self.user["email"], "password": self.user["password"], "new_password": new},
                                      bearer=self.session["idToken"])
        self.g.clear_faults()
        self.assertEqual((status, body.get("password_changed")), (503, "unknown"))
        self.assertEqual(self.g.diag()["calls"]["update_password"], 1)
        self.assertEqual((self.g.login(self.user["email"], new)[0], self.g.login(self.user["email"], self.user["password"])[0]), (200, 400))

    def test_a_failed_revocation_is_not_repeated_and_still_returns_a_session_and_says_so(self):
        self.g.set_faults(revoke_refresh_tokens=True)
        status, body, _ = self.g.call("change_password", {"email": self.user["email"], "password": self.user["password"],
                                                           "new_password": new_password()}, bearer=self.session["idToken"])
        self.g.clear_faults()
        self.assertEqual((status, body["password_changed"], body["sessions_revoked"], bool(body["custom_token"])), (200, True, False, True))
        self.assertEqual(self.g.diag()["calls"]["revoke"], 1)

    def test_a_failed_token_mint_is_not_repeated_and_is_a_distinct_safe_outcome(self):
        self.g.set_faults(mint_custom_token=True)
        status, body, _ = self.g.call("change_password", {"email": self.user["email"], "password": self.user["password"],
                                                           "new_password": new_password()}, bearer=self.session["idToken"])
        self.g.clear_faults()
        self.assertEqual((status, body["password_changed"], body["custom_token"], body["retry_signin"]), (200, True, None, True))
        self.assertEqual(self.g.diag()["calls"]["mint"], 1)


class Diagnostics(Base):
    def test_every_response_carries_a_random_request_id_and_it_matches_the_log_line(self):
        g = Gateway("retry0")
        support.LAB.relay_faults(drop_first=1)
        status, _, head = self.login(g)
        rid = request_id(head)
        self.assertEqual(status, 503)
        self.assertRegex(rid, r"^[0-9a-f]{12}$")
        line = [l for l in support.LAB.logs("retry0").splitlines() if "rid=" + rid in l]
        self.assertEqual(len(line), 1)
        self.assertRegex(line[0], r"op=login outcome=unavailable stage=verify_password cause=reset")
        ok_status, _, ok_head = self.login(g)
        self.assertNotEqual(request_id(ok_head), rid)

    def test_stage_timings_and_cause_counters_are_available_and_contain_no_values(self):
        g = Gateway()
        self.login(g)
        snap = g.diag()["gateway"]
        self.assertGreaterEqual(snap["timings"]["verify_password"]["n"], 1)
        text = str(snap)
        for forbidden in (self.user["email"], self.user["password"], self.user["uid"]):
            self.assertNotIn(forbidden, text)


class ZZFinal(unittest.TestCase):
    """Runs last (classes run alphabetically): looks at every 503 the whole module provoked."""

    def test_every_503_answered_in_this_module_had_a_request_id_and_a_safe_cause(self):
        self.assertGreater(len(support.UNAVAILABLE_RESPONSES), 10)
        for path, head in support.UNAVAILABLE_RESPONSES:
            self.assertRegex(request_id(head) or "", r"^[0-9a-f]{12}$", path)
            self.assertIn(cause(head), SAFE_CAUSES, path)



if __name__ == "__main__":
    unittest.main()
