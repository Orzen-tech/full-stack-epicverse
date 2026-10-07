"""F-09 H1 — pre-hijack regression and the temporary legacy-compat switch.

The vulnerability: /auth/verify-otp is unauthenticated, yet used to credit
email verification to whichever Firebase account owned the email. An attacker
who pre-created an (unverified) account for a victim's address therefore
received the verification when the REAL owner completed the OTP.
"""
import pytest

from app.core.config import settings
from app.services import user_db
from f09_helpers import (API, bearer, execute, fetchval, is_verified, mark_verified, signup_proof)

VICTIM = "victim@example.com"


async def test_prehijack_attacker_account_does_not_receive_the_victims_verification(
        client, mailbox, fb, make_user):
    # Attacker pre-creates a Firebase account + profile using the victim's email.
    attacker_tok = await make_user("uidAttacker", VICTIM)
    assert fb.accounts_by_email[VICTIM].uid == "uidAttacker"  # old code would have found this

    # The real owner now completes send-email-otp + verify-otp.
    victim_proof = await signup_proof(client, mailbox, VICTIM)

    # The unauthenticated verify-otp neither identified nor updated the attacker.
    assert fb.lookup_calls == 0
    assert not await is_verified("uidAttacker")

    # The attacker cannot cash in without the raw proof...
    assert (await mark_verified(client, attacker_tok)).status_code == 403
    assert (await mark_verified(client, attacker_tok, "evp1_" + "Z" * 43)).status_code == 403
    # ...nor with a proof for some other address they could obtain themselves.
    own_proof = await signup_proof(client, mailbox, "attacker-own@example.com")
    assert (await mark_verified(client, attacker_tok, own_proof)).status_code == 403
    assert not await is_verified("uidAttacker")

    # The victim's proof is still intact and unconsumed.
    unconsumed = await fetchval(
        "SELECT count(*) FROM email_verification_proofs_v2 WHERE consumed_at IS NULL")
    assert unconsumed == 2  # the victim's and the attacker's own, neither consumed
    assert victim_proof  # held only by the party that solved the OTP


async def test_attacker_cannot_poll_for_a_signup_in_progress(client, mailbox, make_user):
    """The v1 weakness: an email-only proof could be grabbed by anyone whose
    token had that email. Polling mark-verified must now fail until the
    proof secret is presented, however often it is tried."""
    attacker_tok = await make_user("uidAttacker", VICTIM)
    await signup_proof(client, mailbox, VICTIM)
    for _ in range(5):
        assert (await mark_verified(client, attacker_tok)).status_code == 403
    assert not await is_verified("uidAttacker")


# ----------------------------------------------------------- legacy compat
async def test_legacy_compat_on_documents_the_transition_window_risk(client, mailbox, fb, make_user, monkeypatch):
    """With the temporary switch ON the old behaviour is deliberately back
    (so old app builds keep working), including the pre-hijack weakness. This
    test pins that fact: it proves the harness would detect the vulnerability
    if secure mode ever regressed, and that the switch is what controls it."""
    monkeypatch.setattr(settings, "EMAIL_VERIFY_LEGACY_COMPAT", True)
    await make_user("uidAttacker", VICTIM)
    await signup_proof(client, mailbox, VICTIM)
    assert fb.lookup_calls == 1
    assert await is_verified("uidAttacker")  # the original vulnerability, only in compat mode


async def test_secure_mode_cannot_reach_legacy_marking(client, mailbox, fb, make_user):
    tok = await make_user("uidA", VICTIM)
    # a legacy (v1, email-only) proof, as the old verify-otp would have left behind
    await user_db.record_email_verification_proof(VICTIM)
    assert await fetchval("SELECT count(*) FROM email_verification_proofs") == 1
    # secure mode: no legacy fallback for a proof-less mark-verified
    r = await mark_verified(client, tok)
    assert r.status_code == 403 and not await is_verified("uidA")
    # a v2 proof is the only thing that works, and the legacy lookup never ran
    proof = await signup_proof(client, mailbox, VICTIM)
    assert (await mark_verified(client, tok, proof)).status_code == 200
    assert fb.lookup_calls == 0


async def test_legacy_compat_on_keeps_old_clients_working(client, mailbox, fb, make_user, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_VERIFY_LEGACY_COMPAT", True)
    # Old signup: verify-otp (no account yet) leaves a v1 proof; the proof-less
    # mark-verified then consumes it.
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": VICTIM})
    r = await client.post(f"{API}/auth/verify-otp", data={"identifier": VICTIM, "otp": mailbox.last_otp(VICTIM)})
    assert r.status_code == 200
    assert await fetchval("SELECT count(*) FROM email_verification_proofs") == 1
    tok = await make_user("uidA", VICTIM, firebase_account=False)
    assert (await mark_verified(client, tok)).status_code == 200
    assert await is_verified("uidA")
    # a v1 proof is single-use too
    await execute("UPDATE users SET email_verified = FALSE WHERE uid = 'uidA'")
    assert (await mark_verified(client, tok)).status_code == 403


async def test_legacy_compat_on_still_never_falls_back_when_a_proof_is_sent(client, mailbox, make_user, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_VERIFY_LEGACY_COMPAT", True)
    tok = await make_user("uidA", VICTIM, firebase_account=False)
    await user_db.record_email_verification_proof(VICTIM)
    r = await mark_verified(client, tok, "evp1_" + "Q" * 43)  # a bad v2 proof must not fall back to v1
    assert r.status_code == 403 and not await is_verified("uidA")


async def test_legacy_telemetry_has_no_identifying_data(client, mailbox, make_user, monkeypatch, capsys):
    monkeypatch.setattr(settings, "EMAIL_VERIFY_LEGACY_COMPAT", True)
    tok = await make_user("uidA", VICTIM, firebase_account=False)
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": VICTIM})
    otp = mailbox.last_otp(VICTIM)
    r = await client.post(f"{API}/auth/verify-otp", data={"identifier": VICTIM, "otp": otp})
    proof = r.json()["email_verification_proof"]
    await mark_verified(client, tok)
    out = capsys.readouterr()
    text = out.out + out.err
    assert "[EMAIL-VERIFY] legacy_compat path=verify_otp_mark" in text
    assert "[EMAIL-VERIFY] legacy_compat path=mark_verified_no_proof" in text
    for secret in (VICTIM, "uidA", otp, proof):
        assert secret not in text


# --------------------------------------------------------------- redaction
async def test_server_never_logs_proof_otp_or_email(client, mailbox, make_user, capsys):
    tok = await make_user("uidA", VICTIM)
    await client.post(f"{API}/auth/send-email-otp", data={"identifier": VICTIM})
    otp = mailbox.last_otp(VICTIM)
    wrong = "000000" if otp != "000000" else "111111"
    await client.post(f"{API}/auth/verify-otp", data={"identifier": VICTIM, "otp": wrong})
    r = await client.post(f"{API}/auth/verify-otp", data={"identifier": VICTIM, "otp": otp})
    proof = r.json()["email_verification_proof"]
    await mark_verified(client, tok, "evp1_" + "Y" * 43)
    await mark_verified(client, tok, proof)
    out = capsys.readouterr()
    text = out.out + out.err
    for secret in (proof, otp, VICTIM, "evp1_" + "Y" * 43):
        assert secret not in text
    assert await is_verified("uidA")
