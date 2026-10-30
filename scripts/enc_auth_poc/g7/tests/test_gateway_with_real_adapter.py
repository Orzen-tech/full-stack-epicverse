"""The existing encrypted gateway running on the REAL-Firebase adapter, with a FAKE Firebase behind it (isolated Docker lab, no network, no
Google service). Proves the four operations compose correctly with the adapter's error mapping, ordering and fail-closed behaviour.
NOT a statement about real Firebase.
"""
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
G7 = HERE.parent
sys.path.insert(0, str(G7 / "harness"))
sys.path.insert(0, str(G7.parent / "g6b" / "tests"))

import orchestrate_g7 as lab7  # noqa: E402,F401  (must come first: it swaps in the G7 lab)
import support  # noqa: E402  (the G6b lab support: gateway client, proofs, leak scan)
from support import Gateway, LabTestCase, SECRETS, new_password, unique  # noqa: E402

GENERIC_WRONG = {"error": "invalid-credential"}


def setUpModule():
    support.start(["main", "g7real", "g7misconf", "disabled"])
    SECRETS.update({lab7.FAKE_API_KEY, lab7.SIGNER})


def tearDownModule():
    support.stop()


def world(g, name, data=None):
    return g.post_json("/__g6b/g7/" + name, data or {})[1]


def state(g, **query):
    return world(g, "state", query)


def new_account(g, **kw):
    email, password = unique("w"), new_password()
    SECRETS.add(email)
    uid = world(g, "account", {"email": email, "password": password, **kw})["uid"]
    return email, password, uid


def cause(head):
    return head.get("X-Enc-Cause") or head.get("x-enc-cause")


class Base(LabTestCase):
    backend_for_diag = "g7real"

    def setUp(self):
        self.g = Gateway("g7real")
        world(self.g, "fault", {"clear": True})
        world(self.g, "policy", {"min_length": 8})
        world(self.g, "reset")

    def tearDown(self):
        world(self.g, "fault", {"clear": True})

    def token_claims(self, token):
        got = world(self.g, "verify_custom_token", {"token": token})
        self.assertTrue(got["ok"], got)
        return got["claims"]


class Signup(Base):
    def test_signup_creates_an_unverified_account_and_a_custom_token_signed_for_exactly_that_uid(self):
        email, password = unique("s"), new_password()
        SECRETS.add(email)
        proof = self.g.proof(email)
        status, body, _ = self.g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((status, body["created"]), (200, True))
        info = state(self.g, email=email, password=password)["account"]
        self.assertEqual((info["email_verified"], info["password_matches"], info["disabled"]), (False, True, False))
        claims = self.token_claims(body["custom_token"])
        self.assertEqual(claims["uid"], info["uid"])
        self.assertFalse({"email", "email_verified", "claims"} & set(claims))
        events = state(self.g)["events"]
        self.assertLess(events.index("admin.create_user"), events.index("iam.sign_blob"))
        self.assertEqual(events.count("admin.create_user"), 1)

    def test_recovery_with_the_same_password_and_refusal_with_another(self):
        email, password = unique("r"), new_password()
        SECRETS.add(email)
        proof = self.g.proof(email)
        first = self.g.call("signup", {"email": email, "password": password}, proof=proof)
        uid = state(self.g, email=email)["account"]["uid"]
        again = self.g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((first[0], again[0], again[1].get("recovered")), (200, 200, True))
        self.assertEqual(self.token_claims(again[1]["custom_token"])["uid"], uid)
        wrong = self.g.call("signup", {"email": email, "password": new_password()}, proof=proof)
        self.assertEqual(wrong[:2], (400, {"error": "email-already-in-use"}))
        self.assertEqual(state(self.g, email=email)["account"]["uid"], uid, "no second account for the same e-mail")

    def test_a_weak_password_is_refused_by_the_identity_service_and_creates_nothing(self):
        email = unique("w")
        SECRETS.add(email)
        proof = self.g.proof(email)
        world(self.g, "policy", {"min_length": 30})
        before = state(self.g)["account_count"]
        status, body, _ = self.g.call("signup", {"email": email, "password": new_password()}, proof=proof)
        self.assertEqual((status, body), (400, {"error": "weak-password"}))
        self.assertEqual(state(self.g)["account_count"], before)
        self.assertEqual(state(self.g)["calls"].get("iam.sign_blob", 0), 0)

    def test_account_creation_outage_is_a_safe_503_and_is_not_retried(self):
        email = unique("o")
        SECRETS.add(email)
        proof = self.g.proof(email)
        world(self.g, "fault", {"operation": "admin.create_user", "kind": "unavailable", "cause": "sdk_unavailable", "times": 5})
        status, body, head = self.g.call("signup", {"email": email, "password": new_password()}, proof=proof)
        self.assertEqual((status, body, cause(head)), (503, {"error": "AUTH_UNAVAILABLE"}, "sdk_unavailable"))
        self.assertEqual(state(self.g)["calls"]["admin.create_user"], 1)
        self.assertNotIn("custom_token", body)


