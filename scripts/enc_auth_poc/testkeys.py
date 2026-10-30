"""TEST-ONLY key helpers. Keys are generated in memory for each test run and are never serialised, written or printed."""
import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from envelope import RSA_MIN_BITS


def generate_test_private_key(bits=RSA_MIN_BITS):
    return rsa.generate_private_key(public_exponent=65537, key_size=bits)


def kid_for(public_key):
    """Key identifier: 'k' + the first 12 base64url characters of SHA-256(SubjectPublicKeyInfo DER). Public data only."""
    der = public_key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "k" + base64.urlsafe_b64encode(hashlib.sha256(der).digest()).decode("ascii")[:12]
