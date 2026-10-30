"""Stage A / B / C scripts and the account protection, run by the REAL G7 adapter and the REAL G8 egress/protection over the G7 fake Firebase.
Every fake operation charges the real quotas, so these tests measure the request count of each script. Offline; no network."""
import asyncio
import unittest
from unittest import mock

import _g8 as t
from _g8 import Lab
from _tree import ATTEMPTS, run

import g3_guard as g3
import g8_guard as guard
import g8_protect as protect
import g8_stages as stages
from g8_guard import LiveSafetyStop
from app.core.config import settings

B_CLASSES = ("create", "password", "revoke", "disable")


def run_a(lab, report=None):
    return run(stages.run_stage_a(lab.ctx, report))


def run_b(lab, classes=B_CLASSES, report=None):
    lab.confirm_all(*classes)
    return run(stages.run_stage_b(lab.ctx, report))


def stop_code(coro_factory):
    try:
        run(coro_factory())
    except LiveSafetyStop as stop:
        return stop.code
    return None


class StageA(unittest.TestCase):
    def test_clean_run_uses_exactly_eight_requests_and_changes_nothing(self):
        lab = Lab("A")
        before = lab.dummy_record()
        report = run_a(lab)
        self.assertTrue(report.clean(), report.render())
        self.assertEqual([c for c, _ in report.checks], ["A01", "A02", "A03", "A04", "A05", "A06", "A07"])
        self.assertEqual(report.requests, ({"sign_in": 2, "lookup": 2, "certs": 2, "sign_blob": 1, "custom_exchange": 1}, 8))
        self.assertEqual(lab.dummy_record(), before)
        self.assertEqual(lab.admin.mutated, [])
        self.assertEqual(lab.ops.mutated, [])
        self.assertEqual(lab.session.created_by_exchange, [])
        self.assertFalse(report.created_account)

    def test_every_mutating_method_refuses_in_stage_a_whatever_is_confirmed(self):
        lab = Lab("A")
        uid = lab.dummy[0]
        for cls in protect.CLASSES:
            self.assertEqual(stop_code(lambda: asyncio.sleep(0, lab.state.mutations.confirm(cls))), "mutation_not_allowed", cls)
        for call in (lambda: lab.identity.create_user("g8-0123456789abcdef@example.com", "Start-Passw0rd-1"),
                     lambda: lab.identity.update_password(uid, "Second-Passw0rd-2"), lambda: lab.identity.revoke_refresh_tokens(uid),
                     lambda: lab.ctx.ops.set_disabled(uid, True), lambda: lab.ctx.ops.delete_user(uid)):
            self.assertEqual(stop_code(call), "mutation_forbidden_stage_a")
        self.assertEqual(lab.egress.used(), ({}, 0))

    def test_stage_a_can_only_name_the_dummy_account(self):
        lab = Lab("A")
        self.assertEqual(stop_code(lambda: lab.identity.get_account("someoneelse")), "uid_not_the_dummy")
        self.assertEqual(stop_code(lambda: lab.identity.verify_password("other@example.com", "x" * 12)), "email_not_the_dummy")
        self.assertEqual(stop_code(lambda: lab.identity.mint_custom_token("dummyuid00000001")), "mint_for_unverified_uid")      # not yet looked up in this run
        self.assertEqual(lab.egress.used(), ({}, 0))

    def test_an_exchange_that_creates_an_account_is_a_critical_stop(self):
        class Creating(t.FakeSession):
            async def exchange(self, custom_token):
                outcome = await super().exchange(custom_token)
                outcome.is_new_user = True
                return outcome
        lab = Lab("A", session_cls=Creating)
        report = run_a(lab)
        self.assertEqual(report.stop_code, "custom_exchange_created_or_unknown_account")
        self.assertIn("account_created_unexpectedly", report.notes)
        self.assertFalse(report.clean())
        self.assertEqual(report.outcome("A06"), "not_run")

    def test_an_unknown_new_user_flag_is_also_a_stop(self):
        state = protect.RunState("A", guard.Egress("A", t.SIGNER), "u1", "e@example.net")
        for flag in (None, True, "false", 0):
            with self.assertRaises(LiveSafetyStop):
                state.assert_exchange_did_not_create(stages.SessionOutcome(True, is_new_user=flag))
        state.assert_exchange_did_not_create(stages.SessionOutcome(True, is_new_user=False))

    def test_a_wrong_dummy_password_stops_early_and_runs_nothing_else(self):
        lab = Lab("A")
        lab.ctx.secrets["dummy_password"] = "not-the-password"
        report = run_a(lab)
        self.assertEqual(report.outcome("A01"), "fail")
        self.assertEqual(report.counts()["not_run"], 6)
        self.assertEqual(lab.egress.used()[1], 1)
        self.assertFalse(report.clean())

    def test_an_admin_outage_is_ambiguous_never_a_pass_with_retries_off(self):
        lab = Lab("A")
        lab.world.fault("admin.get_user", "unavailable", "http_503", times=99)
        with mock.patch.object(settings, "ENC_AUTH_RETRY_MAX", 0):
            report = run_a(lab)
        self.assertEqual(report.outcome("A03"), "ambiguous")
        self.assertFalse(report.clean())

    def test_adapter_retries_would_exceed_the_stage_quota_and_stop_the_run(self):
        lab = Lab("A")
        lab.world.fault("admin.get_user", "unavailable", "http_503", times=99)
        with mock.patch.object(settings, "ENC_AUTH_RETRY_MAX", 2), mock.patch("asyncio.sleep", new=mock.AsyncMock()):
            report = run_a(lab)
        self.assertEqual(report.stop_code, "kind_quota_exhausted")           # the adapter's own retries are charged like any other request
        self.assertLessEqual(report.requests[1], 8)

    def test_cancellation_is_reported_and_propagates(self):
        lab = Lab("A")

        class Cancelling(t.FakeSession):
            async def exchange(self, custom_token):
                raise asyncio.CancelledError()
        lab.ctx.session = Cancelling(lab.world, lab.egress, lab.clock)
        report = stages.Report("A")
        with self.assertRaises(asyncio.CancelledError):
            run_a(lab, report)
        self.assertEqual(report.stop_code, "cancelled")
        self.assertTrue(report.requests[1] >= 4)

    def test_a_keyboard_interrupt_is_reported_and_propagates(self):
        lab = Lab("A")

        class Interrupted(t.FakeSession):
            async def exchange(self, custom_token):
                raise KeyboardInterrupt()
        lab.ctx.session = Interrupted(lab.world, lab.egress, lab.clock)
        report = stages.Report("A")
        with self.assertRaises(KeyboardInterrupt):
            run_a(lab, report)
        self.assertEqual(report.stop_code, "cancelled")