class Login(Base):
    def test_right_password_yields_a_token_for_the_account(self):
        email, password, uid = new_account(self.g)
        status, body, _ = self.g.login(email, password)
        self.assertEqual(status, 200)
        self.assertEqual(self.token_claims(body["custom_token"])["uid"], uid)

    def test_wrong_password_and_unknown_email_get_the_identical_refusal_and_no_signing(self):
        email, password, _ = new_account(self.g)
        wrong = self.g.login(email, new_password())
        unknown = self.g.login(unique("nobody"), new_password())
        self.assertEqual(wrong[:2], (400, GENERIC_WRONG))
        self.assertEqual(wrong[:2], unknown[:2])
        self.assertEqual(state(self.g)["calls"].get("iam.sign_blob", 0), 0)

    def test_a_disabled_account_gets_no_token_even_with_the_right_password(self):
        email, password, _ = new_account(self.g, disabled=True)
        status, body, _ = self.g.login(email, password)
        self.assertEqual((status, body), (400, {"error": "user-disabled"}))
        self.assertEqual(state(self.g)["calls"].get("iam.sign_blob", 0), 0)

    def test_identity_throttling_is_a_429_and_not_counted_as_a_wrong_password(self):
        email, password, _ = new_account(self.g)
        before = int(self.g.sql("SELECT coalesce(sum(hits),0) FROM enc_auth_limits WHERE scope='pwf_global'"))
        world(self.g, "fault", {"operation": "rest.sign_in", "throttle": True, "times": 1})
        status, body, _ = self.g.login(email, password)
        self.assertEqual((status, body), (429, {"error": "too-many-requests"}))
        self.assertEqual(int(self.g.sql("SELECT coalesce(sum(hits),0) FROM enc_auth_limits WHERE scope='pwf_global'")), before)

    def test_a_transient_upstream_failure_is_retried_once_and_a_persistent_one_is_a_safe_503(self):
        email, password, _ = new_account(self.g)
        world(self.g, "fault", {"operation": "rest.sign_in", "kind": "unavailable", "cause": "http_503", "times": 1})
        self.assertEqual(self.g.login(email, password)[0], 200)
        self.assertEqual(state(self.g)["calls"]["rest.sign_in"], 2)
        world(self.g, "reset")
        world(self.g, "fault", {"operation": "rest.sign_in", "kind": "unavailable", "cause": "http_503", "times": 10})
        status, body, head = self.g.login(email, password)
        self.assertEqual((status, body, cause(head)), (503, {"error": "AUTH_UNAVAILABLE"}, "http_503"))
        self.assertEqual(state(self.g)["calls"]["rest.sign_in"], 3)

    def test_signing_failures_are_safe_503s_with_their_category_and_never_retried(self):
        email, password, _ = new_account(self.g)
        for why in ("permission_denied", "service_disabled", "unauthenticated"):
            world(self.g, "reset")
            world(self.g, "fault", {"operation": "iam.sign_blob", "kind": "unavailable", "cause": why, "times": 5})
            status, body, head = self.g.login(email, password)
            self.assertEqual((status, body, cause(head)), (503, {"error": "AUTH_UNAVAILABLE"}, why))
            self.assertEqual(state(self.g)["calls"]["iam.sign_blob"], 1)
            self.assertNotIn("custom_token", body)


