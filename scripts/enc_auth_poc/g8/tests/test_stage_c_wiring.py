"""OFFLINE READINESS CHECK of the cleanup (Stage C): the real runner (`g8_live_run.main cleanup`) over the stateful fake Firebase of the Stage B wiring test,
network-denied. The cleanup approval is patched True ONLY inside these tests. Proves the cleanup can delete exactly one thing - the generated disposable
account named by the handle - and can never delete the existing dummy account."""
import json
import unittest

import _g8 as t
from _tree import ATTEMPTS

import g8_guard as guard
import g8_live_run as runner
import test_stage_a_wiring as wa
import test_stage_b_wiring as wb
from test_stage_a_wiring import ADMIN_TOKEN, DUMMY_EMAIL, DUMMY_UID, SIGNER_TOKEN

HANDLE = "g8-0123456789abcdef"
DISPOSABLE_UID = "gen00000000000000000002"
DISPOSABLE_EMAIL = HANDLE + "@example.com"


class CleanupBackend(wb.Backend):
    """The Stage B backend with a pre-existing disposable account, and the delete endpoint that Stage B's backend refuses to serve."""

    def __init__(self, with_disposable=True, disposable_uid=DISPOSABLE_UID, **overrides):
        super().__init__(**overrides)
        if with_disposable:
            self.accounts[disposable_uid] = {"email": DISPOSABLE_EMAIL, "password": "Whatever-Passw0rd-9", "disabled": False, "valid_since": 0}
        self.snapshot = {u: dict(a) for u, a in self.accounts.items()}
        self.deleted = []

    def handle(self, host, method, path, body):
        if path.endswith("accounts:delete"):
            self.log.append((host, path))
            self.methods.append(method)
            self.bodies.append(body.decode())
            uid = json.loads(body)["localId"]
            self.accounts.pop(uid, None)
            self.deleted.append(uid)
            self.events.append("delete")
            return 200, {"kind": "identitytoolkit#DeleteAccountResponse"}
        return super().handle(host, method, path, body)


class HarnessC(wa.Harness):
    mode, approve = "cleanup", ("C",)

    def __init__(self, backend=None, handle=HANDLE, dummy_uid=DUMMY_UID, delete_phrase=None, env=None):
        self.backend = backend or CleanupBackend()
        P = guard.ALLOWED_PROJECT_ID
        visible = [str(t._tree.tree_dir()), P, runner.PHRASES["C"], delete_phrase or runner.MUTATION_PHRASES["delete"], t.SIGNER, handle]
        hidden = [wb.API_KEY, SIGNER_TOKEN, ADMIN_TOKEN, dummy_uid]
        super().__init__(self.backend, hidden=hidden, visible=visible, env=env)


class Cleanup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = HarnessC().run()
        cls.b = cls.h.backend
        cls.text = "\n".join(cls.h.out)

    def test_clean_run_in_three_requests_lookup_delete_lookup(self):
        self.assertEqual(self.h.code, 0, self.text)
        self.assertTrue(self.h.out[0].startswith("stage=C clean=True stop=-"))
        self.assertIn("requests total=3 delete=1 lookup=2", self.text)
        self.assertEqual([p.rsplit("/", 1)[-1] for _, p in self.b.log], ["accounts:lookup", "accounts:delete", "accounts:lookup"])
        self.assertEqual([line.split()[:2] for line in self.h.out[0].splitlines()[1:4]], [["C01", "pass"], ["C02", "pass"], ["C03", "pass"]])

    def test_exactly_the_disposable_account_was_deleted_and_the_dummy_is_untouched(self):
        self.assertEqual(self.b.deleted, [DISPOSABLE_UID])
        self.assertNotIn(DISPOSABLE_UID, self.b.accounts)
        self.assertEqual(self.b.accounts[DUMMY_UID], self.b.dummy_before)
        self.assertNotIn(DUMMY_UID, "\n".join(self.b.bodies))
        self.assertNotIn(DUMMY_EMAIL, "\n".join(self.b.bodies))
        self.assertEqual(self.b.events, ["delete"])

    def test_only_two_mutating_gates_matter_and_nothing_else_was_requested(self):
        paths = [p for _, p in self.b.log]
        for word in ("accounts:update", "signUp", ":batch", "signInWith", "/v1/token", "signBlob"):
            self.assertFalse(any(word in p for p in paths), word)

    def test_prompts_and_output_are_safe(self):
        self.assertEqual(len(self.h.hidden_asked), 4)
        self.assertTrue(all("(hidden)" in p for p in self.h.hidden_asked))
        for needle in (DISPOSABLE_UID, DISPOSABLE_EMAIL, DUMMY_UID, DUMMY_EMAIL, SIGNER_TOKEN, ADMIN_TOKEN, "AIza", "eyJ"):
            self.assertNotIn(needle, self.text)
        self.assertNotIn("cleanup_pending", self.text)

    def test_process_state_is_clean_and_the_stages_stay_as_approved(self):
        self.assertIsNone(guard._installed["urllib3"])
        self.assertIsNone(guard._installed["socket"])
        self.assertEqual((guard.LIVE_EXECUTION_APPROVED, guard.STAGE_APPROVED), (False, {"A": False, "B": False, "C": False}))
        self.assertEqual(ATTEMPTS, [])


