"""Encrypted-credential gateway port (scratch verification): the gateway's signup proof check is a NON-consuming view of HEAD's own proof
(email_verification_proofs_v2 + the same HMAC helpers), the gateway's tables are created under HEAD's bounded startup transaction, and a disabled
or misconfigured gateway refuses every request without touching the legacy endpoints. Runs inside HEAD's isolated harness (throwaway PostgreSQL,
fake Firebase, captured e-mail)."""
import asyncio

from app.core.config import settings
from app.services import user_db
from app.services.db_pool import get_pool
from f09_helpers import API, execute, fetchval, signup_proof

EMAIL = "owner@example.com"
MAX_AGE = 600.0


async def live(proof, email=EMAIL, max_age=MAX_AGE):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await user_db.email_verification_proof_is_live(conn, proof, email, max_age)


# ------------------------------------------------------------------------------- the non-consuming proof check
async def test_a_proof_issued_for_the_email_is_live_and_the_check_changes_nothing(client, mailbox):
    proof = await signup_proof(client, mailbox, EMAIL)
    before = await fetchval("SELECT count(*) FROM email_verification_proofs_v2 WHERE consumed_at IS NULL")
    for _ in range(3):
        assert await live(proof) is True
    assert await fetchval("SELECT count(*) FROM email_verification_proofs_v2 WHERE consumed_at IS NULL") == before == 1
    assert await live(proof, "  Owner@Example.COM ") is True            # the same normalisation as the production consume path


async def test_wrong_email_unknown_malformed_and_foreign_proofs_are_not_live(client, mailbox):
    proof = await signup_proof(client, mailbox, EMAIL)
    other = await signup_proof(client, mailbox, "other@example.com")
    assert await live(proof, "other@example.com") is False               # bound to the e-mail it was issued for
    assert await live(other, EMAIL) is False
    for bad in (None, "", 7, b"evp1_x", "evp1_", "evp1_" + "Z" * 43, proof[:-1], proof + "x", "evp2_" + proof[5:], proof.upper(), "x" + proof, "evp1_" + "A" * 400):
        assert await live(bad) is False, repr(bad)[:40]
    assert await live(proof, "no-at-sign") is False
    assert await live(proof, "") is False
    assert await live(proof, None) is False


async def test_missing_secret_fails_closed(client, mailbox, monkeypatch):
    proof = await signup_proof(client, mailbox, EMAIL)
    monkeypatch.setattr(settings, "MFA_OTP_HASH_SECRET", "")
    assert await live(proof) is False


async def test_consumed_and_expired_proofs_are_not_live_and_the_owner_can_still_consume_after_the_check(client, mailbox, make_user):
    token = await make_user("uidOwner", EMAIL)
    proof = await signup_proof(client, mailbox, EMAIL)
    assert await live(proof) is True
    assert await user_db.consume_email_verification_proof("uidOwner", EMAIL, proof) is True      # HEAD's production consume still works after the check
    assert await live(proof) is False                                                            # consumed: single use
    assert await user_db.consume_email_verification_proof("uidOwner", EMAIL, proof) is False
    assert token

    proof2 = await signup_proof(client, mailbox, EMAIL)
    await execute("UPDATE email_verification_proofs_v2 SET created_at = NOW() - INTERVAL '1 hour', expires_at = NOW() - INTERVAL '1 minute' "
                  "WHERE consumed_at IS NULL")
    assert await live(proof2) is False                                                           # expired


async def test_the_gateway_side_maximum_age_is_enforced_without_changing_the_global_expiry(client, mailbox):
    proof = await signup_proof(client, mailbox, EMAIL)
    assert await fetchval("SELECT (expires_at - created_at)::text FROM email_verification_proofs_v2") == "00:15:00"      # HEAD's global expiry, untouched
    await execute("UPDATE email_verification_proofs_v2 SET created_at = created_at - INTERVAL '9 minutes' WHERE consumed_at IS NULL")
    assert await live(proof) is True                                                             # 9 minutes: inside the 10-minute gateway window
    await execute("UPDATE email_verification_proofs_v2 SET created_at = created_at - INTERVAL '2 minutes' WHERE consumed_at IS NULL")
    assert await live(proof) is False                                                            # 11 minutes: too old for the gateway ...
    assert await live(proof, max_age=1200.0) is True                                             # ... though still unexpired for the global rule (HEAD's 15 minutes; the aging above moved created_at only)


async def test_a_newer_proof_for_the_same_email_supersedes_the_older_one(client, mailbox):
    first = await signup_proof(client, mailbox, EMAIL)
    second = await signup_proof(client, mailbox, EMAIL)
    assert first != second
    assert await live(first) is False
    assert await live(second) is True


