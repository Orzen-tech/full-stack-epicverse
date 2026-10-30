"""Server copy of the app's password policy: a faithful port of
frontend/EpicVerseApp/lib/core/utils/password_validator.dart (PasswordValidator.validate), nothing added and nothing relaxed.

Behaviour copied from the Dart source:
  * empty                      -> "Password is required"
  * length < 8                 -> counted in UTF-16 code units, like Dart's String.length (an emoji counts as 2)
  * at least one A-Z, a-z, 0-9 -> ASCII classes only
  * at least one special       -> one of  ! @ # $ % ^ & * ( ) _ + - = [ ] { } ; : ' " \\ | , . < > / ? ~ `
  * not one of the common passwords after toLowerCase()
Pure module: no application imports.
"""
import re

MIN_LENGTH = 8

_UPPERCASE = re.compile(r"[A-Z]")
_LOWERCASE = re.compile(r"[a-z]")
_NUMBER = re.compile(r"[0-9]")
_SPECIAL = re.compile(r"[!@#$%^&*()_+\-=\[\]{};:'\"\\|,.<>/?~`]+")

COMMON_PASSWORDS = (
    "123456", "12345678", "123456789", "password", "12345", "1234567",
    "qwerty", "1234567890", "iloveyou", "111111", "password123", "admin",
    "welcome", "letmein",
)


def utf16_length(value):
    """Dart's String.length: the number of UTF-16 code units."""
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def validate(value):
    """Returns the error message of the first unmet rule, or None if the password satisfies the policy."""
    if value is None or value == "":
        return "Password is required"
    if utf16_length(value) < MIN_LENGTH:
        return "Password must be at least 8 characters"
    if not _UPPERCASE.search(value):
        return "Must contain at least one uppercase letter"
    if not _LOWERCASE.search(value):
        return "Must contain at least one lowercase letter"
    if not _NUMBER.search(value):
        return "Must contain at least one number"
    if not _SPECIAL.search(value):
        return "Must contain at least one special character"
    if value.lower() in COMMON_PASSWORDS:
        return "This password is too common and easily guessable"
    return None


def meets_policy(value):
    return validate(value) is None
