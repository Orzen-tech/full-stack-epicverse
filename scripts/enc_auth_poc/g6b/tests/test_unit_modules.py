"""Host-side unit tests of the PURE G6b modules (no database, no Firebase, no network): bounded retry, limiter decision logic, diagnostics.
Dummy values only; the modules are loaded from the built G6b tree."""
import asyncio
import random
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tree import load_module  # noqa: E402

retry = load_module("app/services/enc_auth_retry.py", "g6b_retry")
limits = load_module("app/services/enc_auth_limits_eval.py", "g6b_limits")
diag = load_module("app/services/enc_auth_diag.py", "g6b_diag")


class Transient(Exception):
    cause = "http_503"


class Permanent(Exception):
    cause = "http_4xx"


class FakeTime:
    """A virtual clock and sleep, so the budget logic is tested without real waiting."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run(coro):
    return asyncio.run(coro)


class Retry(unittest.TestCase):
    def go(self, outcomes, max_retries=2, budget_ms=1500, duration=0.0):
        ft, calls, notes = FakeTime(), [], []
        queue = list(outcomes)

        async def call():
            calls.append(1)
            ft.now += duration
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        async def main():
            return await retry.retrying(call, stage="verify_password", is_transient=lambda e: isinstance(e, Transient), max_retries=max_retries,
                                        budget_ms=budget_ms, note=lambda *a: notes.append(a), sleep=ft.sleep, clock=ft.clock, rand=lambda: 0.5)
        return main, calls, ft, notes

    def test_success_needs_no_retry(self):
        main, calls, ft, _ = self.go(["ok"])
        self.assertEqual(run(main()), "ok")
        self.assertEqual((len(calls), ft.sleeps), (1, []))

    def test_one_transient_failure_is_retried_once(self):
        main, calls, ft, notes = self.go([Transient(), "ok"])
        self.assertEqual(run(main()), "ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual([n[0] for n in notes], ["retry", "retry_success"])

    def test_at_most_two_retries_then_the_failure_is_raised(self):
        main, calls, ft, _ = self.go([Transient(), Transient(), Transient(), "never"])
        with self.assertRaises(Transient):
            run(main())
        self.assertEqual(len(calls), 3)                                  # 1 attempt + 2 retries
        self.assertEqual(len(ft.sleeps), 2)
        self.assertLess(sum(ft.sleeps), 0.5)                              # delays are short

    def test_permanent_errors_are_never_retried(self):
        main, calls, ft, _ = self.go([Permanent(), "never"])
        with self.assertRaises(Permanent):
            run(main())
        self.assertEqual((len(calls), ft.sleeps), (1, []))

    def test_zero_retries_configured(self):
        main, calls, _, _ = self.go([Transient(), "never"], max_retries=0)
        with self.assertRaises(Transient):
            run(main())
        self.assertEqual(len(calls), 1)

    def test_a_slow_attempt_is_not_retried_when_the_budget_is_used_up(self):
        main, calls, ft, _ = self.go([Transient(), "never"], duration=1.45)       # the first attempt alone took 1.45 s
        with self.assertRaises(Transient):
            run(main())
        self.assertEqual((len(calls), ft.sleeps), (1, []))

    def test_total_effort_stays_inside_the_budget(self):
        main, calls, ft, _ = self.go([Transient(), Transient(), Transient(), "never"], duration=0.6)
        with self.assertRaises(Transient):
            run(main())
        self.assertLessEqual(ft.now * 1000.0, 1500 + 1)
        self.assertLessEqual(len(calls), 3)

    def test_jitter_bounds(self):
        for r in (0.0, 0.999):
            ft = FakeTime()
            queue = [Transient(), "ok"]

            async def call():
                item = queue.pop(0)
                if isinstance(item, Exception):
                    raise item
                return item

            async def main():
                return await retry.retrying(call, stage="get_account", is_transient=lambda e: True, max_retries=2, budget_ms=1500,
                                            sleep=ft.sleep, clock=ft.clock, rand=lambda: r)
            run(main())
            self.assertGreaterEqual(ft.sleeps[0], 0.08)
            self.assertLessEqual(ft.sleeps[0], 0.12)


class LimitDecisions(unittest.TestCase):
    LIM = {"pair": 5, "ip": 20, "email": 30, "global": 2000}

    def hits(self, **kw):
        base = {"pair": 1, "ip": 1, "email": 1, "global": 1}
        base.update(kw)
        return base

    def test_normal_use_is_not_blocked(self):
        self.assertIsNone(limits.blocked_scope(self.hits(), self.LIM, 2))

    def test_pair_limit_blocks_only_the_sixth_attempt(self):
        self.assertIsNone(limits.blocked_scope(self.hits(pair=5), self.LIM, 2))
        self.assertEqual(limits.blocked_scope(self.hits(pair=6), self.LIM, 2), "pair")

    def test_ip_limit_and_email_limit(self):
        self.assertEqual(limits.blocked_scope(self.hits(ip=21), self.LIM, 2), "ip")
        self.assertEqual(limits.blocked_scope(self.hits(email=31), self.LIM, 2), "email")
        self.assertIsNone(limits.blocked_scope(self.hits(email=30, ip=20, pair=5), self.LIM, 2))

    def test_a_victim_needs_failures_from_several_addresses_to_be_locked_out(self):
        # one attacker address is stopped at 5 (pair) and can never reach the e-mail limit alone
        self.assertLess(self.LIM["pair"], self.LIM["email"])
        self.assertGreaterEqual(self.LIM["email"] // self.LIM["pair"], 6)

    def test_abuse_mode_lowers_only_the_per_ip_limit(self):
        self.assertIsNone(limits.blocked_scope(self.hits(ip=3), self.LIM, 2))                        # normal mode: 3 failures from one IP are fine
        abuse = self.hits(ip=3, **{"global": 2001})
        self.assertEqual(limits.blocked_scope(abuse, self.LIM, 2), "abuse_ip")             # abuse mode: that IP is restricted
        self.assertIsNone(limits.blocked_scope(self.hits(ip=2, **{"global": 2001}), self.LIM, 2))   # a clean address still gets through
        self.assertEqual(limits.effective_ip_limit(self.hits(**{"global": 2001}), self.LIM, 2), 2)
        self.assertEqual(limits.effective_ip_limit(self.hits(), self.LIM, 2), 20)


class Diagnostics(unittest.TestCase):
    def setUp(self):
        diag.reset()

    def test_request_ids_are_random_hex_and_unrelated_to_content(self):
        ids = {diag.new_request_id() for _ in range(200)}
        self.assertEqual(len(ids), 200)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids))

    def test_log_lines_use_only_the_closed_vocabularies(self):
        diag.new_request_id()
        line = diag.line("login", "unavailable", "verify_password", "http_503", 41)
        self.assertRegex(line, r"^\[ENC-AUTH\] rid=[0-9a-f]{12} op=login outcome=unavailable stage=verify_password cause=http_503 ms=41$")

    def test_a_value_that_is_not_a_code_cannot_reach_the_log_or_the_counters(self):
        secret = "someone@example.invalid"
        line = diag.line(secret, secret, secret, secret)
        self.assertNotIn(secret, line)
        self.assertIn("op=other outcome=other", line)
        diag.note("unavailable", secret, secret)
        diag.note(secret, "db", "pool")
        diag.observe(secret, 3.0)
        self.assertNotIn(secret, repr(diag.snapshot()))

    def test_counts_and_timings(self):
        diag.note("unavailable", "db", "pool")
        diag.note("unavailable", "db", "pool")
        diag.observe("verify_password", 10.0)
        diag.observe("verify_password", 30.0)
        snap = diag.snapshot()
        self.assertEqual(snap["counts"]["unavailable|db|pool"], 2)
        self.assertEqual(snap["timings"]["verify_password"], {"n": 2, "avg_ms": 20.0, "max_ms": 30.0})

    def test_every_cause_code_is_a_plain_word(self):
        for code in diag.CAUSES | diag.STAGES:
            self.assertRegex(code, r"^[a-z0-9_-]+$")


if __name__ == "__main__":
    unittest.main()