class Reauth(Base):
    def setUp(self):
        super().setUp()
        self.email, self.password, self.uid = new_account(self.g)
        self.bearer = world(self.g, "id_token", {"uid": self.uid})["token"]
        SECRETS.add(self.bearer)

    def reauth(self, password=None, bearer="default", email=None):
        return self.g.call("reauth", {"email": email or self.email, "password": password or self.password},
                           bearer=self.bearer if bearer == "default" else bearer)

    def test_right_password_and_matching_session_yield_a_fresh_token_for_the_same_uid(self):
        status, body, _ = self.reauth()
        self.assertEqual(status, 200)
        self.assertEqual(self.token_claims(body["custom_token"])["uid"], self.uid)

    def test_a_valid_session_alone_is_not_enough_the_password_is_checked_every_time(self):
        status, body, _ = self.reauth(password=new_password())
        self.assertEqual((status, body), (400, GENERIC_WRONG))
        self.assertEqual(state(self.g)["calls"].get("iam.sign_blob", 0), 0)

    def test_missing_expired_revoked_disabled_or_foreign_sessions_get_no_token(self):
        self.assertEqual(self.reauth(bearer=None)[0], 401)
        self.assertEqual(self.reauth(bearer="garbage")[0], 401)
        expired = world(self.g, "id_token", {"uid": self.uid, "ttl": -5})["token"]
        self.assertEqual(self.reauth(bearer=expired)[0], 401)
        world(self.g, "disable", {"uid": self.uid})
        self.assertEqual(self.reauth()[0], 401)
        world(self.g, "disable", {"uid": self.uid, "disabled": False})
        self.assertEqual(self.reauth()[0], 200)
        stale = world(self.g, "id_token", {"uid": self.uid, "issued_ago": 30})["token"]
        world(self.g, "revoke", {"uid": self.uid})
        self.assertEqual(self.reauth(bearer=stale)[0], 401)
        other_email, other_password, other_uid = new_account(self.g)
        status, body, _ = self.reauth(password=other_password, email=other_email)             # someone else's valid credentials with MY session
        self.assertEqual((status, body), (400, GENERIC_WRONG))
        self.assertEqual(state(self.g)["calls"].get("admin.set_password", 0), 0)

    def test_an_identity_outage_during_the_session_check_is_a_503_not_a_401(self):
        world(self.g, "fault", {"operation": "admin.verify_token", "kind": "unavailable", "cause": "sdk_unavailable", "times": 10})
        status, body, head = self.reauth()
        self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}))
        self.assertEqual(cause(head), "sdk_unavailable")


class ChangePassword(Base):
    def setUp(self):
        super().setUp()
        self.email, self.password, self.uid = new_account(self.g)
        time.sleep(1.1)
        self.bearer = world(self.g, "id_token", {"uid": self.uid})["token"]
        SECRETS.add(self.bearer)

    def change(self, new, current=None, bearer="default"):
        SECRETS.add(new)
        return self.g.call("change_password", {"email": self.email, "password": current or self.password, "new_password": new},
                           bearer=self.bearer if bearer == "default" else bearer)

    def test_success_updates_then_revokes_then_signs_in_that_order_and_ends_the_old_session(self):
        new = new_password()
        time.sleep(1.1)
        status, body, _ = self.change(new)
        self.assertEqual((status, body["password_changed"], body["sessions_revoked"], bool(body["custom_token"])), (200, True, True, True))
        events = state(self.g)["events"]
        self.assertLess(events.index("admin.set_password"), events.index("admin.revoke"))
        self.assertLess(events.index("admin.revoke"), events.index("iam.sign_blob"))
        info = state(self.g, uid=self.uid, password=new)["account"]
        self.assertTrue(info["password_matches"])
        self.assertFalse(state(self.g, uid=self.uid, password=self.password)["account"]["password_matches"])
        self.assertEqual(self.token_claims(body["custom_token"])["uid"], self.uid)
        self.assertEqual(self.g.call("reauth", {"email": self.email, "password": new}, bearer=self.bearer)[0], 401, "the old session must be revoked")

    def test_a_wrong_current_password_changes_nothing(self):
        status, body, _ = self.change(new_password(), current=new_password())
        self.assertEqual((status, body), (400, GENERIC_WRONG))
        self.assertEqual(state(self.g)["calls"].get("admin.set_password", 0), 0)
        self.assertTrue(state(self.g, uid=self.uid, password=self.password)["account"]["password_matches"])

    def test_same_weak_and_unauthenticated_requests_change_nothing(self):
        status, body, _ = self.change(self.password)
        self.assertEqual((status, body), (400, {"error": "password-unchanged"}))
        status, body, _ = self.change("short")
        self.assertEqual(status, 400)
        self.assertIn(body["error"], ("weak-password",))
        self.assertEqual(self.change(new_password(), bearer=None)[0], 401)
        self.assertEqual(self.change(new_password(), bearer="garbage")[0], 401)
        self.assertEqual(state(self.g)["calls"].get("admin.set_password", 0), 0)

    def test_a_policy_failure_inside_the_identity_service_is_reported_and_leaves_the_password(self):
        world(self.g, "policy", {"min_length": 40})
        status, body, _ = self.change(new_password())
        self.assertEqual((status, body["error"], body["password_changed"]), (400, "weak-password", False))
        self.assertTrue(state(self.g, uid=self.uid, password=self.password)["account"]["password_matches"])

    def test_an_update_outage_is_reported_as_unknown_and_never_retried(self):
        world(self.g, "fault", {"operation": "admin.set_password", "kind": "unavailable", "cause": "sdk_timeout", "times": 5})
        status, body, head = self.change(new_password())
        self.assertEqual((status, body, cause(head)), (503, {"error": "AUTH_UNAVAILABLE", "password_changed": "unknown"}, "sdk_timeout"))
        self.assertEqual(state(self.g)["calls"]["admin.set_password"], 1)

    def test_a_failed_revocation_or_signing_after_the_update_is_reported_honestly(self):
        world(self.g, "fault", {"operation": "admin.revoke", "kind": "unavailable", "cause": "sdk_unavailable", "times": 5})
        status, body, _ = self.change(new_password())
        self.assertEqual((status, body["password_changed"], body["sessions_revoked"], bool(body["custom_token"])), (200, True, False, True))
        world(self.g, "fault", {"clear": True})
        self.password = self.password  # the password was changed by the call above; use a fresh account for the next case
        email, password, uid = new_account(self.g)
        bearer = world(self.g, "id_token", {"uid": uid})["token"]
        world(self.g, "fault", {"operation": "iam.sign_blob", "kind": "unavailable", "cause": "permission_denied", "times": 5})
        status, body, _ = self.g.call("change_password", {"email": email, "password": password, "new_password": new_password()}, bearer=bearer)
        self.assertEqual((status, body["password_changed"], body["custom_token"], body["retry_signin"]), (200, True, None, True))