class Refusals(unittest.TestCase):
    def tearDown(self):
        self.assertIsNone(guard._installed["urllib3"])

    def test_the_approved_handle_is_accepted_and_deletes_only_that_account(self):
        handle = "g8-c26ac1bbc24d17eb"
        backend = CleanupBackend(with_disposable=False)
        backend.accounts[DISPOSABLE_UID] = {"email": handle + "@example.com", "password": "x", "disabled": False, "valid_since": 0}
        h = HarnessC(backend, handle=handle).run()
        self.assertEqual(h.code, 0, h.out)
        self.assertEqual(h.backend.deleted, [DISPOSABLE_UID])
        self.assertEqual(h.backend.accounts[DUMMY_UID], h.backend.dummy_before)
        for other in ("g8-c26ac1bbc24d17ec", "g8-c26ac1bbc24d17e", "g8-c26ac1bbc24d17eb0", "G8-c26ac1bbc24d17eb"):
            self.assertNotEqual(HarnessC(CleanupBackend(with_disposable=False), handle=other).run().backend.deleted, [DISPOSABLE_UID])

    def test_without_the_separate_delete_confirmation_nothing_is_asked_or_sent(self):
        h = HarnessC(delete_phrase="yes").run()
        self.assertEqual(h.out, ["G8 stopped: mutation_not_confirmed"])
        self.assertEqual((h.hidden_asked, h.backend.log, h.backend.deleted), ([], [], []))

    def test_cleanup_is_refused_when_its_own_approval_is_off(self):
        h = HarnessC()
        h.approve = ()
        h.run()
        self.assertEqual(h.out, ["G8 stopped: live_execution_not_approved"])
        self.assertEqual((h.visible_asked, h.hidden_asked, h.backend.log), ([], [], []))

    def test_a_handle_that_is_not_the_generated_shape_is_refused_before_any_request(self):
        for handle in (DUMMY_EMAIL.split("@")[0], DUMMY_EMAIL, "g8-0123456789ABCDEF", "g8-0123456789abcde", HANDLE + "@x", "", "../" + HANDLE):
            h = HarnessC(handle=handle).run()
            self.assertTrue("stop=find_refused" in " ".join(h.out) or h.out[-1].startswith("G8 stopped"), (handle, h.out))
            self.assertEqual((h.backend.log, h.backend.deleted), ([], []), handle)
            self.assertEqual(h.backend.accounts[DUMMY_UID], h.backend.dummy_before)

    def test_a_missing_dummy_uid_to_refuse_stops_before_any_request(self):
        h = HarnessC(dummy_uid="").run()
        self.assertEqual(h.out[-1], "G8 stopped: dummy_identity_missing")
        self.assertEqual((h.backend.log, h.backend.deleted), ([], []))

    def test_an_account_answering_to_the_handle_but_owning_the_dummy_uid_is_never_deleted(self):
        backend = CleanupBackend(with_disposable=False)
        backend.accounts[DUMMY_UID]["email"] = DISPOSABLE_EMAIL                       # a corrupt world: the dummy uid answers to the generated handle
        h = HarnessC(backend).run()
        self.assertIn("stop=adopt_not_disposable", h.out[0])
        self.assertEqual(h.backend.deleted, [])
        self.assertIn(DUMMY_UID, h.backend.accounts)
        self.assertEqual(len(h.backend.log), 1)                                      # only the lookup that found it

    def test_an_account_that_is_already_gone_is_a_clean_no_op(self):
        h = HarnessC(CleanupBackend(with_disposable=False)).run()
        self.assertEqual(h.code, 0, h.out)
        self.assertIn("C01 pass", h.out[0])
        self.assertEqual((len(h.backend.log), h.backend.deleted), (1, []))

    def test_a_delete_that_fails_is_not_a_pass_and_reports_nothing_as_deleted(self):
        class Failing(CleanupBackend):
            def handle(self, host, method, path, body):
                if path.endswith("accounts:delete"):
                    self.log.append((host, path))
                    return 500, {"error": {"message": "INTERNAL"}}
                return super().handle(host, method, path, body)
        h = HarnessC(Failing()).run()
        self.assertEqual(h.code, 1)
        self.assertNotIn("C02 pass", h.out[0])
        self.assertIn(DISPOSABLE_UID, h.backend.accounts)

    def test_the_cleanup_budget_is_three_requests_and_cannot_be_exceeded(self):
        self.assertEqual(guard.STAGE_QUOTAS["C"], {"lookup": 2, "delete": 1})
        self.assertEqual(sum(sum(q.values()) for q in guard.STAGE_QUOTAS.values()), guard.GLOBAL_REQUEST_CAP)
        class Flaky(CleanupBackend):
            def handle(self, host, method, path, body):
                if path.endswith("accounts:lookup") and not self.deleted:
                    self.log.append((host, path))
                    return 503, {"error": {"message": "x"}}
                return super().handle(host, method, path, body)
        h = HarnessC(Flaky()).run()
        self.assertEqual(len(h.backend.log), 2)                                       # the SDK's 503 retries stop at the lookup quota
        self.assertEqual(h.backend.deleted, [])


if __name__ == "__main__":
    unittest.main()