async def test_concurrent_checks_and_concurrent_consumption_cannot_authorize_two_accounts(client, mailbox, make_user):
    await make_user("uidA", EMAIL)
    proof = await signup_proof(client, mailbox, EMAIL)
    assert all(await asyncio.gather(*[live(proof) for _ in range(20)]))
    # two different UIDs that both hold a profile row with this e-mail race for the single-use proof: exactly one wins
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("INSERT INTO users (uid, display_name, email, email_verified, mfa_enabled) VALUES ('uidB', 'b', $1, FALSE, FALSE)", EMAIL)
    results = await asyncio.gather(*[user_db.consume_email_verification_proof(uid, EMAIL, proof) for uid in ("uidA", "uidB") * 5])
    assert results.count(True) == 1
    assert await live(proof) is False
    assert await fetchval("SELECT count(*) FROM users WHERE email_verified") == 1


# ------------------------------------------------------------------------------- startup schema hook
GATEWAY_TABLES = ("enc_auth_nonces", "enc_auth_limits", "enc_auth_proof_claims")
GATEWAY_INDEXES = ("idx_enc_nonce_expires", "idx_enc_limits_window", "idx_enc_claims_created")


async def _drop_gateway_tables():
    """Test database only (the harness refuses to run against anything but its throwaway Unix-socket PostgreSQL)."""
    for table in GATEWAY_TABLES:
        await execute(f"DROP TABLE IF EXISTS {table} CASCADE")


async def _ensure_gateway_schema():
    """Creates the gateway tables the way the startup hook does; only does anything while settings.ENC_AUTH_ENABLED is true."""
    from app.services import enc_auth_store as store
    pool = await get_pool()
    async with pool.acquire() as conn:
        await store.ensure_schema(conn)


async def _gateway_objects():
    tables = [t for t in GATEWAY_TABLES if await fetchval("SELECT to_regclass($1) IS NOT NULL", t)]
    indexes = [i for i in GATEWAY_INDEXES if await fetchval("SELECT count(*) FROM pg_indexes WHERE indexname = $1", i)]
    return tables, indexes


async def test_disabled_gateway_creates_no_gateway_table_or_index_while_the_application_schema_exists():
    assert settings.ENC_AUTH_ENABLED is False
    await _drop_gateway_tables()
    assert await _gateway_objects() == ([], [])
    await user_db.init_db()
    await user_db.init_db()                                                                       # idempotent, and still nothing
    assert await _gateway_objects() == ([], [])
    assert await fetchval("SELECT count(*) FROM pg_indexes WHERE tablename LIKE 'enc\\_auth\\_%'") == 0
    for table in ("users", "user_otps", "email_verification_proofs_v2", "mfa_sessions"):          # the application's own schema is created as before
        assert await fetchval("SELECT to_regclass($1) IS NOT NULL", table) is True, table


async def test_enabled_gateway_creates_the_three_tables_and_their_indexes_and_init_db_is_idempotent(monkeypatch):
    await _drop_gateway_tables()
    monkeypatch.setattr(settings, "ENC_AUTH_ENABLED", True)
    await user_db.init_db()
    assert await _gateway_objects() == (list(GATEWAY_TABLES), list(GATEWAY_INDEXES))
    await user_db.init_db()
    await user_db.init_db()
    assert await _gateway_objects() == (list(GATEWAY_TABLES), list(GATEWAY_INDEXES))


async def test_a_failing_gateway_schema_never_stops_startup_and_the_log_names_only_the_error_class(monkeypatch, capsys):
    async def boom(conn):
        raise RuntimeError("secret detail that must never be logged")
    monkeypatch.setattr(user_db, "ensure_enc_auth_schema", boom)
    await user_db.init_db()
    out = capsys.readouterr().out
    assert "[ENC-AUTH] schema unavailable: RuntimeError" in out
    assert "secret detail" not in out


# ------------------------------------------------------------------------------- disabled / misconfigured gateway
ENC_PATHS = ("nonce", "login", "signup", "reauth", "change_password")


async def _all_unavailable(client, cause):
    for path in ENC_PATHS:
        response = await client.post(f"{API}/auth/enc/{path}", content=b"{}", headers={"Content-Type": "application/json"})
        assert response.status_code == 503, (path, response.status_code)
        assert response.json() == {"error": "AUTH_UNAVAILABLE"}
        assert response.headers.get("x-enc-cause") == cause, (path, response.headers.get("x-enc-cause"))
        assert "custom_token" not in response.text


async def test_the_gateway_is_disabled_by_default_and_refuses_everything(client):
    assert settings.ENC_AUTH_ENABLED is False
    assert settings.ENC_AUTH_IDENTITY_BACKEND == "emulator"
    await _all_unavailable(client, "disabled")