class StageB(unittest.TestCase):
    def test_clean_run_uses_exactly_the_stage_quota(self):
        lab = Lab("B", with_dummy=True)
        before = lab.dummy_record()
        report = run_b(lab)
        self.assertTrue(report.clean(), report.render())
        self.assertEqual(len(report.checks), 25)
        self.assertEqual(report.requests, ({"create": 2, "sign_in": 7, "lookup": 10, "update": 4, "refresh": 2, "sign_blob": 2, "custom_exchange": 2, "certs": 5}, 34))
        self.assertEqual(report.requests[0], guard.STAGE_QUOTAS["B"])
        self.assertEqual(lab.dummy_record(), before)                          # the pre-existing account was never touched
        created = lab.state.created_uid
        self.assertEqual([op for op, _ in lab.admin.mutated + lab.ops.mutated], ["create", "password", "revoke", "disable", "enable"])
        self.assertTrue(all(uid == created for _, uid in lab.admin.mutated + lab.ops.mutated))
        self.assertNotIn(lab.dummy[0], [uid for _, uid in lab.admin.mutated + lab.ops.mutated])

    def test_cleanup_is_not_executed_and_its_handle_is_reported(self):
        lab = Lab("B")
        report = run_b(lab)
        self.assertIn(lab.state.created_uid, lab.world.accounts)              # still there: deleting needs the separate cleanup run
        self.assertNotIn("delete", report.requests[0])
        self.assertEqual(report.cleanup_handle, "g8-0123456789abcdef")
        self.assertIn("cleanup_pending handle=g8-0123456789abcdef", report.render())
        self.assertTrue(report.created_account)

    def test_each_unconfirmed_class_stops_the_run_before_that_mutation(self):
        for missing in B_CLASSES:
            lab = Lab("B", with_dummy=True)
            report = run_b(lab, [c for c in B_CLASSES if c != missing])
            self.assertEqual(report.stop_code, "mutation_not_confirmed", missing)
            self.assertFalse(report.clean())
            if missing == "create":
                self.assertEqual(len(lab.world.accounts), 1)                  # only the pre-existing account: nothing was created
                self.assertFalse(report.created_account)
            if missing == "password":
                self.assertEqual(lab.world.accounts[lab.state.created_uid]["password"], "Start-Passw0rd-1")
            if missing == "revoke":
                self.assertEqual(lab.world.accounts[lab.state.created_uid]["valid_since"], 0)
            if missing == "disable":
                self.assertFalse(lab.world.accounts[lab.state.created_uid]["disabled"])

    def test_stage_b_is_never_given_the_dummy_identity(self):
        egress = guard.Egress("B", t.SIGNER)
        for args in (("uid", None), (None, "e@example.net"), ("uid", "e@example.net")):
            self.assertEqual(stop_code(lambda: asyncio.sleep(0, protect.RunState("B", egress, *args))), "dummy_identity_not_allowed_in_stage_b")

    def test_a_confirmed_stage_b_still_cannot_mutate_anything_but_its_own_account(self):
        lab = Lab("B", with_dummy=True)
        lab.confirm_all(*B_CLASSES)
        uid = run(lab.identity.create_user("g8-0123456789abcdef@example.com", "Start-Passw0rd-1"))
        for other in (lab.dummy[0], "someoneelse", "", None):
            self.assertEqual(stop_code(lambda: lab.identity.update_password(other, "Second-Passw0rd-2")), "mutation_target_not_disposable")
            self.assertEqual(stop_code(lambda: lab.identity.revoke_refresh_tokens(other)), "mutation_target_not_disposable")
            self.assertEqual(stop_code(lambda: lab.ctx.ops.set_disabled(other, True)), "mutation_target_not_disposable")
        self.assertEqual(stop_code(lambda: lab.identity.get_account(lab.dummy[0])), "uid_not_disposable")
        self.assertEqual(stop_code(lambda: lab.identity.verify_password(lab.dummy[1], "x" * 12)), "email_not_disposable")
        self.assertEqual(lab.ops.mutated, [])
        self.assertEqual(lab.admin.mutated, [("create", uid)])

    def test_account_creation_is_limited_to_one_account_and_one_duplicate_probe(self):
        lab = Lab("B")
        lab.confirm_all(*B_CLASSES)
        email, password = "g8-0123456789abcdef@example.com", "Start-Passw0rd-1"
        self.assertEqual(stop_code(lambda: lab.identity.create_user("someone@example.com", password)), "email_not_disposable_shape")
        self.assertEqual(stop_code(lambda: lab.identity.create_user("g8-0123456789ABCDEF@example.com", password)), "email_not_disposable_shape")
        self.assertEqual(stop_code(lambda: lab.identity.create_user("g8-0123456789abcdef@example.org", password)), "email_not_disposable_shape")
        run(lab.identity.create_user(email, password))
        self.assertEqual(stop_code(lambda: lab.identity.create_user("g8-fedcba9876543210@example.com", password)), "second_create_with_other_email")
        with self.assertRaises(t.base.EmailExists):
            run(lab.identity.create_user(email, password))
        self.assertEqual(stop_code(lambda: lab.identity.create_user(email, password)), "create_calls_exhausted")
        self.assertEqual(len(lab.world.accounts), 1)

    def test_a_duplicate_create_that_succeeds_is_a_stop(self):
        class Inner:
            def __init__(self):
                self.uids = iter(["uidaaaa1", "uidbbbb2"])

            async def create_user(self, email, password):
                return next(self.uids)
        egress = guard.Egress("B", t.SIGNER)
        state = protect.RunState("B", egress)
        state.mutations.confirm("create")
        identity = protect.GuardedIdentity(Inner(), state)
        run(identity.create_user("g8-0123456789abcdef@example.com", "Start-Passw0rd-1"))
        self.assertEqual(stop_code(lambda: identity.create_user("g8-0123456789abcdef@example.com", "Start-Passw0rd-1")), "duplicate_create_succeeded")

    def test_creation_outcome_unknown_still_reports_the_cleanup_handle(self):
        lab = Lab("B")
        lab.world.fault("admin.create_user", "unavailable", "http_503", times=1)
        report = run_b(lab)
        self.assertEqual(report.outcome("B01"), "fail")
        self.assertEqual(report.cleanup_handle, "g8-0123456789abcdef")
        self.assertEqual(report.counts()["not_run"], 24)
        self.assertFalse(report.clean())

    def test_a_password_update_outage_is_ambiguous_not_a_pass(self):
        lab = Lab("B")
        lab.world.fault("admin.set_password", "unavailable", "http_503", times=1)
        report = run_b(lab)
        self.assertEqual(report.outcome("B08"), "ambiguous")
        self.assertFalse(report.clean())

    def test_a_revoke_outage_makes_every_dependent_revocation_check_ambiguous(self):
        lab = Lab("B")
        lab.world.fault("admin.revoke", "unavailable", "http_503", times=1)
        report = run_b(lab)
        self.assertEqual([report.outcome(c) for c in ("B11", "B12", "B13", "B15")], ["ambiguous"] * 4)
        self.assertFalse(report.clean())

    def test_same_second_revocation_is_reported_ambiguous_never_silently_a_pass(self):
        lab = Lab("B", step=0.0)                                              # nothing takes time: the token and the revoke share a second
        lab.ctx.pre_revoke_wait = 0
        report = run_b(lab)
        self.assertEqual(report.outcome("B12"), "ambiguous")
        self.assertEqual(report.outcome("B13"), "ambiguous")
        self.assertEqual(report.outcome("B15"), "pass")                       # after the settle delay the new token is provably newer
        self.assertEqual(report.counts()["fail"], 0)
        self.assertFalse(report.clean())
        self.assertIn("ambiguous", report.render())

    def test_a_revocation_that_does_not_take_effect_is_a_failure(self):
        lab = Lab("B", ignore_revoke=True)
        report = run_b(lab)
        self.assertEqual(report.outcome("B12"), "fail")
        self.assertEqual(report.outcome("B13"), "fail")
        self.assertFalse(report.clean())

    def test_a_disabled_account_that_gets_a_session_is_a_failure(self):
        lab = Lab("B", session_cls=t.LenientSession)
        report = run_b(lab)
        self.assertEqual(report.outcome("B22"), "fail")
        self.assertFalse(report.clean())

    def test_with_the_default_waits_even_a_zero_latency_run_has_no_ambiguity(self):
        lab = Lab("B", step=0.0)
        report = run_b(lab)
        self.assertTrue(report.clean(), report.render())

    def test_the_settle_delay_is_taken_between_revoke_and_the_fresh_sign_in(self):
        lab = Lab("B")
        run_b(lab)
        self.assertEqual(lab.slept, [stages.SETTLE_SECONDS, stages.SETTLE_SECONDS])        # before the revoke, and between the revoke and the fresh sign-in

    def test_cancellation_mid_run_reports_the_handle_and_propagates(self):
        lab = Lab("B")
        report = stages.Report("B")

        async def cancelled(seconds):
            raise asyncio.CancelledError()
        lab.ctx.sleep = cancelled
        with self.assertRaises(asyncio.CancelledError):
            run_b(lab, report=report)
        self.assertEqual(report.stop_code, "cancelled")
        self.assertEqual(report.cleanup_handle, "g8-0123456789abcdef")
        self.assertIn(lab.state.created_uid, lab.world.accounts)

    def test_keyboard_interrupt_mid_run_is_handled_the_same_way(self):
        lab = Lab("B")
        report = stages.Report("B")

        async def interrupted(seconds):
            raise KeyboardInterrupt()
        lab.ctx.sleep = interrupted
        with self.assertRaises(KeyboardInterrupt):
            run_b(lab, report=report)
        self.assertEqual((report.stop_code, report.cleanup_handle), ("cancelled", "g8-0123456789abcdef"))

    def test_the_run_stops_when_the_stage_quota_would_be_exceeded(self):
        lab = Lab("B")
        lab.egress._quota["lookup"] = 3
        lab.egress.total_cap = 34 - 7
        report = run_b(lab)
        self.assertEqual(report.stop_code, "kind_quota_exhausted")
        self.assertLessEqual(report.requests[1], 27)
        self.assertFalse(report.clean())

    def test_a_token_minted_for_an_unverified_uid_is_never_exchanged(self):
        lab = Lab("B")
        lab.confirm_all(*B_CLASSES)
        self.assertEqual(stop_code(lambda: lab.identity.mint_custom_token("neverseen1")), "mint_for_unverified_uid")
        self.assertEqual(lab.egress.used(), ({}, 0))


