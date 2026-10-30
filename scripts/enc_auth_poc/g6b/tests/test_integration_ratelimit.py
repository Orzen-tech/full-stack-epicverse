"""G6b integration tests (3/3): the password-failure rate-limit policy (anti lock-out DoS).

Policy under test (see README for the exact numbers and trade-offs): counters over fixed windows, all counted BEFORE a password is
verified and given back unless the identity service really answered "wrong credential":
    pair   e-mail+IP   5     ip    one IP, all e-mails   20     email  one e-mail, all IPs   30     global  2000 -> ABUSE MODE: per-IP limit 2
The `policy` variant runs with these DEFAULTS; the `scaled` variant uses small numbers (pair 3, ip 6, email 8, global 14, abuse-ip 2) so
that the cross-IP and global behaviour can be driven in seconds; `shortwin` has a 6-second window to test expiry.
Emulator-based; dummy accounts only. The client IP is the X-Forwarded-For value the lab sets (the deployed `client_ip` helper).
"""
import concurrent.futures
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import Gateway, LabTestCase, claims, make_user, new_password, session_from, unique  # noqa: E402

WRONG = (400, {"error": "invalid-credential"})


def setUpModule():
    support.start(["policy", "scaled", "shortwin", "main"])


def tearDownModule():
    support.stop()


def hits(g, scope):
    return int(g.sql("SELECT coalesce(sum(hits),0) FROM enc_auth_limits WHERE scope='%s'" % scope))


class Base(LabTestCase):
    backend_for_diag = "scaled"

    def gateway(self, backend, ip=None):
        g = Gateway(backend, ip=ip)
        support.SECRETS.add(g.ip)                    # an IP address must not appear in any log or error body either
        return g

    def user(self):
        user = make_user()
        support.SECRETS.add(user["email"])           # nor an e-mail address
        return user

    def wrong(self, g, user):
        return g.login(user["email"], new_password())

    def good(self, g, user):
        return g.login(user["email"], user["password"])

    def reset(self, backend):
        Gateway(backend).sql("TRUNCATE enc_auth_limits")


class DefaultPolicy(Base):
    def setUp(self):
        self.reset("policy")

    def test_five_wrong_passwords_from_one_ip_are_allowed_the_sixth_attempt_is_refused_but_other_ips_still_log_in(self):
        user, attacker, victim = self.user(), self.gateway("policy"), self.gateway("policy")
        for i in range(5):
            self.assertEqual(self.wrong(attacker, user)[:2], WRONG, i)
        status, body, head = self.good(attacker, user)                # even the CORRECT password is refused from the exhausted pair
        self.assertEqual((status, body), (429, {"error": "too-many-requests"}))
        self.assertGreaterEqual(int({k.title(): v for k, v in head.items()}["Retry-After"]), 1)
        self.assertEqual(self.good(victim, user)[0], 200)             # the real owner, on another network, is NOT locked out
        self.assertEqual(self.wrong(victim, user)[:2], WRONG)         # and is still judged on the password alone

    def test_the_default_thresholds_are_the_documented_ones(self):
        g = self.gateway("policy")
        user = self.user()
        for _ in range(5):
            self.wrong(g, user)
        self.assertEqual(hits(g, "pwf_pair"), 5)
        self.assertEqual(self.wrong(g, user)[0], 429)
        self.assertEqual(hits(g, "pwf_pair"), 5)                      # a refused attempt is given back at once


