"""Shared helpers for the G3 Phase 1 tests: a NETWORK DENIAL layer installed on import, and synthetic builders (keys, passwords, JWTs).

Importing this module makes every attempt to resolve a name or open a socket raise NetworkDenied *before* anything leaves the process, and
records the attempt. Nothing here (or in the tests) connects anywhere, not even to check that the network is blocked: the denial is
verified by calling the patched functions, which raise immediately.
"""
import base64
import json
import secrets
import socket
import ssl
import sys
from pathlib import Path

G3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(G3))

ATTEMPTS = []


class NetworkDenied(BaseException):
    """Raised by the denial layer. It derives from BaseException so that no `except Exception` in the code under test can swallow it."""


def _deny(name):
    def denied(*args, **kwargs):
        ATTEMPTS.append(name)
        raise NetworkDenied("network access attempted: " + name)
    return denied


def install_network_denial():
    for owner, name in ((socket, "create_connection"), (socket, "getaddrinfo"), (socket, "gethostbyname"), (socket, "gethostbyname_ex"),
                        (socket.socket, "connect"), (socket.socket, "connect_ex"), (ssl.SSLContext, "wrap_socket")):
        setattr(owner, name, _deny("%s.%s" % (getattr(owner, "__name__", "obj"), name)))


install_network_denial()

ALLOWED = "epicverse-g3-auth-test"


def synthetic_api_key():
    """Right shape ('AIza' + 35 URL-safe characters), random, assembled at run time so no key-shaped literal exists in any source file."""
    return "AI" + "za" + secrets.token_urlsafe(40).replace("=", "")[:35]


def synthetic_password():
    return "Pw-" + secrets.token_urlsafe(12)


def synthetic_email():
    return "g3-%s@example.invalid" % secrets.token_hex(4)


def b64(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_jwt(payload=None, header=None, signature=None, project=ALLOWED, now=1_800_000_000, raw_payload=None):
    """A SYNTHETIC token with a made-up signature. It is not signed by anything and verifies against nothing."""
    header = {"alg": "RS256", "kid": "synthetic-kid", "typ": "JWT"} if header is None else header
    if payload is None:
        payload = {"iss": "https://securetoken.google.com/" + project, "aud": project, "sub": "synthetic-uid-1", "user_id": "synthetic-uid-1",
                   "iat": now - 60, "exp": now + 3540, "auth_time": now - 120, "firebase": {"identities": {}, "sign_in_provider": "password"}}
    body = raw_payload if raw_payload is not None else json.dumps(payload).encode()
    sig = b64(secrets.token_bytes(64)) if signature is None else signature
    return "%s.%s.%s" % (b64(json.dumps(header).encode()), b64(body), sig)


# ---- synthetic PKI for Phase 2: generated at run time, in memory, with the locally installed `cryptography`; never written anywhere ----
def make_pki(kid="synthetic-kid-1", bits=2048, not_before=None, not_after=None, curve=False):
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1()) if curve else rsa.generate_private_key(public_exponent=65537, key_size=bits)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic")])
    before = not_before or datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
    after = not_after or datetime.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(before).not_valid_after(after).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.PEM).decode("ascii"), kid


def signed_claims(project=ALLOWED, now=1_800_000_000, **changes):
    claims = {"iss": "https://securetoken.google.com/" + project, "aud": project, "sub": "synthetic-uid-1", "user_id": "synthetic-uid-1",
              "iat": now - 60, "exp": now + 3540, "auth_time": now - 120, "firebase": {"identities": {}, "sign_in_provider": "password"},
              "email": "g3-user@example.invalid", "email_verified": False}
    claims.update(changes)
    return {k: v for k, v in claims.items() if v is not ...}


def sign_rs256(key, header, raw_payload=None, **claim_changes):
    """A token really signed (RS256) with a SYNTHETIC key. `claim_changes` may include project= and now=; use ... to delete a claim."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    project = claim_changes.pop("project", ALLOWED)
    now = claim_changes.pop("now", 1_800_000_000)
    body = raw_payload if raw_payload is not None else json.dumps(signed_claims(project, now, **claim_changes)).encode()
    signing_input = b64(json.dumps(header).encode()) + "." + b64(body)
    signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + b64(signature)