async def test_enabled_without_a_hash_secret_or_keys_or_with_an_unusable_identity_backend_still_refuses(client, monkeypatch):
    from app.api import enc_gateway
    monkeypatch.setattr(settings, "ENC_AUTH_ENABLED", True)
    monkeypatch.setattr(enc_gateway, "_state", None)
    monkeypatch.setattr(settings, "ENC_AUTH_HASH_SECRET", "")
    await _all_unavailable(client, "no_hash_secret")
    monkeypatch.setattr(settings, "ENC_AUTH_HASH_SECRET", "x" * 32)
    monkeypatch.setattr(settings, "ENC_AUTH_IDENTITY_BACKEND", "no-such-backend")                # a typo can never fall back to the emulator
    monkeypatch.delenv("FIREBASE_AUTH_EMULATOR_HOST", raising=False)
    response = await client.post(f"{API}/auth/enc/login", content=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 503 and response.json() == {"error": "AUTH_UNAVAILABLE"}
    monkeypatch.setattr(settings, "ENC_AUTH_IDENTITY_BACKEND", "emulator")                       # emulator backend without a verified local emulator
    response = await client.post(f"{API}/auth/enc/login", content=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 503 and response.json() == {"error": "AUTH_UNAVAILABLE"}
    monkeypatch.setattr(settings, "ENC_AUTH_IDENTITY_BACKEND", "firebase")                       # real Firebase selected but not configured
    response = await client.post(f"{API}/auth/enc/login", content=b"{}", headers={"Content-Type": "application/json"})
    assert response.status_code == 503 and response.json() == {"error": "AUTH_UNAVAILABLE"}


async def test_enabled_with_malformed_keys_refuses(client, monkeypatch):
    from app.api import enc_gateway
    from app.services import enc_auth_identity as ident
    monkeypatch.setattr(settings, "ENC_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "ENC_AUTH_HASH_SECRET", "x" * 32)
    monkeypatch.setattr(settings, "ENC_AUTH_KEYS", "not json")
    monkeypatch.setattr(enc_gateway, "_state", None)
    monkeypatch.setattr(ident, "ready", lambda: True)
    await _all_unavailable(client, "bad_keys")


async def test_resolve_token_email_is_exactly_the_old_token_claim_while_the_gateway_is_off():
    from app.services.enc_auth_identity import resolve_token_email
    assert await resolve_token_email({"uid": "u", "email": " A@Example.com "}) == "A@Example.com"
    assert await resolve_token_email({"uid": "u"}) is None
    assert await resolve_token_email({"uid": "u", "email": ""}) is None


# ------------------------------------------------------------------------------- proof claims at /auth/mark-verified (G6b Option D)
def _use_claim_email(monkeypatch):
    """With the gateway enabled resolve_token_email asks the identity service (covered by the lab suites); here the token claim stands in for it."""
    from app.api import routes

    async def claim_email(current_user):
        return current_user.get("email")
    monkeypatch.setattr(routes, "resolve_token_email", claim_email)


async def test_mark_verified_refuses_a_proof_first_used_by_another_uid_but_only_when_the_gateway_is_enabled(client, mailbox, make_user, monkeypatch):
    from app.services import enc_auth_store as store
    token_a = await make_user("uidA", EMAIL)
    proof = await signup_proof(client, mailbox, EMAIL)

    async def mark(token):
        return await client.post(f"{API}/auth/mark-verified", headers={"Authorization": f"Bearer {token}"}, data={"proof": proof})

    # gateway disabled: the legacy path is untouched (claim_allows is a no-op that returns True without any database access)
    assert await store.claim_allows("uidB", proof) is True

    monkeypatch.setattr(settings, "ENC_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "ENC_AUTH_HASH_SECRET", "x" * 32)
    await _ensure_gateway_schema()                                        # a disabled startup creates no gateway table, so create them as an enabled one would
    _use_claim_email(monkeypatch)
    assert await store.bind_claim(proof, "uidA") is True
    assert await store.bind_claim(proof, "uidB") is False                 # the proof belongs to the first verified UID at the gateway
    token_b = await make_user("uidB", "uidb@example.com")
    assert (await mark(token_b)).status_code == 403                     # another UID presenting the claimed proof is refused ...
    assert await live(proof) is True                                    # ... and the proof is not consumed by that attempt
    ok = await mark(token_a)
    assert ok.status_code == 200 and ok.json() == {"status": "ok"}      # the claiming UID verifies once
    assert (await mark(token_a)).status_code == 200                     # (already verified answers 200 without a change)
    assert await live(proof) is False


async def test_mark_verified_answers_503_when_the_claim_cannot_be_checked(client, mailbox, make_user, monkeypatch):
    await make_user("uidA", EMAIL)
    proof = await signup_proof(client, mailbox, EMAIL)
    token = "tok:uidA"
    monkeypatch.setattr(settings, "ENC_AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "ENC_AUTH_HASH_SECRET", "")           # the claim store cannot be keyed: fail closed, consume nothing
    _use_claim_email(monkeypatch)
    response = await client.post(f"{API}/auth/mark-verified", headers={"Authorization": f"Bearer {token}"}, data={"proof": proof})
    assert response.status_code == 503
    assert await live(proof) is True
