"""F-09 H1 — authenticated verification for an existing, unverified user
(/auth/email/verify-request and /auth/email/verify-confirm)."""
from app.services import mfa_challenge
from f09_helpers import (API, bearer, execute, fetch, fetchrow, fetchval, is_verified)

EMAIL = "user@example.com"
REQ = f"{API}/auth/email/verify-request"
CONFIRM = f"{API}/auth/email/verify-confirm"


async def _request(client, token):
    return await client.post(REQ, headers=bearer(token))


async def _confirm(client, token, challenge_id, otp):
    return await client.post(CONFIRM, headers=bearer(token), data={"challenge_id": challenge_id, "otp": otp})


async def _start(client, mailbox, token, email=EMAIL):
    r = await _request(client, token)
    assert r.status_code == 200 and r.json()["status"] == "sent", r.text
    return r.json()["challenge_id"], mailbox.last_otp(email)


def _wrong(otp: str) -> str:
    return "000000" if otp != "000000" else "111111"


async def test_authenticated_unverified_user_can_verify_their_own_account(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    r = await _request(client, tok)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent" and body["expires_in"] == 300
    # the code went to the authoritative database email, and is stored hashed
    assert [to for to, _ in mailbox.sent] == [EMAIL]
    row = await fetchrow("SELECT uid, purpose, identifier, otp_hash FROM mfa_login_challenges")
    assert row["uid"] == "uidA" and row["purpose"] == "verify_email"
    assert EMAIL not in str(dict(row)) and mailbox.last_otp(EMAIL) not in str(dict(row))
    assert not await is_verified("uidA")

    r = await _confirm(client, tok, body["challenge_id"], mailbox.last_otp(EMAIL))
    assert r.status_code == 200 and r.json() == {"status": "verified"}
    assert await is_verified("uidA")
    # no MFA session is ever created by email verification, and MFA is untouched
    assert await fetchval("SELECT count(*) FROM mfa_sessions") == 0
    assert not await fetchval("SELECT mfa_enabled FROM users WHERE uid = 'uidA'")
    # the challenge is single-use
    again = await _confirm(client, tok, body["challenge_id"], mailbox.last_otp(EMAIL))
    assert again.status_code == 400


async def test_another_uid_cannot_consume_the_challenge(client, mailbox, make_user):
    tok_a = await make_user("uidA", EMAIL)
    tok_b = await make_user("uidB", "other@example.com")
    cid, otp = await _start(client, mailbox, tok_a)
    r = await _confirm(client, tok_b, cid, otp)
    assert r.status_code == 400
    assert not await is_verified("uidA") and not await is_verified("uidB")
    # the legitimate owner is unaffected and can still finish
    assert (await _confirm(client, tok_a, cid, otp)).status_code == 200
    assert await is_verified("uidA") and not await is_verified("uidB")


async def test_even_a_same_email_other_account_cannot_use_the_challenge(client, mailbox, make_user):
    tok_a = await make_user("uidA", EMAIL)
    tok_b = await make_user("uidB", EMAIL, firebase_account=False)  # orphan profile with the same email
    cid, otp = await _start(client, mailbox, tok_a)
    assert (await _confirm(client, tok_b, cid, otp)).status_code == 400
    assert not await is_verified("uidB")


async def test_unauthenticated_requests_verify_nothing(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    cid, otp = await _start(client, mailbox, tok)
    for headers in ({}, {"Authorization": "Bearer tok:forged"}):
        a = await client.post(REQ, headers=headers)
        b = await client.post(CONFIRM, headers=headers, data={"challenge_id": cid, "otp": otp})
        assert a.status_code in (401, 403) and b.status_code in (401, 403)
    assert not await is_verified("uidA")


async def test_cross_purpose_challenges_are_rejected_both_ways(client, mailbox, make_user, fb):
    tok = await make_user("uidA", EMAIL)
    # an MFA challenge must not verify the email
    for purpose in (mfa_challenge.PURPOSE_LOGIN_MFA, mfa_challenge.PURPOSE_ENABLE_MFA):
        cid, otp = await mfa_challenge.create_mfa_challenge("uidA", EMAIL, purpose)
        assert (await _confirm(client, tok, cid, otp)).status_code == 400
    assert not await is_verified("uidA")
    # a verify_email challenge must not mint an MFA session or enable MFA
    cid, otp = await mfa_challenge.create_mfa_challenge("uidA", EMAIL, mfa_challenge.PURPOSE_VERIFY_EMAIL)
    r = await client.post(f"{API}/auth/mfa/verify", headers=bearer(tok), data={"challenge_id": cid, "otp": otp})
    assert r.status_code == 400
    r = await client.post(f"{API}/user/mfa/enable-confirm", headers=bearer(tok),
                          data={"challenge_id": cid, "otp": otp})
    assert r.status_code == 400
    assert await fetchval("SELECT count(*) FROM mfa_sessions") == 0
    assert not await fetchval("SELECT mfa_enabled FROM users WHERE uid = 'uidA'")
    assert not await is_verified("uidA")


async def test_already_verified_user_gets_already_verified_and_no_email(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL, verified=True)
    r = await _request(client, tok)
    assert r.status_code == 200 and r.json() == {"status": "already_verified"}
    assert mailbox.sent == []
    assert await fetchval("SELECT count(*) FROM mfa_login_challenges") == 0


async def test_token_email_must_match_the_database_email(client, mailbox, make_user, fb):
    await make_user("uidA", "stored@example.com")
    tok = fb.add("uidA", "different@example.com")  # token email != stored email
    r = await _request(client, tok)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "EMAIL_MISMATCH"
    assert mailbox.sent == []
    # a challenge created for the stored email cannot be confirmed with a mismatching token either
    cid, otp = await mfa_challenge.create_mfa_challenge("uidA", "stored@example.com", "verify_email")
    assert (await _confirm(client, tok, cid, otp)).status_code == 400
    assert not await is_verified("uidA")


async def test_challenge_bound_to_a_different_email_is_rejected(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    cid, otp = await mfa_challenge.create_mfa_challenge("uidA", "someone-else@example.com", "verify_email")
    assert (await _confirm(client, tok, cid, otp)).status_code == 400
    assert not await is_verified("uidA")


async def test_missing_profile_is_a_404(client, mailbox, fb):
    tok = fb.add("uidGhost", EMAIL)
    assert (await _request(client, tok)).status_code == 404
    assert mailbox.sent == []


async def test_five_wrong_attempts_exhaust_the_challenge(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    cid, otp = await _start(client, mailbox, tok)
    codes = [(await _confirm(client, tok, cid, _wrong(otp))).status_code for _ in range(5)]
    assert codes == [400, 400, 400, 400, 429]
    # the correct code no longer works once the challenge is exhausted
    assert (await _confirm(client, tok, cid, otp)).status_code == 400
    assert not await is_verified("uidA")
    # a fresh challenge recovers
    cid2, otp2 = await _start(client, mailbox, tok)
    assert (await _confirm(client, tok, cid2, otp2)).status_code == 200
    assert await is_verified("uidA")


async def test_expired_challenge_is_rejected(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    cid, otp = await _start(client, mailbox, tok)
    await execute("UPDATE mfa_login_challenges SET created_at = NOW() - INTERVAL '10 minutes', "
                  "expires_at = NOW() - INTERVAL '5 minutes'")
    assert (await _confirm(client, tok, cid, otp)).status_code == 400
    assert not await is_verified("uidA")


async def test_malformed_codes_and_unknown_challenges_are_rejected(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    cid, otp = await _start(client, mailbox, tok)
    for bad in ("12345", "1234567", "abcdef", "12 456"):
        assert (await _confirm(client, tok, cid, bad)).status_code == 400
    assert (await _confirm(client, tok, "no-such-challenge", otp)).status_code == 400
    # malformed codes must not burn the challenge's attempts
    assert await fetchval("SELECT attempts FROM mfa_login_challenges") == 0
    assert (await _confirm(client, tok, cid, otp)).status_code == 200


async def test_a_new_request_supersedes_the_previous_challenge(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    cid1, otp1 = await _start(client, mailbox, tok)
    cid2, otp2 = await _start(client, mailbox, tok)
    assert cid1 != cid2
    assert (await _confirm(client, tok, cid1, otp1)).status_code == 400
    assert (await _confirm(client, tok, cid2, otp2)).status_code == 200


async def test_send_rate_limit_applies_to_verify_request(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    codes = [(await _request(client, tok)).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    assert len(mailbox.sent) == 3


async def test_verification_does_not_require_an_mfa_session(client, mailbox, make_user):
    """An unverified account cannot have MFA on, so none may be demanded."""
    tok = await make_user("uidA", EMAIL)
    r = await client.post(REQ, headers=bearer(tok))  # no X-MFA-Session header
    assert r.status_code == 200
    cid, otp = r.json()["challenge_id"], mailbox.last_otp(EMAIL)
    assert (await client.post(CONFIRM, headers=bearer(tok), data={"challenge_id": cid, "otp": otp})
            ).status_code == 200


async def test_missing_hmac_secret_fails_closed(client, mailbox, make_user, monkeypatch):
    from app.core.config import settings
    tok = await make_user("uidA", EMAIL)
    monkeypatch.setattr(settings, "MFA_OTP_HASH_SECRET", "")
    assert (await _request(client, tok)).status_code == 503
    assert mailbox.sent == []
    assert await fetchval("SELECT count(*) FROM mfa_login_challenges") == 0


async def test_verified_account_unlocks_phase3_routes_after_this_flow(client, mailbox, make_user):
    tok = await make_user("uidA", EMAIL)
    blocked = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": "hi"})
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "EMAIL_VERIFICATION_REQUIRED"
    cid, otp = await _start(client, mailbox, tok)
    await _confirm(client, tok, cid, otp)
    ok = await client.post(f"{API}/feedback", headers=bearer(tok), json={"message": "hi"})
    assert ok.status_code == 200