class StageC(unittest.TestCase):
    def after_b(self):
        lab_b = Lab("B", with_dummy=True)
        run_b(lab_b)
        lab_c = Lab("C", world=lab_b.world, clock=lab_b.clock, with_dummy=True, protect_dummy=True)
        return lab_b, lab_c

    def test_cleanup_deletes_only_the_disposable_account_with_two_lookups_and_one_delete(self):
        lab_b, lab_c = self.after_b()
        before = lab_c.dummy_record()
        lab_c.confirm_all("delete")
        report = run(stages.run_stage_c(lab_c.ctx))
        self.assertTrue(report.clean(), report.render())
        self.assertEqual(report.requests, ({"lookup": 2, "delete": 1}, 3))
        self.assertNotIn(lab_b.state.created_uid, lab_c.world.accounts)
        self.assertEqual(lab_c.dummy_record(), before)
        self.assertEqual(lab_c.ops.mutated, [("delete", lab_b.state.created_uid)])

    def test_cleanup_without_the_delete_confirmation_deletes_nothing(self):
        lab_b, lab_c = self.after_b()
        report = run(stages.run_stage_c(lab_c.ctx))
        self.assertEqual(report.stop_code, "mutation_not_confirmed")
        self.assertIn(lab_b.state.created_uid, lab_c.world.accounts)
        self.assertEqual(lab_c.ops.mutated, [])

    def test_cleanup_of_an_account_that_is_already_gone_passes_without_deleting(self):
        lab = Lab("C", with_dummy=True, protect_dummy=True)
        lab.confirm_all("delete")
        report = run(stages.run_stage_c(lab.ctx))
        self.assertTrue(report.clean())
        self.assertIn("already_absent", report.notes)
        self.assertEqual(report.requests, ({"lookup": 1}, 1))

    def test_cleanup_refuses_a_handle_that_is_not_the_generated_shape(self):
        lab = Lab("C", with_dummy=True, protect_dummy=True)
        lab.confirm_all("delete")
        lab.ctx.secrets["email"] = lab.dummy[1]
        report = run(stages.run_stage_c(lab.ctx))
        self.assertEqual(report.stop_code, "find_refused")
        self.assertIsNotNone(lab.dummy_record())
        self.assertEqual(report.requests, ({}, 0))

    def test_cleanup_refuses_to_adopt_the_dummy_uid_even_behind_a_disposable_looking_email(self):
        lab = Lab("C", with_dummy=True, protect_dummy=True)
        lab.world.accounts[lab.dummy[0]]["email"] = "g8-0123456789abcdef@example.com"          # a corrupt world: the dummy uid answers to the handle
        lab.confirm_all("delete")
        report = run(stages.run_stage_c(lab.ctx))
        self.assertEqual(report.stop_code, "adopt_not_disposable")
        self.assertIsNotNone(lab.dummy_record())
        self.assertEqual(lab.ops.mutated, [])

    def test_cleanup_can_confirm_nothing_but_delete(self):
        lab = Lab("C")
        for cls in ("create", "password", "revoke", "disable"):
            self.assertEqual(stop_code(lambda: asyncio.sleep(0, lab.state.mutations.confirm(cls))), "mutation_not_allowed")
        self.assertEqual(stop_code(lambda: lab.ctx.ops.set_disabled("uid1", True)), "mutation_not_confirmed")

    def test_adoption_is_only_possible_in_the_cleanup_stage_and_only_once(self):
        lab = Lab("B")
        self.assertEqual(stop_code(lambda: asyncio.sleep(0, lab.state.adopt_for_cleanup("uid1", "g8-0123456789abcdef@example.com"))), "adopt_refused")
        lab = Lab("C")
        lab.state.adopt_for_cleanup("uid1", "g8-0123456789abcdef@example.com")
        self.assertEqual(stop_code(lambda: asyncio.sleep(0, lab.state.adopt_for_cleanup("uid2", "g8-0123456789abcdef@example.com"))), "adopt_refused")

    def test_find_is_refused_outside_cleanup(self):
        lab = Lab("B")
        self.assertEqual(stop_code(lambda: lab.ctx.ops.find_uid("g8-0123456789abcdef@example.com")), "find_refused")


