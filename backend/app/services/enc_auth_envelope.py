"""Encrypted credential envelope, SERVER side (protocol version 1, identical to scripts/enc_auth_poc/envelope.py).

RSA-OAEP-SHA256 (MGF1-SHA256, label "epicverse-enc-auth-v1") wraps a fresh AES-256-GCM key; the key id, operation, server nonce
and timestamp are authenticated data. This module has NO application imports (only `cryptography` and the standard library),
never logs, and every error carries only a fixed code, never a value taken from the request.

Order of checks in `open_credentials`: size, strict JSON, version, key id, field formats, operation, timestamp window, a READ-ONLY
nonce check, then the RSA/AES work, then the ATOMIC nonce consume, then plaintext validation. A forged or tampered request
therefore cannot burn someone else's nonce, and cheap checks run before the RSA operation.
"""
import asyncio
import base64
import json
import re
import struct
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = 1

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
_NONCE_RE = re.compile(r"\A[A-Za-z0-9_-]{43}\Z")
_B64U_RE = re.compile(r"\A[A-Za-z0-9_-]*\Z")
_ENVELOPE_FIELDS = frozenset({"v", "kid", "op", "nonce", "ts", "ek", "iv", "ct"})

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
    """Carries only a fixed error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code

    def __str__(self):
        return self.code


class KeyConfigError(Exception):
    """The configured private keys are missing or unusable. Never carries key material or file content."""


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


def validate_fields(operation, fields):
    if operation not in OPERATIONS or type(fields) is not dict or set(fields) != set(OPERATIONS[operation]):
        raise EnvelopeError(MALFORMED)
    for name, value in fields.items():
        _check_text(value, MAX_EMAIL_CHARS if name == "email" else MAX_PASSWORD_CHARS)
    if "@" not in fields["email"]:
        raise EnvelopeError(MALFORMED)


class KeyRing:
    """Private keys by key id. Several ids can be loaded at once (rotation overlap); remove an id to retire it."""

    def __init__(self):
        self._keys = {}

    def add(self, kid, private_key):
        if not _KID_RE.match(kid) or not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < RSA_MIN_BITS:
            raise KeyConfigError("invalid key or key id")
        self._keys[kid] = private_key

    def get(self, kid):
        return self._keys.get(kid)

    def kids(self):
        return sorted(self._keys)

    def __len__(self):
        return len(self._keys)


def load_keyring(text):
    """Builds a KeyRing from JSON: {"<kid>": "<PEM private key>", ...}. Raises KeyConfigError without echoing any content."""
    ring = KeyRing()
    try:
        data = json.loads(text) if text and text.strip() else {}
        if type(data) is not dict or not data:
            raise KeyConfigError("no keys configured")
        for kid, pem in data.items():
            if type(kid) is not str or type(pem) is not str:
                raise KeyConfigError("invalid key entry")
            ring.add(kid, serialization.load_pem_private_key(pem.encode("ascii"), password=None))
    except KeyConfigError:
        raise
    except Exception:
        raise KeyConfigError("keys could not be loaded") from None
    return ring


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


def _decrypt(private_key, ek, iv, ct, aad):
    try:
        key = private_key.decrypt(ek, _oaep())
        if len(key) != AES_KEY_BYTES:
            raise ValueError("bad key length")
        return AESGCM(key).decrypt(iv, ct, aad)
    except (ValueError, InvalidTag):
        raise EnvelopeError(DECRYPT_FAILED) from None  # one code for every RSA or AES failure: no oracle


async def open_credentials(raw, keyring, nonces, expected_operation, now=None):
    """Returns Credentials or raises EnvelopeError. `nonces` offers: now() -> int, async inspect(nonce, op), async consume(nonce, op)."""
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
    await nonces.inspect(nonce, operation)  # unknown / expired / already used / issued for another operation

    # the RSA operation is CPU work: keep it off the event loop
    plaintext = await asyncio.to_thread(_decrypt, private_key, ek, iv, ct, _aad(kid, operation, nonce, ts))

    await nonces.consume(nonce, operation)  # atomic: of several identical requests exactly one gets past this line

    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise EnvelopeError(TOO_LARGE)
    fields = _strict_json(plaintext)
    validate_fields(operation, fields)
    return Credentials(operation=operation, kid=kid, fields=fields)
