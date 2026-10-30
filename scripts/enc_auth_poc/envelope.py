"""Offline proof of concept: an encrypted credential envelope (RSA-OAEP-SHA256 key wrap + AES-256-GCM).

Purpose: the e-mail and password of a login, signup, re-authentication or password change travel inside this envelope, so a
TLS-intercepting proxy sees only ciphertext. Only well-known primitives of the `cryptography` package are used; nothing is
implemented by hand. Not connected to Firebase, the network, the environment or any file.

What it does NOT do: protect against a compromised device or runtime instrumentation (the secret exists in clear on the device
before it is sealed), and it does not stop an attacker who can reach the gateway from sending their OWN credentials.

Wire format (one JSON object, all values strings except `v` and `ts`):
    {"v":1,"kid":...,"op":...,"nonce":...,"ts":...,"ek":...,"iv":...,"ct":...}
    ek = RSA-OAEP(SHA-256, MGF1-SHA-256, fixed label) of a fresh random 32-byte AES key
    ct = AES-256-GCM(key, iv, JSON plaintext, AAD)   (the 16-byte tag is the tail of ct)
    AAD = "EVENC" | version | for each of (kid, op, nonce, ts): 2-byte length + ASCII bytes
so the key id, operation, server nonce and timestamp are authenticated: changing any of them makes decryption fail.
"""
import base64
import json
import os
import re
import struct
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1

# operation -> exact set of plaintext fields (strict: missing or extra fields are rejected)
OPERATIONS = {
    "login": ("email", "password"),
    "signup": ("email", "password"),
    "reauth": ("email", "password"),
    "change_password": ("email", "password", "new_password"),  # `password` is the CURRENT password
}

MAX_ENVELOPE_BYTES = 8192
MAX_PLAINTEXT_BYTES = 2048
MAX_EMAIL_CHARS = 254
MAX_PASSWORD_CHARS = 256
MAX_TS_SKEW_SECONDS = 300
RSA_MIN_BITS = 3072
AES_KEY_BYTES = 32
IV_BYTES = 12
TAG_BYTES = 16

_OAEP_LABEL = b"epicverse-enc-auth-v1"
_AAD_MAGIC = b"EVENC"
_KID_RE = re.compile(r"\A[A-Za-z0-9._-]{1,64}\Z")
_NONCE_RE = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")  # 32 random bytes, base64url without padding
_B64U_RE = re.compile(r"\A[A-Za-z0-9_-]*\Z")
_ENVELOPE_FIELDS = frozenset({"v", "kid", "op", "nonce", "ts", "ek", "iv", "ct"})

# Error codes. They never carry request data. A gateway should return ONE generic external error for all of them.
MALFORMED = "MALFORMED"
TOO_LARGE = "TOO_LARGE"
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
UNKNOWN_KEY = "UNKNOWN_KEY"
WRONG_OPERATION = "WRONG_OPERATION"
EXPIRED = "EXPIRED"
UNKNOWN_NONCE = "UNKNOWN_NONCE"
REPLAY = "REPLAY"
DECRYPT_FAILED = "DECRYPT_FAILED"
CAPACITY = "CAPACITY"