class ScaledPolicy(Base):
    def setUp(self):
        self.reset("scaled")

    def test_failures_spread_over_several_addresses_cap_at_the_per_email_limit_and_a_few_addresses_cannot_lock_the_owner_out(self):
        user = self.user()
        gs = [self.gateway("scaled") for _ in range(6)]
        for g in gs[:2]:                                              # 2 attacker addresses x 3 failures = 6 < 8 (email limit)
            for _ in range(3):
                self.assertEqual(self.wrong(g, user)[:2], WRONG)
        self.assertEqual(self.good(gs[5], user)[0], 200)              # the owner is still fine
        for _ in range(2):                                            # a third attacker address brings the total to 8
            self.assertEqual(self.wrong(gs[2], user)[:2], WRONG)
        self.assertEqual(self.wrong(gs[3], user)[0], 429)             # the ninth failure anywhere is refused (email aggregate)
        self.assertEqual(hits(gs[0], "pwf_email"), 8)                 # refused attempts did not push the counter further
        # TRADE-OFF (documented): once the aggregate is exhausted the owner is refused too, until the window ends. Reaching it needed
        # failures from 4 different addresses here (6 with the default 30/5 thresholds), not from one.
        self.assertEqual(self.good(gs[5], user)[0], 429)

    def test_one_ip_spraying_many_accounts_is_stopped_at_the_per_ip_limit_other_ips_are_unaffected(self):
        sprayer, other = self.gateway("scaled"), self.gateway("scaled")
        users = [self.user() for _ in range(8)]
        for u in users[:6]:
            self.assertEqual(self.wrong(sprayer, u)[:2], WRONG)
        self.assertEqual(self.wrong(sprayer, users[6])[0], 429)       # seventh failure from this IP, on a fresh account
        self.assertEqual(self.good(other, users[6])[0], 200)          # that account's owner on another network: fine

    def test_alternating_login_signup_recovery_reauth_and_change_password_share_one_counter(self):
        user, g = self.user(), self.gateway("scaled")
        proof = g.proof(user["email"])
        session = session_from(Gateway("scaled").login(user["email"], user["password"])[1]["custom_token"])
        bearer = session["idToken"]
        steps = [
            ("login", lambda: g.login(user["email"], new_password())),
            ("reauth", lambda: g.call("reauth", {"email": user["email"], "password": new_password()}, bearer=bearer)),
            ("change_password", lambda: g.call("change_password", {"email": user["email"], "password": new_password(),
                                                                    "new_password": new_password()}, bearer=bearer)),
        ]
        for name, step in steps:                                      # 3 failures through three different operations
            self.assertEqual(step()[:2], WRONG, name)
        self.assertEqual(g.call("signup", {"email": user["email"], "password": new_password()}, proof=proof)[0], 429)   # signup recovery
        self.assertEqual(g.login(user["email"], user["password"])[0], 429)
        self.assertEqual(g.call("reauth", {"email": user["email"], "password": user["password"]}, bearer=bearer)[0], 429)
        self.assertEqual(hits(g, "pwf_pair"), 3)
        self.assertEqual(self.good(self.gateway("scaled"), user)[0], 200)

    def test_signup_recovery_failures_count_like_login_failures(self):
        user, g = self.user(), self.gateway("scaled")
        proof = g.proof(user["email"])
        for _ in range(3):
            self.assertEqual(g.call("signup", {"email": user["email"], "password": new_password()}, proof=proof)[:2],
                             (400, {"error": "email-already-in-use"}))
        self.assertEqual(g.login(user["email"], user["password"])[0], 429)

    def test_global_abuse_mode_restricts_addresses_that_already_failed_but_not_clean_ones(self):
        victim = self.user()
        repeat = self.gateway("scaled")
        for _ in range(2):                                            # this address already failed twice (normal ip limit is 6)
            self.assertEqual(self.wrong(repeat, victim)[:2], WRONG)
        for _ in range(13):                                           # 15 failures overall -> above the global limit of 14
            self.assertEqual(self.wrong(self.gateway("scaled"), self.user())[:2], WRONG)
        self.assertGreater(hits(repeat, "pwf_global"), 14)
        self.assertEqual(self.wrong(repeat, victim)[0], 429)          # ABUSE MODE: a third failure from that address is refused (limit 2)
        clean = self.gateway("scaled")
        self.assertEqual(self.good(clean, victim)[0], 200)            # a clean address with the right password still gets in
        clean_wrong = self.gateway("scaled")
        self.assertEqual(self.wrong(clean_wrong, self.user())[:2], WRONG)   # ... and may fail once or twice like anyone

    def test_a_blocked_flood_does_not_extend_the_counters(self):
        user, g = self.user(), self.gateway("scaled")
        for _ in range(3):
            self.wrong(g, user)
        for _ in range(12):
            self.assertEqual(self.wrong(g, user)[0], 429)
        self.assertEqual((hits(g, "pwf_pair"), hits(g, "pwf_ip"), hits(g, "pwf_email"), hits(g, "pwf_global")), (3, 3, 3, 3))

    def test_concurrent_wrong_guesses_cannot_overshoot_the_limit(self):
        user, g = self.user(), self.gateway("scaled")
        g.diag_reset()
        envelopes = [g.seal("login", {"email": user["email"], "password": new_password()}) for _ in range(12)]
        headers = {"Content-Type": "application/json"}
        with concurrent.futures.ThreadPoolExecutor(12) as pool:
            replies = list(pool.map(lambda e: g.request("POST", "/auth/enc/login", e.encode(), headers), envelopes))
        statuses = sorted(r[0] for r in replies)
        self.assertEqual(statuses.count(400), 3, statuses)            # exactly the limit's worth of passwords was ever checked
        self.assertEqual(statuses.count(429), 9, statuses)
        self.assertEqual(g.diag()["calls"]["verify_password"], 3)     # the other nine never reached the identity service
        self.assertEqual(hits(g, "pwf_pair"), 3)

    def test_transport_failures_do_not_count_as_wrong_passwords(self):
        user, g = self.user(), self.gateway("scaled")
        support.LAB.relay_reset()
        support.LAB.relay_faults(refuse_for_s=60)
        try:
            for _ in range(4):                                        # more failures than the pair limit of 3
                status, body, _ = self.good(g, user)
                self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}))
        finally:
            support.LAB.relay_reset()
        self.assertEqual((hits(g, "pwf_pair"), hits(g, "pwf_ip"), hits(g, "pwf_email"), hits(g, "pwf_global")), (0, 0, 0, 0))
        self.assertEqual(self.good(g, user)[0], 200)                  # nobody was locked out by an outage
        self.assertEqual(hits(g, "pwf_pair"), 0)                      # a correct login leaves no trace

    def test_email_and_uid_mismatch_failures_are_counted_but_only_against_that_pair(self):
        a, b = self.user(), self.user()
        g, other = self.gateway("scaled"), self.gateway("scaled")
        session = session_from(Gateway("scaled").login(a["email"], a["password"])[1]["custom_token"])
        for _ in range(3):                                            # A's session with B's e-mail: never reaches a password check
            self.assertEqual(g.call("reauth", {"email": b["email"], "password": b["password"]}, bearer=session["idToken"])[:2], WRONG)
        self.assertEqual(self.good(g, b)[0], 429)                     # this address can no longer try B from here ...
        self.assertEqual(self.good(other, b)[0], 200)                 # ... but B's owner elsewhere is unaffected

    def test_limiter_records_hold_no_plaintext_email_or_address(self):
        user, g = self.user(), self.gateway("scaled")
        self.wrong(g, user)
        dump = g.sql("SELECT string_agg(t::text, ',') FROM enc_auth_limits t")
        self.assertNotIn(user["email"].lower(), dump.lower())
        self.assertNotIn(user["email"].split("@")[0].lower(), dump.lower())
        self.assertNotIn(g.ip, dump)
        rows = g.sql("SELECT scope || ' ' || length(key_hash) FROM enc_auth_limits WHERE scope LIKE 'pwf_%' ORDER BY scope").split("\n")
        self.assertEqual(rows, ["pwf_email 64", "pwf_global 64", "pwf_ip 64", "pwf_pair 64"])