class SafeOutput(unittest.TestCase):
    def test_reports_and_reprs_contain_no_identifier_secret_or_token(self):
        lab_a = Lab("A")
        report_a = run_a(lab_a)
        lab_b = Lab("B", with_dummy=True)
        report_b = run_b(lab_b)
        text = report_a.render() + report_b.render() + repr(report_a) + repr(report_b)
        needles = [lab_a.dummy[0], lab_a.dummy[1], lab_a.dummy[2], "Start-Passw0rd-1", "Second-Passw0rd-2", lab_b.state.created_uid,
                   "g8-0123456789abcdef@example.com", "@", "fake-id-", "fake-refresh", "eyJ"]
        for needle in needles:
            self.assertNotIn(needle, text.replace("g8-0123456789abcdef", "HANDLE"), needle)
        for token in list(lab_b.world.id_tokens) + list(lab_b.session.refresh_tokens):
            self.assertNotIn(token, text)

    def test_the_session_tokens_are_secrets_that_do_not_print(self):
        outcome = stages.SessionOutcome(True, "-", g3.Secret("tokenvalue"), g3.Secret("refreshvalue"), False)
        self.assertNotIn("tokenvalue", repr(outcome))
        self.assertNotIn("refreshvalue", "%s" % outcome)

    def test_the_vocabulary_of_a_report_is_fixed(self):
        report = stages.Report("B")
        with self.assertRaises(AssertionError):
            report.add("B01", "maybe")

    def test_no_network_was_attempted_by_any_stage_test(self):
        self.assertEqual(ATTEMPTS, [])


if __name__ == "__main__":
    unittest.main()