class EnvelopeError(Exception):
    """Carries only a fixed error code, never a value taken from the request."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def __str__(self):
        return self.code


def b64e(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(value, max_chars):
    if type(value) is not str:
        raise EnvelopeError(MALFORMED)
    if len(value) > max_chars:
        raise EnvelopeError(TOO_LARGE)
    if not _B64U_RE.match(value) or len(value) % 4 == 1:
        raise EnvelopeError(MALFORMED)
    out = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if b64e(out) != value:  # reject non-canonical encodings
        raise EnvelopeError(MALFORMED)
    return out


def _oaep():
    return padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=_OAEP_LABEL)


def _aad(kid, operation, nonce, ts):
    out = bytearray(_AAD_MAGIC)
    out.append(VERSION)
    for part in (kid, operation, nonce, str(ts)):
        raw = part.encode("ascii")
        out += struct.pack(">H", len(raw)) + raw
    return bytes(out)


def _check_text(value, max_chars):
    if type(value) is not str or not value or len(value) > max_chars:
        raise EnvelopeError(MALFORMED if type(value) is not str or not value else TOO_LARGE)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise EnvelopeError(MALFORMED)


def _validate_fields(operation, fields):
    if operation not in OPERATIONS or type(fields) is not dict or set(fields) != set(OPERATIONS[operation]):
        raise EnvelopeError(MALFORMED)
    for name, value in fields.items():
        _check_text(value, MAX_EMAIL_CHARS if name == "email" else MAX_PASSWORD_CHARS)
    if "@" not in fields["email"]:
        raise EnvelopeError(MALFORMED)


def _seal_bytes(public_key, kid, operation, nonce, ts, plaintext):
    """Encrypts raw plaintext bytes. Split out so tests can build envelopes the validating front door would refuse."""
    if not isinstance(public_key, rsa.RSAPublicKey) or public_key.key_size < RSA_MIN_BITS:
        raise EnvelopeError(MALFORMED)
    if not _KID_RE.match(kid) or operation not in OPERATIONS or not _NONCE_RE.match(nonce):
        raise EnvelopeError(MALFORMED)
    key = AESGCM.generate_key(bit_length=256)
    iv = os.urandom(IV_BYTES)
    ct = AESGCM(key).encrypt(iv, plaintext, _aad(kid, operation, nonce, ts))
    ek = public_key.encrypt(key, _oaep())
    return json.dumps({"v": VERSION, "kid": kid, "op": operation, "nonce": nonce, "ts": ts,
                       "ek": b64e(ek), "iv": b64e(iv), "ct": b64e(ct)}, separators=(",", ":"))


def seal_credentials(public_key, kid, operation, nonce, fields, now=None):
    """Client side (a Dart port is a later gate). `nonce` must be a fresh nonce the server issued for `operation`."""
    _validate_fields(operation, fields)
    plaintext = json.dumps(fields, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise EnvelopeError(TOO_LARGE)
    ts = int(time.time() if now is None else now)
    return _seal_bytes(public_key, kid, operation, nonce, ts, plaintext)


class KeyRing:
    """Server-side private keys by key id. `active=False` keys still decrypt (rotation overlap); remove them to retire."""

    def __init__(self):
        self._keys = {}

    def add(self, kid, private_key, active=True):
        if not _KID_RE.match(kid) or not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < RSA_MIN_BITS:
            raise ValueError("invalid key or key id")
        self._keys[kid] = (private_key, active)

    def remove(self, kid):
        self._keys.pop(kid, None)

    def get(self, kid):
        entry = self._keys.get(kid)
        return entry[0] if entry else None


@dataclass(frozen=True)
class Credentials:
    operation: str
    kid: str
    fields: dict

    @property
    def email(self):
        return self.fields["email"]

    @property
    def password(self):
        return self.fields["password"]

    @property
    def new_password(self):
        return self.fields.get("new_password")

    def __repr__(self):
        return "Credentials(operation=%s, kid=%s, <redacted>)" % (self.operation, self.kid)

    __str__ = __repr__


def _strict_json(data):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out

    def reject_constant(_name):
        raise ValueError("non-finite number")

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
    except (ValueError, RecursionError):  # includes UnicodeDecodeError and JSONDecodeError
        raise EnvelopeError(MALFORMED) from None


def open_credentials(raw, keyring, nonces, expected_operation, now=None):
    """Server side. Returns Credentials or raises EnvelopeError.

    Order matters: all cheap checks and a read-only nonce check come BEFORE the RSA operation, and the nonce is consumed only
    AFTER the ciphertext authenticated. A forged or tampered request therefore cannot burn someone else's nonce.
    """
    if isinstance(raw, str):
        try:
            raw = raw.encode("utf-8")
        except UnicodeEncodeError:
            raise EnvelopeError(MALFORMED) from None
    if not isinstance(raw, (bytes, bytearray)):
        raise EnvelopeError(MALFORMED)
    if len(raw) > MAX_ENVELOPE_BYTES:
        raise EnvelopeError(TOO_LARGE)

    env = _strict_json(bytes(raw))
    if type(env) is not dict or set(env) != _ENVELOPE_FIELDS:
        raise EnvelopeError(MALFORMED)
    version, kid, operation, nonce, ts = env["v"], env["kid"], env["op"], env["nonce"], env["ts"]
    if type(version) is not int:
        raise EnvelopeError(MALFORMED)
    if version != VERSION:
        raise EnvelopeError(UNSUPPORTED_VERSION)
    if type(kid) is not str or not _KID_RE.match(kid) or type(nonce) is not str or not _NONCE_RE.match(nonce):
        raise EnvelopeError(MALFORMED)
    if type(operation) is not str or operation not in OPERATIONS or type(ts) is not int or not 0 <= ts < 2 ** 40:
        raise EnvelopeError(MALFORMED)
    iv = _b64d(env["iv"], 32)
    ct = _b64d(env["ct"], 4 * (MAX_PLAINTEXT_BYTES + TAG_BYTES) // 3 + 8)
    if len(iv) != IV_BYTES or not TAG_BYTES < len(ct) <= MAX_PLAINTEXT_BYTES + TAG_BYTES:
        raise EnvelopeError(TOO_LARGE if len(ct) > MAX_PLAINTEXT_BYTES + TAG_BYTES else MALFORMED)

    private_key = keyring.get(kid)
    if private_key is None:
        raise EnvelopeError(UNKNOWN_KEY)
    ek = _b64d(env["ek"], 4 * (private_key.key_size // 8) // 3 + 8)
    if len(ek) != private_key.key_size // 8:
        raise EnvelopeError(MALFORMED)

    if operation != expected_operation:
        raise EnvelopeError(WRONG_OPERATION)
    current = nonces.now() if now is None else int(now)
    if abs(current - ts) > MAX_TS_SKEW_SECONDS:
        raise EnvelopeError(EXPIRED)
    nonces.inspect(nonce, operation)  # unknown / expired / already used / issued for another operation

    try:
        key = private_key.decrypt(ek, _oaep())
        if len(key) != AES_KEY_BYTES:
            raise ValueError("bad key length")
        plaintext = AESGCM(key).decrypt(iv, ct, _aad(kid, operation, nonce, ts))
    except (ValueError, InvalidTag):
        raise EnvelopeError(DECRYPT_FAILED) from None  # one code for every RSA or AES failure, so it is no oracle

    nonces.consume(nonce, operation)  # atomic: of several identical requests exactly one gets past this line

    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise EnvelopeError(TOO_LARGE)
    fields = _strict_json(plaintext)
    _validate_fields(operation, fields)
    return Credentials(operation=operation, kid=kid, fields=fields)