class WindowExpiry(Base):
    def setUp(self):
        self.reset("shortwin")

    def test_after_the_window_the_counters_start_over_and_the_correct_password_works(self):
        user, g = self.user(), self.gateway("shortwin")
        for _ in range(3):
            self.assertEqual(self.wrong(g, user)[:2], WRONG)
        status, body, head = self.good(g, user)
        self.assertEqual(status, 429)
        retry = int({k.title(): v for k, v in head.items()}["Retry-After"])
        self.assertTrue(1 <= retry <= 6, retry)
        time.sleep(retry + 1.2)
        self.assertEqual(self.good(g, user)[0], 200)
        self.assertEqual(self.wrong(g, user)[:2], WRONG)               # a fresh allowance


class OtpLimiterUntouched(Base):
    def test_gateway_password_failures_never_write_to_the_existing_otp_limiter_table(self):
        g, user = self.gateway("main"), self.user()
        before = g.sql("SELECT count(*) FROM otp_send_rate_limits")
        for _ in range(3):
            self.wrong(g, user)
        self.assertEqual(self.good(g, user)[0], 200)
        self.assertEqual(g.sql("SELECT count(*) FROM otp_send_rate_limits"), before)
        self.assertGreater(int(g.sql("SELECT count(*) FROM enc_auth_limits")), 0)      # the gateway used its OWN table


if __name__ == "__main__":
    unittest.main()
