"""G3 Phase 2 signature verification of Firebase ID tokens against Firebase's PUBLIC signing certificates (cryptography package only).

Order is deliberate: header (algorithm, key id) -> signature -> ONLY THEN the claims. A token whose signature does not verify never has
its claims examined. RS256 is the sole accepted algorithm and no symmetric primitive is used anywhere, so algorithm-confusion attacks
(HS256 with the public key as secret, "none") have nothing to attach to. Makes no network call: the certificate map is passed in.
"""
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import g3_token_inspect as ti
from g3_guard import validate_project

MIN_RSA_BITS = 2048


class CertificateRejected(Exception):
    def __init__(self, reason):
        super().__init__("certificate rejected: %s" % reason)
        self.reason = reason


def public_key_from_certificate(pem, now):
    """RSA public key of a PEM certificate that is currently valid. Nothing from the certificate is returned except the key."""
    if not isinstance(pem, str) or not 0 < len(pem) <= 8192:
        raise CertificateRejected("certificate_malformed")
    try:
        cert = x509.load_pem_x509_certificate(pem.encode("ascii"))
        key = cert.public_key()
        not_before, not_after = int(cert.not_valid_before_utc.timestamp()), int(cert.not_valid_after_utc.timestamp())
    except Exception:
        raise CertificateRejected("certificate_malformed") from None
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < MIN_RSA_BITS:
        raise CertificateRejected("certificate_key_unacceptable")
    if not not_before <= now <= not_after:
        raise CertificateRejected("certificate_not_valid_now")
    return key


class FirebaseSignatureVerifier(ti.SignatureVerifier):
    """Verifies RS256 ID tokens against a certificate map {kid: PEM} that was fetched over verified HTTPS from the approved endpoint."""

    def __init__(self, certificates):
        if not isinstance(certificates, dict) or not certificates:
            raise CertificateRejected("certificates_missing")
        self._certificates = dict(certificates)

    def verify(self, token, expected_project, now):
        validate_project(expected_project)
        if not isinstance(token, str) or not token:
            raise ti.TokenInspectionError("token_missing")
        if len(token) > ti.MAX_TOKEN_CHARS:
            raise ti.TokenInspectionError("token_too_large")
        if isinstance(now, bool) or not isinstance(now, int):
            raise ti.TokenInspectionError("clock_invalid")
        parts = token.split(".")
        if len(parts) != 3:
            raise ti.TokenInspectionError("token_malformed")
        header = ti._json_object(ti._b64url_decode(parts[0], "header_malformed"), "header_malformed")
        if header.get("alg") != "RS256":
            raise ti.TokenInspectionError("header_algorithm")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ti.TokenInspectionError("kid_missing")
        if kid not in self._certificates:
            raise ti.TokenInspectionError("kid_unknown")
        signature = ti._b64url_decode(parts[2], "signature_malformed")
        try:
            key = public_key_from_certificate(self._certificates[kid], now)
        except CertificateRejected as rejected:
            raise ti.TokenInspectionError(rejected.reason) from None
        try:
            key.verify(signature, (parts[0] + "." + parts[1]).encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            raise ti.TokenInspectionError("signature_invalid") from None
        except Exception:
            raise ti.TokenInspectionError("signature_invalid") from None
        claims = ti.decode_unverified(token, expected_project, now)         # claims are examined only AFTER the signature verified
        return ti.VerifiedClaims(claims, _key=ti._VERIFIED_KEY)


def safe_summary(verified):
    """Booleans and claim TYPES only: no uid, e-mail or time values."""
    c = verified.claims
    return {"email_claim_present": c.email is not None,
            "email_verified": "absent" if c.email_verified is None else ("true" if c.email_verified else "false"),
            "sign_in_provider": c.sign_in_provider if c.sign_in_provider in {"password", "custom", "anonymous", "google.com", "phone"} else "other",
            "exp_after_iat": c.expires_at > c.issued_at, "auth_time_present": True}
