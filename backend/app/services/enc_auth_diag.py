"""Safe diagnostics for the encrypted-credential gateway: a request correlation id, FIXED stage and cause codes, counters and timings.

Nothing here can record a value taken from a request (no e-mail, password, proof, token, nonce, envelope content, key or uid): the only
inputs are codes from the closed vocabularies below, anything else is replaced by "other". Pure module (standard library only).
"""
import contextvars
import secrets
from collections import Counter

STAGES = frozenset({
    "gateway", "config", "envelope", "limiter", "nonce", "proof", "claim", "verify_password", "get_account", "create_user",
    "update_password", "revoke", "mint", "verify_token", "token_email", "overall", "db", "request",
})
CAUSES = frozenset({
    # configuration
    "disabled", "no_hash_secret", "not_local", "bad_keys",
    # transport to the identity service
    "connect", "refused", "reset", "dns", "connect_timeout", "read_timeout", "transport", "http_502", "http_503", "http_504", "http_5xx",
    "http_4xx",
    # identity SDK
    "sdk_unavailable", "sdk_timeout", "sdk_error",
    # database
    "pool", "db_unavailable", "db_timeout",
    # gateway
    "timeout", "capacity", "unexpected", "uid_mismatch", "email_mismatch", "claim_conflict", "limited",
    # real-Firebase adapter (G7)
    "not_configured", "bad_response", "unauthenticated", "permission_denied", "service_disabled", "rate_limited",
    # envelope verdicts (server-side log only; the client always sees the same generic answer)
    "malformed", "too_large", "unsupported_version", "unknown_key", "wrong_operation", "expired", "unknown_nonce", "replay",
    "decrypt_failed",
    "-",
})
KINDS = frozenset({"unavailable", "retry", "retry_success", "outcome"})

_request_id = contextvars.ContextVar("enc_auth_request_id", default="-")
STATS = Counter()
TIMINGS = {}          # stage -> [count, total_ms, max_ms]


def _stage(value):
    return value if value in STAGES else "other"


def _cause(value):
    return value if value in CAUSES else "other"


def new_request_id():
    """A random 12-hex-digit id, unrelated to any user or request content. Returned in the X-Enc-Request-Id response header."""
    rid = secrets.token_hex(6)
    _request_id.set(rid)
    return rid


def request_id():
    return _request_id.get()


def note(kind, stage="-", cause="-"):
    STATS[(kind if kind in KINDS else "other", _stage(stage) if stage != "-" else "-", _cause(cause))] += 1


def observe(stage, milliseconds):
    entry = TIMINGS.setdefault(_stage(stage), [0, 0.0, 0.0])
    entry[0] += 1
    entry[1] += milliseconds
    entry[2] = max(entry[2], milliseconds)


def snapshot():
    return {
        "counts": {"%s|%s|%s" % key: n for key, n in sorted(STATS.items())},
        "timings": {stage: {"n": v[0], "avg_ms": round(v[1] / v[0], 1) if v[0] else 0.0, "max_ms": round(v[2], 1)}
                    for stage, v in sorted(TIMINGS.items())},
    }


def reset():
    STATS.clear()
    TIMINGS.clear()


def line(op, outcome, stage="-", cause="-", milliseconds=None):
    text = "[ENC-AUTH] rid=%s op=%s outcome=%s stage=%s cause=%s" % (request_id(), op if op in {"login", "signup", "reauth", "change_password",
                                                                                              "nonce", "token_email"} else "other",
                                                                    outcome if outcome in {"ok", "refused", "unavailable", "error"} else "other",
                                                                    _stage(stage) if stage != "-" else "-", _cause(cause))
    return text + (" ms=%d" % milliseconds if milliseconds is not None else "")


def log(op, outcome, stage="-", cause="-", milliseconds=None):
    print(line(op, outcome, stage, cause, milliseconds), flush=True)