class FailClosed(LabTestCase):
    backend_for_diag = "g7misconf"

    def test_an_inconsistent_configuration_answers_503_not_configured_for_every_endpoint(self):
        g = Gateway("g7misconf")
        for op in ("nonce", "login", "signup", "reauth", "change_password"):
            status, body, head = g.request("POST", "/auth/enc/" + op, b'{"op":"login"}', {"Content-Type": "application/json"})
            self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}), op)
            self.assertEqual(cause(head), "not_configured", op)

    def test_the_misconfigured_backend_never_reaches_a_fake_or_real_service(self):
        g = Gateway("g7misconf")
        self.assertEqual(world(g, "state")["calls"], {})


class DisabledGatewayDdl(LabTestCase):
    """A gateway-DISABLED backend (the default) runs the application's own startup schema work and NOTHING gateway-specific: no enc_auth_* table,
    no enc_auth index. An enabled backend (same code, same startup) has all three tables and their indexes. Checked in the real database."""

    TABLES = "SELECT count(*) FROM information_schema.tables WHERE table_name ~ '^enc_auth_'"
    INDEXES = "SELECT count(*) FROM pg_indexes WHERE indexname ~ '^idx_enc_'"

    def test_disabled_creates_no_gateway_table_or_index_while_the_application_schema_exists(self):
        g = Gateway("disabled")
        self.assertEqual((g.sql(self.TABLES), g.sql(self.INDEXES)), ("0", "0"))
        self.assertEqual(g.sql("SELECT count(*) FROM information_schema.tables WHERE table_name IN ('users', 'user_otps', 'mfa_sessions', 'email_ownership_proofs')"), "4")

    def test_enabled_still_creates_the_three_tables_and_their_indexes(self):
        g = Gateway("main")
        self.assertEqual((g.sql(self.TABLES), g.sql(self.INDEXES)), ("3", "3"))

    def test_disabled_gateway_still_refuses_every_request_and_touches_no_gateway_table(self):
        g = Gateway("disabled")
        for op in ("nonce", "login", "signup", "reauth", "change_password"):
            status, body, head = g.request("POST", "/auth/enc/" + op, b'{"op":"login"}', {"Content-Type": "application/json"})
            self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}), op)
            self.assertEqual(cause(head), "disabled", op)
        self.assertEqual((g.sql(self.TABLES), g.sql(self.INDEXES)), ("0", "0"))


if __name__ == "__main__":
    unittest.main()
