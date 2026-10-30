"""G8 revocation edge cases. Pure functions, standard library only, no network.

Firebase stores the revocation time (`validSince`) in WHOLE SECONDS and rejects an ID token only when `iat < validSince`. A token issued in the
same second as the revocation is therefore accepted although it was issued "before" the revoke, and the local clock is not Firebase's clock.
So every outcome is classified into exactly one of:

    REVOKED     the token was rejected and it is provably older than the revocation window  -> counts as a pass for "revocation works"
    VALID       the token was accepted and it is provably newer than the revocation window  -> counts as a pass for "fresh login works"
    FAILED      the answer contradicts what revocation guarantees (an old token accepted, a new one rejected)
    AMBIGUOUS   the timestamps are too close (or the answer could not be obtained) to tell. NEVER a pass; reported as such.

`window` is the local time range [before, after] (whole seconds) in which the revoke call ran; SKEW_SECONDS widens it on both sides for
clock difference and second-granularity rounding.
"""
SKEW_SECONDS = 2

REVOKED, VALID, FAILED, AMBIGUOUS = "revoked", "valid", "failed", "ambiguous"
PASSING = frozenset({REVOKED, VALID})


def window(before, after):
    """Local epoch-seconds before and after the revoke call -> an integer window; refuses nonsense (returns None)."""
    if isinstance(before, bool) or isinstance(after, bool) or not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    if after < before or before <= 0:
        return None
    return int(before), int(after)


def classify_old_token(answer, token_iat, revoke_window):
    """A token that was issued BEFORE the revoke. `answer`: "rejected" | "accepted" | anything else (unavailable/unknown)."""
    if revoke_window is None or not isinstance(token_iat, int) or isinstance(token_iat, bool):
        return AMBIGUOUS
    start, end = revoke_window
    if token_iat > end + SKEW_SECONDS:
        return FAILED                         # not an "old" token at all: the test itself is wrong
    provably_older = token_iat < start - SKEW_SECONDS
    if answer == "rejected":
        return REVOKED if provably_older else AMBIGUOUS         # rejected, but could be unrelated (clock) - never silently a pass
    if answer == "accepted":
        return FAILED if provably_older else AMBIGUOUS           # same-second token accepted: the documented edge case
    return AMBIGUOUS


def classify_new_token(answer, token_iat, revoke_window):
    """A token that was issued AFTER the revoke (a fresh sign-in). `answer`: "accepted" | "rejected" | other."""
    if revoke_window is None or not isinstance(token_iat, int) or isinstance(token_iat, bool):
        return AMBIGUOUS
    _, end = revoke_window
    provably_newer = token_iat > end + SKEW_SECONDS
    if answer == "accepted":
        return VALID if provably_newer else AMBIGUOUS
    if answer == "rejected":
        return FAILED if provably_newer else AMBIGUOUS
    return AMBIGUOUS


def classify_refresh(error_code, revoke_window, attempted_at):
    """Refresh with a refresh token issued before the revoke. Firebase answers TOKEN_EXPIRED once the revocation applies."""
    if revoke_window is None or isinstance(attempted_at, bool) or not isinstance(attempted_at, (int, float)):
        return AMBIGUOUS
    _, end = revoke_window
    late_enough = int(attempted_at) > end + SKEW_SECONDS
    if error_code == "TOKEN_EXPIRED":
        return REVOKED
    if error_code in (None, "-"):                            # the refresh SUCCEEDED
        return FAILED if late_enough else AMBIGUOUS
    return AMBIGUOUS                                         # any other code: not evidence either way


def is_pass(classification):
    return classification in PASSING
