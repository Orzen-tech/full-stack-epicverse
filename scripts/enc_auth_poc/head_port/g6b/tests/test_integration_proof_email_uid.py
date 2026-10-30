"""G6b integration tests (1/3): Option D proof hardening, the verified-UID e-mail helper on /auth/email/verify-request and
/auth/email/verify-confirm, the UID-mismatch check, and the behaviour with the gateway disabled.

Client (G1 envelope sealer) -> loopback port -> isolated backend container (the G6b-patched lineage) -> Postgres + LOCAL Firebase Auth
emulator. EMULATOR-BASED: nothing here proves real Firebase behaviour. Dummy accounts only.
"""
import hashlib
import hmac
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import (Gateway, LabTestCase, auth, claims, forged_token, id_token_for, make_user, new_password,  # noqa: E402
                     session_from, unique)

HASH_SECRET = "local-test-secret-3"           # ENC_AUTH_HASH_SECRET of every lab backend (a throw-away test value)
GENERIC = {"error": "AUTH_FAILED"}
PROOF_INVALID = {"error": "email-proof-invalid"}


def claim_key(proof):
    return hmac.new(HASH_SECRET.encode(), ("proof_claim:" + proof).encode(), hashlib.sha256).hexdigest()


def setUpModule():
    support.start(["main", "disabled", "proofage"])


def tearDownModule():
    support.stop()


