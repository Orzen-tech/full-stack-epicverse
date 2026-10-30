"""G3 custom-token OFFLINE harness: builds and validates the claims and the signing input of a Firebase custom token, and assembles a token
from a signature. It contains NO signer for any real key, no IAM call and no network use: the only signer here is the abstract interface
(tests plug in a synthetic in-memory key). Nothing is signed or exchanged for real in this phase.

Format (Firebase custom tokens): JWT, alg RS256; claims iss = sub = the service-account e-mail, aud = the Identity Toolkit audience,
iat/exp (lifetime at most one hour), uid (1-128 characters), optional developer claims (reserved names forbidden, small).
"""
import base64
import json

import g3_guard as guard

AUDIENCE = "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit"
MAX_LIFETIME_SECONDS = 3600
MAX_UID_CHARS = 128
MAX_DEVELOPER_CLAIMS_BYTES = 800
RESERVED_CLAIMS = frozenset({"acr", "amr", "at_hash", "aud", "auth_time", "azp", "cnf", "c_hash", "exp", "firebase", "iat", "iss", "jti", "nbf", "nonce",
                             "sub", "uid", "email", "email_verified"})      # e-mail claims are refused here on purpose: the test must observe what Firebase itself adds


class CustomTokenError(Exception):
    """Fixed reason code only; never a uid, e-mail or token."""

    def __init__(self, reason):
        super().__init__("custom token refused: %s" % reason)
        self.reason = reason


class Signer:
    """Interface for a future, separately approved signer (IAM-based signing or a short-lived key). Phase 4 provides none."""

    def sign(self, signing_input):
        raise NotImplementedError


def _b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def validate_service_account(email, project_id=guard.ALLOWED_PROJECT_ID):
    guard.validate_project(project_id)
    try:
        return guard.validate_service_account(email)               # one implementation, in the guard
    except guard.GuardError as refusal:
        raise CustomTokenError(refusal.reason) from None


def build_claims(service_account, uid, now, lifetime=MAX_LIFETIME_SECONDS, developer_claims=None):
    validate_service_account(service_account)
    if not isinstance(uid, str) or not 1 <= len(uid) <= MAX_UID_CHARS or any(ord(c) < 33 or ord(c) > 126 for c in uid):
        raise CustomTokenError("uid_invalid")
    if isinstance(now, bool) or not isinstance(now, int) or now <= 0:
        raise CustomTokenError("clock_invalid")
    if isinstance(lifetime, bool) or not isinstance(lifetime, int) or not 60 <= lifetime <= MAX_LIFETIME_SECONDS:
        raise CustomTokenError("lifetime_invalid")
    claims = {"iss": service_account, "sub": service_account, "aud": AUDIENCE, "iat": now, "exp": now + lifetime, "uid": uid}
    if developer_claims is not None:
        if not isinstance(developer_claims, dict) or set(developer_claims) & RESERVED_CLAIMS or not all(isinstance(k, str) for k in developer_claims):
            raise CustomTokenError("developer_claims_invalid")
        encoded = json.dumps(developer_claims, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_DEVELOPER_CLAIMS_BYTES:
            raise CustomTokenError("developer_claims_too_large")
        claims["claims"] = developer_claims
    return claims


def signing_input(claims):
    """header.payload as ASCII bytes - the exact bytes a signer must sign (RS256)."""
    header = {"alg": "RS256", "typ": "JWT"}
    return (_b64(json.dumps(header, separators=(",", ":")).encode()) + "." + _b64(json.dumps(claims, separators=(",", ":")).encode())).encode("ascii")


def assemble(data, signature):
    if not isinstance(data, bytes) or data.count(b".") != 1 or not isinstance(signature, bytes) or not 128 <= len(signature) <= 1024:
        raise CustomTokenError("signature_invalid")
    return data.decode("ascii") + "." + _b64(signature)


def mint_with(signer, service_account, uid, now, **kwargs):
    """Builds the token through an injected signer. Offline tests pass a synthetic one; no real signer exists in this phase."""
    if not isinstance(signer, Signer):
        raise CustomTokenError("signer_invalid")
    data = signing_input(build_claims(service_account, uid, now, **kwargs))
    return assemble(data, signer.sign(data))
