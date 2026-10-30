"""G3 token inspection. Standard library only. No network, no key download.

DECODING IS NOT VERIFYING. decode_unverified() reads the claims of a JWT and checks their SHAPE and plausibility (issuer, audience,
times). It does NOT check the signature, so nothing it returns is authenticated and every result carries verified=False. Only a
SignatureVerifier (a separate, future interface that needs real Firebase signing keys) may produce VerifiedClaims. Phase 1 has no
working verifier on purpose: Phase1Verifier always refuses.
"""
import base64
import binascii
import json

from g3_guard import validate_project

MAX_TOKEN_CHARS = 4096
MAX_SEGMENT_CHARS = 3072
CLOCK_SKEW_SECONDS = 300
MAX_LIFETIME_SECONDS = 24 * 3600
_B64URL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


class TokenInspectionError(Exception):
    """Fixed reason code only; never any part of the token."""

    def __init__(self, reason):
        super().__init__("token rejected: %s" % reason)
        self.reason = reason


class SignatureVerificationUnavailable(Exception):
    pass


def _b64url_decode(segment, reason):
    if not segment or len(segment) > MAX_SEGMENT_CHARS or not set(segment) <= _B64URL or len(segment) % 4 == 1:
        raise TokenInspectionError(reason)
    try:
        return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        raise TokenInspectionError(reason) from None


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _json_object(raw, reason):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise TokenInspectionError(reason) from None
    if not isinstance(value, dict):
        raise TokenInspectionError(reason)
    return value


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value, limit):
    return isinstance(value, str) and 0 < len(value) <= limit


class UnverifiedClaims:
    """Plausible, UNAUTHENTICATED claims. verified is always False."""
    verified = False
    __slots__ = ("uid", "issued_at", "expires_at", "auth_time", "email", "email_verified", "sign_in_provider")

    def __init__(self, uid, issued_at, expires_at, auth_time, email, email_verified, sign_in_provider):
        self.uid, self.issued_at, self.expires_at, self.auth_time = uid, issued_at, expires_at, auth_time
        self.email, self.email_verified, self.sign_in_provider = email, email_verified, sign_in_provider

    def __repr__(self):
        return "UnverifiedClaims(verified=False, has_email=%s, provider=%r)" % (self.email is not None, self.sign_in_provider)


def decode_unverified(token, expected_project, now):
    """Shape and plausibility checks on an ID token. `now` (epoch seconds) is passed in so the result never depends on a clock."""
    validate_project(expected_project)
    if not isinstance(token, str) or not token:
        raise TokenInspectionError("token_missing")
    if len(token) > MAX_TOKEN_CHARS:
        raise TokenInspectionError("token_too_large")
    if isinstance(now, bool) or not isinstance(now, int):
        raise TokenInspectionError("clock_invalid")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenInspectionError("token_malformed")
    header = _json_object(_b64url_decode(parts[0], "header_malformed"), "header_malformed")
    payload = _json_object(_b64url_decode(parts[1], "payload_malformed"), "payload_malformed")
    _b64url_decode(parts[2], "signature_malformed")            # present and well-formed; NOT checked against any key

    if header.get("alg") != "RS256":                           # rejects "none", HS256 and anything else (emulator tokens are unsigned)
        raise TokenInspectionError("header_algorithm")
    if not _text(header.get("kid"), 128) or header.get("typ", "JWT") != "JWT":
        raise TokenInspectionError("header_fields")

    if payload.get("iss") != "https://securetoken.google.com/" + expected_project:
        raise TokenInspectionError("issuer_mismatch")
    if payload.get("aud") != expected_project:
        raise TokenInspectionError("audience_mismatch")
    sub = payload.get("sub")
    if not _text(sub, 128) or payload.get("user_id", sub) != sub:
        raise TokenInspectionError("subject_invalid")
    iat, exp, auth_time = payload.get("iat"), payload.get("exp"), payload.get("auth_time")
    if not (_is_int(iat) and _is_int(exp) and iat > 0 and exp > iat and exp - iat <= MAX_LIFETIME_SECONDS):
        raise TokenInspectionError("time_claims_invalid")
    if not _is_int(auth_time) or auth_time <= 0 or auth_time > iat or auth_time > now + CLOCK_SKEW_SECONDS:
        raise TokenInspectionError("auth_time_invalid")
    if iat > now + CLOCK_SKEW_SECONDS:
        raise TokenInspectionError("issued_in_future")
    if exp <= now:
        raise TokenInspectionError("token_expired")

    firebase = payload.get("firebase")
    if not isinstance(firebase, dict) or not _text(firebase.get("sign_in_provider"), 64):
        raise TokenInspectionError("firebase_claim_invalid")
    email = payload.get("email")
    if email is not None and not _text(email, 254):
        raise TokenInspectionError("email_claim_invalid")
    email_verified = payload.get("email_verified")
    if email_verified is not None and not isinstance(email_verified, bool):
        raise TokenInspectionError("email_verified_claim_invalid")
    return UnverifiedClaims(sub, iat, exp, auth_time, email, email_verified, firebase["sign_in_provider"])


_VERIFIED_KEY = object()


class VerifiedClaims:
    """Claims whose signature WAS checked. Only a SignatureVerifier implementation holds the key needed to build one."""
    verified = True
    __slots__ = ("claims",)

    def __init__(self, claims, _key=None):
        if _key is not _VERIFIED_KEY:
            raise TypeError("VerifiedClaims can only be produced by a SignatureVerifier")
        self.claims = claims


class SignatureVerifier:
    """Future interface (needs separate approval): verify the RS256 signature against Firebase's published signing keys, then run the
    same claim checks. An implementation must return VerifiedClaims and raise on any failure."""

    def verify(self, token, expected_project, now):
        raise NotImplementedError


class Phase1Verifier(SignatureVerifier):
    """Phase 1 has no keys and no network: it always refuses, so nothing can be mistaken for an authenticated token."""

    def verify(self, token, expected_project, now):
        raise SignatureVerificationUnavailable("signature verification is not available in Phase 1")
