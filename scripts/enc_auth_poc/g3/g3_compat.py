"""G3 custom-token compatibility helpers (offline, pure). NOT used by any live path yet.

For the NEXT approved live test (custom-token sign-in, not approved in this phase): these functions take claims that were ALREADY
signature-verified (g3_verify) and turn them into a safe, value-free observation record and a set of checks against what the EpicVerse
backend assumes (/sync-user and /auth/mark-verified read the e-mail from the verified token; the G6 helper tolerates a missing claim).
They mint, sign, exchange and request nothing.
"""
from g3_token_inspect import VerifiedClaims


def _claims(verified):
    if not isinstance(verified, VerifiedClaims):
        raise TypeError("only signature-verified claims can be observed")
    return verified.claims


def observe(verified, expected_uid):
    """Safe booleans/categories about one verified session token. `expected_uid` is compared locally and never echoed."""
    c = _claims(verified)
    return {
        "uid_matches_expected": c.uid == expected_uid,
        "email_claim_present": c.email is not None,
        "email_verified": "absent" if c.email_verified is None else ("true" if c.email_verified else "false"),
        "sign_in_provider": c.sign_in_provider if c.sign_in_provider in {"password", "custom", "anonymous", "google.com", "phone"} else "other",
        "auth_time_present": True,
        "auth_time_is_recent": c.issued_at - c.auth_time <= 300,          # relevant to recent-login rules
        "exp_after_iat": c.expires_at > c.issued_at,
    }


def compare_sessions(before, after):
    """Refresh behaviour: what a refresh preserved. Inputs are two verified tokens (for example custom-token ID token, then refreshed one)."""
    a, b = _claims(before), _claims(after)
    return {"uid_unchanged": a.uid == b.uid, "auth_time_preserved": a.auth_time == b.auth_time, "iat_not_earlier": b.issued_at >= a.issued_at,
            "provider_unchanged": a.sign_in_provider == b.sign_in_provider, "email_claim_presence_unchanged": (a.email is None) == (b.email is None)}


def backend_assumptions(observation):
    """Which EpicVerse backend assumptions a custom-token session would satisfy (see the plan in CUSTOM_TOKEN_PLAN.md)."""
    has_email = observation["email_claim_present"]
    return {
        "sync_user_reads_email_claim_directly": has_email,                       # deployed /sync-user: breaks without the claim unless the G6a helper is used
        "mark_verified_reads_email_claim_directly": has_email,
        "g6_helper_needed": not has_email,
        "provider_is_custom": observation["sign_in_provider"] == "custom",       # code that keys on sign_in_provider == "password" would differ
        "recent_login_fresh": observation["auth_time_is_recent"],
    }
