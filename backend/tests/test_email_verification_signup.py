"""F-09 H1 — new-signup email verification (proof-secret flow).

Secure mode is the default (EMAIL_VERIFY_LEGACY_COMPAT is False).
"""
import asyncio

import pytest

from app.core.config import settings
from app.services import user_db
from f09_helpers import (API, bearer, execute, expire_proofs, fetch, fetchrow, fetchval, is_verified,
                         mark_verified, signup_proof)

VICTIM = "victim@example.com"


def test_legacy_compat_is_off_by_default():
    # The default comes from the Settings class itself, not from this process's
    # (already-cleaned) environment.
    assert settings.EMAIL_VERIFY_LEGACY_COMPAT is False
    assert type(settings)().EMAIL_VERIFY_LEGACY_COMPAT is False


# ---------------------------------------------------------------- A.1 - A.4
async def test_correct_otp_returns_proof_and_marks_nothing(client, mailbox, fb, make_user):
    # An unverified profile that already holds this email, and a Firebase
    # account that owns it: exactly what the old code would have credited.
    await make_user("uidA", VICTIM)
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": VICTIM})
    r = await client.post(f"{API}/auth/verify-otp",
                          data={"identifier": VICTIM, "otp": mailbox.last_otp(VICTIM)})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    proof = body["email_verification_proof"]
    assert proof.startswith("evp1_") and len(proof) >= 48
    assert r.headers["cache-control"] == "no-store"
    # verify-otp itself never marks anyone, and never asks Firebase who owns the email.
    assert not await is_verified("uidA")
    assert fb.lookup_calls == 0
    assert await fetchval("SELECT count(*) FROM email_verification_proofs") == 0  # legacy table unused


async def test_proof_is_stored_only_as_hashes(client, mailbox):
    proof = await signup_proof(client, mailbox, VICTIM)
    rows = await fetch("SELECT * FROM email_verification_proofs_v2")
    assert len(rows) == 1
    row = rows[0]
    assert proof not in str(dict(row)) and VICTIM not in str(dict(row))
    assert len(row["proof_hash"]) == 64 and len(row["email_hash"]) == 64
    assert row["purpose"] == "signup_email_verification"
    assert row["consumed_at"] is None and row["consumed_by_uid"] is None
    ttl = await fetchval("SELECT EXTRACT(EPOCH FROM (expires_at - created_at)) FROM email_verification_proofs_v2")
    assert 14 * 60 < ttl <= 15 * 60 + 1


async def test_wrong_otp_issues_no_proof(client, mailbox):
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": VICTIM})
    wrong = "000000" if mailbox.last_otp(VICTIM) != "000000" else "111111"
    r = await client.post(f"{API}/auth/verify-otp", data={"identifier": VICTIM, "otp": wrong})
    assert r.status_code == 400
    assert "email_verification_proof" not in r.text
    assert await fetchval("SELECT count(*) FROM email_verification_proofs_v2") == 0


async def test_five_wrong_attempts_lock_the_otp(client, mailbox):
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": VICTIM})
    good = mailbox.last_otp(VICTIM)
    wrong = "000000" if good != "000000" else "111111"
    codes = [(await client.post(f"{API}/auth/verify-otp",
                                data={"identifier": VICTIM, "otp": wrong})).status_code for _ in range(5)]
    assert codes == [400, 400, 400, 400, 429]
    after = await client.post(f"{API}/auth/verify-otp", data={"identifier": VICTIM, "otp": good})
    assert after.status_code == 400 and "email_verification_proof" not in after.text
    assert await fetchval("SELECT count(*) FROM email_verification_proofs_v2") == 0