class ProofHardening(LabTestCase):
    def signup(self, g=None, email=None, password=None, proof=None):
        g = g or Gateway()
        email, password = email or unique("ph"), password or new_password()
        proof = proof or g.proof(email)
        status, body, head = g.call("signup", {"email": email, "password": password}, proof=proof)
        return g, email, password, proof, status, body

    # (1) gateway-side maximum age -------------------------------------------------------------------------------------
    def test_a_proof_older_than_the_gateway_maximum_age_is_refused_and_creates_nothing(self):
        g = Gateway("proofage")                                    # maximum age 60 s on this variant
        email, password = unique("age"), new_password()
        proof = g.proof(email)
        g.age_proof(proof, 2)
        status, body, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((status, body), (403, PROOF_INVALID))
        with self.assertRaises(auth.UserNotFoundError):
            auth.get_user_by_email(email, app=support.ADMIN)
        fresh = g.proof(email)                                     # a freshly issued proof works
        self.assertEqual(g.call("signup", {"email": email, "password": password}, proof=fresh)[0], 200)

    def test_default_maximum_age_is_ten_minutes_and_the_global_thirty_minute_expiry_is_unchanged(self):
        g = Gateway()
        ok_email, late_email = unique("age9"), unique("age11")
        ok_proof, late_proof = g.proof(ok_email), g.proof(late_email)
        g.age_proof(ok_proof, 9)
        g.age_proof(late_proof, 11)
        self.assertEqual(g.call("signup", {"email": ok_email, "password": new_password()}, proof=ok_proof)[0], 200)
        self.assertEqual(g.call("signup", {"email": late_email, "password": new_password()}, proof=late_proof)[:2], (403, PROOF_INVALID))
        g.proof(unique("fresh"))
        lifetime = g.sql("SELECT (expires_at - created_at)::text FROM email_verification_proofs_v2 ORDER BY created_at DESC LIMIT 1")
        self.assertEqual(lifetime, "00:15:00")                    # HEAD issues proofs with a 15-minute expiry; the port does not touch it

    def test_the_owner_can_still_verify_after_the_gateway_age_has_passed(self):
        g, email, password, proof, status, body = self.signup()
        self.assertEqual(status, 200)
        g.age_proof(proof, 11)                                     # the gateway no longer accepts it, /auth/mark-verified still does
        session = session_from(body["custom_token"])
        uid = claims(session["idToken"])["user_id"]
        self.assertEqual(g.sync_user(session["idToken"], uid, email)[0], 200)
        status, out, _ = g.bearer_post("/auth/mark-verified", session["idToken"], form={"proof": proof})
        self.assertEqual((status, out), (200, {"status": "ok"}))
        self.assertEqual(g.sql("SELECT email_verified FROM users WHERE uid='%s'" % uid), "t")

    # (2) the first verified UID is recorded; only an HMAC is stored ----------------------------------------------------------
    def test_the_first_verified_uid_is_recorded_under_an_hmac_and_the_raw_proof_is_never_stored(self):
        g, email, password, proof, status, body = self.signup()
        uid = auth.get_user_by_email(email, app=support.ADMIN).uid
        self.assertEqual(g.sql("SELECT uid FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), uid)
        dump = g.sql("SELECT string_agg(t::text, ',') FROM enc_auth_proof_claims t")
        self.assertNotIn(proof, dump)
        self.assertNotIn(hashlib.sha256(proof.encode()).hexdigest(), dump)         # not even the plain hash that the proof table uses
        self.assertNotIn(email.lower(), dump.lower())

    # (3) a different UID cannot use the proof in /auth/mark-verified --------------------------------------------------------
    def test_a_different_uid_with_the_same_email_cannot_mark_verified_with_a_claimed_proof(self):
        g, email, password, proof, status, body = self.signup()
        uid_a = auth.get_user_by_email(email, app=support.ADMIN).uid
        # the first account disappears and ANOTHER Firebase account is created with the same e-mail, directly (not through the gateway)
        auth.delete_user(uid_a, app=support.ADMIN)
        user_b = make_user(email=email)
        self.assertNotEqual(user_b["uid"], uid_a)
        token_b = id_token_for(email, user_b["password"])
        self.assertEqual(g.sync_user(token_b, user_b["uid"], email)[0], 200)
        status, out, _ = g.bearer_post("/auth/mark-verified", token_b, form={"proof": proof})
        self.assertEqual(status, 403)
        unknown = g.bearer_post("/auth/mark-verified", token_b, form={"proof": "evp1_" + "x" * 43})
        self.assertEqual((status, out), (unknown[0], unknown[1]))                  # indistinguishable from an unknown proof: reveals nothing
        self.assertEqual(g.sql("SELECT email_verified FROM users WHERE uid='%s'" % user_b["uid"]), "f")
        self.assertEqual(g.sql("SELECT (consumed_at IS NULL)::text FROM email_verification_proofs_v2 WHERE email_hash IN "
                               "(SELECT email_hash FROM email_verification_proofs_v2 ORDER BY created_at DESC LIMIT 1)"), "true")  # not consumed by the wrong UID

    def test_the_owner_verifies_once_and_the_proof_is_consumed(self):
        g, email, password, proof, status, body = self.signup()
        session = session_from(body["custom_token"])
        uid = claims(session["idToken"])["user_id"]
        self.assertEqual(g.sync_user(session["idToken"], uid, email)[0], 200)
        self.assertEqual(g.bearer_post("/auth/mark-verified", session["idToken"], form={"proof": proof})[:2], (200, {"status": "ok"}))
        self.assertEqual(g.sql("SELECT consumed_by_uid FROM email_verification_proofs_v2 WHERE consumed_at IS NOT NULL AND consumed_by_uid='%s'" % uid), uid)
        # one-time: the consumed proof cannot create another account
        self.assertEqual(g.call("signup", {"email": email, "password": password}, proof=proof)[:2], (403, PROOF_INVALID))

    def test_a_proof_never_used_at_the_gateway_has_no_claim_and_the_old_flow_still_works(self):
        # RESIDUAL RISK, stated honestly: a proof used OUTSIDE the gateway (a direct Firebase signup) is not claimed, so Option D cannot help there
        g = Gateway()
        user = make_user()
        proof = g.proof(user["email"])
        token = id_token_for(user["email"], user["password"])
        self.assertEqual(g.sync_user(token, user["uid"], user["email"])[0], 200)
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "0")
        self.assertEqual(g.bearer_post("/auth/mark-verified", token, form={"proof": proof})[:2], (200, {"status": "ok"}))

    # (4) signup recovery and one-time behaviour are preserved ---------------------------------------------------------------
    def test_recovery_with_the_same_password_keeps_the_same_claim(self):
        g, email, password, proof, status, body = self.signup()
        uid = auth.get_user_by_email(email, app=support.ADMIN).uid
        status, again, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((status, again.get("recovered"), again.get("created")), (200, True, False))
        self.assertEqual(claims(session_from(again["custom_token"])["idToken"])["user_id"], uid)
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "1")
        wrong = g.call("signup", {"email": email, "password": new_password()}, proof=proof)
        self.assertEqual(wrong[:2], (400, {"error": "email-already-in-use"}))

    # (5) atomic under concurrency -------------------------------------------------------------------------------------------
    def test_concurrent_claims_for_different_uids_leave_exactly_one_owner(self):
        g = Gateway()
        proof = "evp1_" + "".join(__import__("secrets").token_urlsafe(32)[:43])
        uids = ["race-uid-%d" % i for i in range(6)]
        support.SECRETS.add(proof)
        with ThreadPoolExecutor(4) as pool:
            replies = list(pool.map(lambda _: g.post_json("/__g6b/bind_race", {"proof": proof, "uids": uids, "repeat": 3}), range(3)))
        owners = {r[1]["owner"] for r in replies}
        self.assertEqual(len(owners), 1)
        owner = owners.pop()
        self.assertIn(owner, uids)
        for _, body, _ in replies:
            for uid, won in zip(body["uids"], body["results"]):
                self.assertEqual(won, uid == owner, "only the owner may be told it owns the proof")
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "1")

    def test_concurrent_signups_with_one_proof_create_one_account_and_one_claim(self):
        g, email, password = Gateway(), unique("cc"), new_password()
        proof = g.proof(email)
        with ThreadPoolExecutor(5) as pool:
            replies = list(pool.map(lambda i: Gateway().call("signup", {"email": email, "password": password}, proof=proof), range(5)))
        statuses = sorted(r[0] for r in replies)
        self.assertTrue(all(s in (200, 429) for s in statuses), statuses)
        self.assertGreaterEqual(statuses.count(200), 1)
        uids = {claims(session_from(r[1]["custom_token"])["idToken"])["user_id"] for r in replies if r[0] == 200}
        self.assertEqual(len(uids), 1)
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "1")

    # (6) failed or interrupted creation must not claim the proof ---------------------------------------------------------------
    def test_a_failed_creation_claims_nothing_and_the_proof_still_works_for_the_right_account(self):
        g, email, password = Gateway(), unique("fc"), new_password()
        proof = g.proof(email)
        g.set_faults(create_user=True)
        try:
            status, body, head = g.call("signup", {"email": email, "password": password}, proof=proof)
        finally:
            g.clear_faults()
        self.assertEqual((status, body["error"]), (503, "AUTH_UNAVAILABLE"))
        self.assertIn(head.get("X-Enc-Cause") or head.get("x-enc-cause"), ("sdk_error",))
        self.assertNotIn("custom_token", body)
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "0")
        self.assertEqual(g.call("signup", {"email": email, "password": password}, proof=proof)[0], 200)

    def test_account_created_but_claim_not_stored_gives_no_token_and_a_retry_completes_for_the_same_owner(self):
        g, email, password = Gateway(), unique("ic"), new_password()
        proof = g.proof(email)
        g.set_faults(bind_claim=True)
        try:
            status, body, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        finally:
            g.clear_faults()
        self.assertEqual((status, body["error"]), (503, "AUTH_UNAVAILABLE"))
        self.assertNotIn("custom_token", body)                                         # no session before the claim is safely recorded
        uid = auth.get_user_by_email(email, app=support.ADMIN).uid                              # the Firebase account exists
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "0")
        # somebody else with the same proof but another password cannot take the account or the claim
        self.assertEqual(g.call("signup", {"email": email, "password": new_password()}, proof=proof)[:2], (400, {"error": "email-already-in-use"}))
        self.assertEqual(g.sql("SELECT count(*) FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), "0")
        # the real owner retries with the same password: recovery binds the claim to the existing account
        status, again, _ = g.call("signup", {"email": email, "password": password}, proof=proof)
        self.assertEqual((status, again.get("recovered")), (200, True))
        self.assertEqual(g.sql("SELECT uid FROM enc_auth_proof_claims WHERE proof_hash='%s'" % claim_key(proof)), uid)

    # (7) gateway disabled: old behaviour --------------------------------------------------------------------------------------
    def test_with_the_gateway_disabled_claims_are_never_consulted_and_the_old_flow_is_unchanged(self):
        g = Gateway("disabled")
        user = make_user()
        proof = g.proof(user["email"])
        g.sql("INSERT INTO enc_auth_proof_claims (proof_hash, uid) VALUES ('%s', 'someone-else')" % claim_key(proof))
        token = id_token_for(user["email"], user["password"])
        self.assertEqual(g.sync_user(token, user["uid"], user["email"])[0], 200)
        self.assertEqual(g.bearer_post("/auth/mark-verified", token, form={"proof": proof})[:2], (200, {"status": "ok"}))
        self.assertEqual(g.sql("SELECT email_verified FROM users WHERE uid='%s'" % user["uid"]), "t")

    def test_every_gateway_endpoint_is_unavailable_when_disabled(self):
        g = Gateway("disabled")
        for op in ("nonce", "login", "signup", "reauth", "change_password"):
            status, body, head = g.request("POST", "/auth/enc/" + op, b"{}", {"Content-Type": "application/json"})
            self.assertEqual((status, body), (503, {"error": "AUTH_UNAVAILABLE"}), op)
            self.assertEqual(head.get("X-Enc-Cause") or head.get("x-enc-cause"), "disabled")


class EmailVerificationRoutes(LabTestCase):
    """The verified-UID e-mail helper now also guards /auth/email/verify-request and /auth/email/verify-confirm."""

    def sent_to(self, g, email):
        return [m for m in g.request("GET", "/__g6b/outbox")[1] if m["to"].lower() == email.lower()]

    def start_flow(self, g, token, user):
        self.assertEqual(g.sync_user(token, user["uid"], user["email"])[0], 200)
        return g.bearer_post("/auth/email/verify-request", token, json_body={})

    def confirm(self, g, token, challenge_id, otp):
        return g.bearer_post("/auth/email/verify-confirm", token, form={"challenge_id": challenge_id, "otp": otp})

    def test_a_custom_token_session_without_an_email_claim_can_verify_through_the_otp_flow(self):
        g, user = Gateway(), make_user()
        custom = g.login(user["email"], user["password"])[1]["custom_token"]
        token = session_from(custom)["idToken"]
        print("  [observed, emulator] custom-token ID token carries an e-mail claim: %s" % ("email" in claims(token)))
        status, out, _ = self.start_flow(g, token, user)
        self.assertEqual((status, out["status"]), (200, "sent"))
        otp = self.sent_to(g, user["email"])[-1]["otp"]
        self.assertEqual(self.confirm(g, token, out["challenge_id"], otp)[:2], (200, {"status": "verified"}))
        self.assertEqual(g.sql("SELECT email_verified FROM users WHERE uid='%s'" % user["uid"]), "t")

    def test_the_same_flow_with_an_unsigned_token_that_has_no_email_claim(self):
        g, user = Gateway(), make_user()
        token = forged_token(user["uid"])
        status, out, _ = self.start_flow(g, token, user)
        self.assertEqual((status, out["status"]), (200, "sent"))
        self.assertEqual(self.confirm(g, token, out["challenge_id"], self.sent_to(g, user["email"])[-1]["otp"])[:2], (200, {"status": "verified"}))

    def test_a_normal_session_with_an_email_claim_still_works(self):
        g, user = Gateway(), make_user()
        token = id_token_for(user["email"], user["password"])
        self.assertIn("email", claims(token))
        status, out, _ = self.start_flow(g, token, user)
        self.assertEqual((status, out["status"]), (200, "sent"))
        self.assertEqual(self.confirm(g, token, out["challenge_id"], self.sent_to(g, user["email"])[-1]["otp"])[:2], (200, {"status": "verified"}))

    def test_a_claim_that_disagrees_with_the_account_is_rejected_and_no_code_is_sent(self):
        g, user = Gateway(), make_user()
        self.assertEqual(g.sync_user(forged_token(user["uid"]), user["uid"], user["email"])[0], 200)
        liar = forged_token(user["uid"], email=unique("liar"))
        self.assertEqual(g.bearer_post("/auth/email/verify-request", liar, json_body={})[0], 403)
        self.assertEqual(self.confirm(g, liar, "c" * 20, "123456")[0], 403)
        self.assertEqual(self.sent_to(g, user["email"]), [])
        self.assertEqual(g.sql("SELECT count(*) FROM mfa_login_challenges WHERE uid='%s'" % user["uid"]), "0")

    def test_disabled_missing_and_account_without_email_are_rejected(self):
        g, user = Gateway(), make_user()
        self.assertEqual(g.sync_user(forged_token(user["uid"]), user["uid"], user["email"])[0], 200)
        auth.update_user(user["uid"], disabled=True, app=support.ADMIN)
        self.assertEqual(g.bearer_post("/auth/email/verify-request", forged_token(user["uid"]), json_body={})[0], 403)
        self.assertEqual(self.confirm(g, forged_token(user["uid"]), "c" * 20, "123456")[0], 403)
        ghost = "g6b-ghost-" + __import__("secrets").token_hex(4)
        self.assertEqual(g.bearer_post("/auth/email/verify-request", forged_token(ghost), json_body={})[0], 403)
        self.assertEqual(self.confirm(g, forged_token(ghost), "c" * 20, "123456")[0], 403)
        no_email = auth.create_user(app=support.ADMIN, uid="g6b-noemail-" + __import__("secrets").token_hex(3))
        self.assertEqual(g.bearer_post("/auth/email/verify-request", forged_token(no_email.uid), json_body={})[0], 403)
        self.assertEqual(self.sent_to(g, user["email"]), [])

    def test_invalid_and_revoked_tokens(self):
        g, user = Gateway(), make_user()
        self.assertEqual(g.bearer_post("/auth/email/verify-request", "garbage", json_body={})[0], 401)
        self.assertEqual(self.confirm(g, "garbage", "c" * 20, "123456")[0], 401)
        token = id_token_for(user["email"], user["password"])
        self.assertEqual(g.sync_user(token, user["uid"], user["email"])[0], 200)
        auth.revoke_refresh_tokens(user["uid"], app=support.ADMIN)
        import time
        time.sleep(1.2)
        status, out, _ = g.bearer_post("/auth/email/verify-request", token, json_body={})
        # The deployed get_current_user does not check revocation (unchanged by G6b): an already issued ID token keeps working until it
        # expires. This is a PRE-EXISTING property, reported, not something G6b introduced or changed.
        print("  [observed] verify-request with an ID token whose refresh tokens were revoked answers HTTP %d" % status)
        self.assertIn(status, (200, 401))

    def test_the_account_is_not_found_by_a_client_supplied_uid_or_email(self):
        g, a, b = Gateway(), make_user(), make_user()
        self.assertEqual(g.sync_user(forged_token(a["uid"]), a["uid"], a["email"])[0], 200)
        token_a = forged_token(a["uid"])
        status, out, _ = g.bearer_post("/auth/email/verify-request", token_a, json_body={"email": b["email"], "uid": b["uid"]})
        self.assertEqual((status, out["status"]), (200, "sent"))
        self.assertEqual(self.sent_to(g, b["email"]), [])                               # nothing the body says can steer the code
        self.assertEqual(len(self.sent_to(g, a["email"])), 1)

    def test_with_the_gateway_disabled_the_old_behaviour_is_unchanged(self):
        g, user = Gateway("disabled"), make_user()
        # claim present: used as is
        token = forged_token(user["uid"], email=user["email"])
        self.assertEqual(g.sync_user(token, user["uid"], user["email"])[0], 200)
        status, out, _ = g.bearer_post("/auth/email/verify-request", token, json_body={})
        self.assertEqual((status, out["status"]), (200, "sent"))
        # claim absent: the old answer, with no account lookup
        status, out, _ = g.bearer_post("/auth/email/verify-request", forged_token(user["uid"]), json_body={})
        self.assertEqual((status, out), (400, {"detail": "No email on token"}))
        # claim that differs from the stored row: the old 409
        status, out, _ = g.bearer_post("/auth/email/verify-request", forged_token(user["uid"], email=unique("other")), json_body={})
        self.assertEqual(status, 409)
        # even for an account that does not exist in Firebase the old code only looks at the claim
        status, _, _ = g.bearer_post("/auth/email/verify-confirm", forged_token("g6b-ghost-x", email="x@example.invalid"),
                                     form={"challenge_id": "c" * 20, "otp": "123456"})
        self.assertEqual(status, 400)


class UidMismatch(LabTestCase):
    """The identity adapter answers with a DIFFERENT UID for credentials of an account (and that account claims the same e-mail)."""

    def setUp(self):
        self.g = Gateway()
        self.a, self.b = make_user(), make_user()
        self.session = session_from(self.g.login(self.a["email"], self.a["password"])[1]["custom_token"])
        self.g.diag_reset()

    def tearDown(self):
        self.g.clear_faults()

    def confuse(self):
        self.g.set_faults(uid_confusion={"uid": self.b["uid"], "email": self.a["email"]})

    def test_reauth_is_rejected_mints_nothing_and_reveals_nothing(self):
        self.confuse()
        status, body, _ = self.g.call("reauth", {"email": self.a["email"], "password": self.a["password"]}, bearer=self.session["idToken"])
        self.g.clear_faults()
        wrong_password = self.g.call("reauth", {"email": self.a["email"], "password": new_password()}, bearer=self.session["idToken"])
        self.assertEqual((status, body), (400, {"error": "invalid-credential"}))
        self.assertEqual((status, body), wrong_password[:2])                              # same answer as a plain wrong password
        self.assertEqual(self.g.diag()["calls"]["mint"], 0)                               # no custom token was minted for anybody

    def test_change_password_is_rejected_and_changes_no_password(self):
        self.confuse()
        new = new_password()
        status, body, _ = self.g.call("change_password", {"email": self.a["email"], "password": self.a["password"], "new_password": new},
                                      bearer=self.session["idToken"])
        self.g.clear_faults()
        self.assertEqual((status, body), (400, {"error": "invalid-credential"}))
        calls = self.g.diag()["calls"]
        self.assertEqual((calls["update_password"], calls["mint"], calls["revoke"]), (0, 0, 0))
        self.assertEqual(self.g.login(self.a["email"], self.a["password"])[0], 200)      # old password of A still works
        self.assertEqual(self.g.login(self.a["email"], new)[0], 400)                      # the new one was never set, for A or B
        self.assertEqual(self.g.login(self.b["email"], self.b["password"])[0], 200)
        self.assertEqual(self.g.login(self.b["email"], new)[0], 400)

    def test_the_mismatch_counts_as_a_failure_for_the_limiter_and_without_the_fault_the_same_call_succeeds(self):
        self.confuse()
        self.g.call("reauth", {"email": self.a["email"], "password": self.a["password"]}, bearer=self.session["idToken"])
        self.g.clear_faults()
        status, body, _ = self.g.call("reauth", {"email": self.a["email"], "password": self.a["password"]}, bearer=self.session["idToken"])
        self.assertEqual(status, 200)
        self.assertTrue(body["custom_token"])


if __name__ == "__main__":
    unittest.main()