# ---------------------------------------------------------------- A.5 - A.8
async def test_mark_verified_without_proof_is_rejected(client, make_user):
    tok = await make_user("uidA", VICTIM)
    r = await mark_verified(client, tok)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_PROOF_INVALID"
    # exactly what an older app build sends: no body, JSON content type
    r = await client.post(f"{API}/auth/mark-verified",
                          headers={**bearer(tok), "Content-Type": "application/json"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "EMAIL_PROOF_INVALID"
    assert not await is_verified("uidA")


async def test_fabricated_proofs_are_rejected(client, mailbox, make_user):
    tok = await make_user("uidA", VICTIM)
    await signup_proof(client, mailbox, VICTIM)  # a real proof exists, but the caller doesn't have it
    for fake in ("evp1_" + "A" * 43, "evp1_", "not-a-proof", "x" * 500, "evp1_" + "B" * 500):
        r = await mark_verified(client, tok, fake)
        assert r.status_code == 403, fake[:20]
    assert not await is_verified("uidA")
    assert await fetchval("SELECT count(*) FROM email_verification_proofs_v2 WHERE consumed_at IS NULL") == 1


@pytest.mark.parametrize("multipart", [False, True])
async def test_valid_proof_verifies_only_the_authenticated_account(client, mailbox, make_user, multipart):
    tok = await make_user("uidA", VICTIM)
    await make_user("uidB", "bystander@example.com")
    proof = await signup_proof(client, mailbox, VICTIM)
    if multipart:  # what the Flutter client's FormData produces
        r = await client.post(f"{API}/auth/mark-verified", headers=bearer(tok), files={"proof": (None, proof)})
    else:
        r = await mark_verified(client, tok, proof)
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert await is_verified("uidA")
    assert not await is_verified("uidB")
    row = await fetchrow("SELECT consumed_at, consumed_by_uid FROM email_verification_proofs_v2")
    assert row["consumed_at"] is not None and row["consumed_by_uid"] == "uidA"


async def test_proof_cannot_be_reused(client, mailbox, make_user):
    tok = await make_user("uidA", VICTIM)
    proof = await signup_proof(client, mailbox, VICTIM)
    assert (await mark_verified(client, tok, proof)).status_code == 200
    # Put the account back to unverified so the early "already verified"
    # shortcut cannot mask a reuse: the consumed proof itself must be refused.
    await execute("UPDATE users SET email_verified = FALSE WHERE uid = 'uidA'")
    r = await mark_verified(client, tok, proof)
    assert r.status_code == 403 and not await is_verified("uidA")


async def test_expired_proof_is_rejected(client, mailbox, make_user):
    tok = await make_user("uidA", VICTIM)
    proof = await signup_proof(client, mailbox, VICTIM)
    await expire_proofs()
    r = await mark_verified(client, tok, proof)
    assert r.status_code == 403 and not await is_verified("uidA")


# --------------------------------------------------------------- A.9 - A.14
async def test_proof_for_email_a_cannot_verify_email_b(client, mailbox, make_user):
    tok_b = await make_user("uidB", "other@example.com")
    tok_a = await make_user("uidA", VICTIM)
    proof_a = await signup_proof(client, mailbox, VICTIM)
    r = await mark_verified(client, tok_b, proof_a)
    assert r.status_code == 403 and not await is_verified("uidB")
    # the rejected attempt must not have burned the legitimate holder's proof
    assert await fetchval("SELECT consumed_at FROM email_verification_proofs_v2") is None
    assert (await mark_verified(client, tok_a, proof_a)).status_code == 200


async def test_missing_sql_row_is_rejected_and_proof_survives(client, mailbox, fb, make_user):
    tok = fb.add("uidGhost", VICTIM)  # signed in with Firebase, but no users row yet
    proof = await signup_proof(client, mailbox, VICTIM)
    r = await mark_verified(client, tok, proof)
    assert r.status_code == 403
    assert await fetchval("SELECT consumed_at FROM email_verification_proofs_v2") is None


async def test_row_with_a_different_email_is_rejected(client, mailbox, fb, make_user):
    await make_user("uidA", "stored@example.com")
    tok = fb.add("uidA", VICTIM)  # token email differs from the stored email
    proof = await signup_proof(client, mailbox, VICTIM)
    r = await mark_verified(client, tok, proof)
    assert r.status_code == 403 and not await is_verified("uidA")
    assert await fetchval("SELECT consumed_at FROM email_verification_proofs_v2") is None


async def test_body_fields_cannot_redirect_the_verification(client, mailbox, make_user):
    tok_a = await make_user("uidA", VICTIM)
    await make_user("uidB", "bystander@example.com")
    proof = await signup_proof(client, mailbox, VICTIM)
    # B (a different account) tries to use A's proof while naming A in the body
    tok_b = "tok:uidB"
    r = await mark_verified(client, tok_b, proof, uid="uidA", email=VICTIM, email_verified="true")
    assert r.status_code == 403
    assert not await is_verified("uidA") and not await is_verified("uidB")
    # A uses its own proof but names B and claims verified in the body: only A changes
    r = await mark_verified(client, tok_a, proof, uid="uidB", email="bystander@example.com",
                            email_verified="true", firebase_id="uidB")
    assert r.status_code == 200
    assert await is_verified("uidA") and not await is_verified("uidB")


async def test_body_cannot_set_email_verified_without_a_proof(client, make_user):
    tok = await make_user("uidA", VICTIM)
    r = await mark_verified(client, tok, email_verified="true", uid="uidA", email=VICTIM)
    assert r.status_code == 403 and not await is_verified("uidA")


async def test_concurrent_consumption_has_exactly_one_winner(client, mailbox, make_user):
    # Two profiles legitimately share the email (an orphaned profile is the
    # documented real-world case); one proof must verify exactly one of them,
    # however many requests race for it.
    tokens = [await make_user(f"uid{i}", VICTIM, firebase_account=(i == 0)) for i in range(6)]
    proof = await signup_proof(client, mailbox, VICTIM)
    results = await asyncio.gather(*[mark_verified(client, t, proof) for t in tokens])
    assert sorted(r.status_code for r in results) == [200, 403, 403, 403, 403, 403]
    assert await fetchval("SELECT count(*) FROM users WHERE email_verified") == 1


async def test_concurrent_consumption_same_account_consumes_once(make_user, client, mailbox):
    await make_user("uidA", VICTIM)
    proof = await signup_proof(client, mailbox, VICTIM)
    outcomes = await asyncio.gather(*[
        user_db.consume_email_verification_proof("uidA", VICTIM, proof) for _ in range(10)])
    assert outcomes.count(True) == 1 and outcomes.count(False) == 9
    assert await fetchval("SELECT count(*) FROM email_verification_proofs_v2 WHERE consumed_at IS NOT NULL") == 1


async def test_second_proof_supersedes_the_first(client, mailbox, make_user):
    tok = await make_user("uidA", VICTIM)
    first = await signup_proof(client, mailbox, VICTIM)
    second = await signup_proof(client, mailbox, VICTIM)
    assert first != second
    assert (await mark_verified(client, tok, first)).status_code == 403
    assert (await mark_verified(client, tok, second)).status_code == 200
    assert await fetchval("SELECT count(*) FROM email_verification_proofs_v2 WHERE consumed_at IS NULL") == 0


async def test_email_is_normalised_end_to_end(client, mailbox, fb, make_user):
    tok = await make_user("uidA", "Victim@Example.COM")  # mixed case in the SQL row and the token
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": "  VICTIM@example.com "})
    r = await client.post(f"{API}/auth/verify-otp", data={
        "identifier": " victim@EXAMPLE.com", "otp": mailbox.last_otp(VICTIM)})
    assert r.status_code == 200
    assert (await mark_verified(client, tok, r.json()["email_verification_proof"])).status_code == 200
    assert await is_verified("uidA")


async def test_missing_hmac_secret_fails_closed(client, mailbox, make_user, monkeypatch):
    tok = await make_user("uidA", VICTIM)
    proof = await signup_proof(client, mailbox, VICTIM)  # issued while the secret was configured
    # OTP issued while the secret was configured (R2: sending fails closed without it)
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": "second@example.com"})
    monkeypatch.setattr(settings, "MFA_OTP_HASH_SECRET", "")
    # verify-otp refuses BEFORE it consumes the code
    r = await client.post(f"{API}/auth/verify-otp", data={
        "identifier": "second@example.com", "otp": mailbox.last_otp("second@example.com")})
    assert r.status_code == 503 and "email_verification_proof" not in r.text
    assert await fetchval("SELECT count(*) FROM user_otps WHERE identifier = 'second@example.com'") == 1
    # nothing can be issued or consumed without the secret
    assert await user_db.issue_email_verification_proof("third@example.com") is None
    r = await mark_verified(client, tok, proof)
    assert r.status_code == 403 and not await is_verified("uidA")


async def test_full_signup_chain_with_real_sync_user(client, mailbox, fb):
    """send-email-otp -> verify-otp -> sync-user (invite) -> mark-verified."""
    await execute("INSERT INTO invite_codes (code, max_uses) VALUES ('EPIC-CHAIN1', 1)")
    proof = await signup_proof(client, mailbox, VICTIM)
    tok = fb.add("uidNew", VICTIM)  # Firebase account created after the OTP step
    r = await client.post(f"{API}/sync-user", headers=bearer(tok), json={
        "firebase_id": "uidNew", "display_name": "New", "email": VICTIM, "invite_code": "EPIC-CHAIN1"})
    assert r.status_code == 200, r.text
    assert not await is_verified("uidNew")  # creating the row verifies nothing
    assert (await mark_verified(client, tok, proof)).status_code == 200
    assert await is_verified("uidNew")
    # Phase 3: the verified account now passes the protected-route check
    fb_resp = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": "hello"})
    assert fb_resp.status_code == 200, fb_resp.text
