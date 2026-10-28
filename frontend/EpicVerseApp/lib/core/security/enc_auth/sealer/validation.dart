import 'errors.dart';

/// Mirrors scripts/enc_auth_poc/envelope.py (version 1). Lengths are counted in Unicode code points (runes), like Python.
const int envelopeVersion = 1;
const int maxPlaintextBytes = 2048;
const int maxEmailChars = 254;
const int maxPasswordChars = 256;
const int rsaMinBits = 3072;
const int aesKeyBytes = 32;
const int ivBytes = 12;
const int tagBits = 128;
const int maxTimestamp = 1099511627776; // 2^40, exclusive

/// operation -> the exact set of plaintext fields. `password` is the CURRENT password for change_password.
const Map<String, List<String>> operations = {
  'login': ['email', 'password'],
  'signup': ['email', 'password'],
  'reauth': ['email', 'password'],
  'change_password': ['email', 'password', 'new_password'],
};

final RegExp kidPattern = RegExp(r'^[A-Za-z0-9._-]{1,64}$');
final RegExp noncePattern = RegExp(r'^[A-Za-z0-9_-]{43}$');

void checkText(String value, int maxChars) {
  if (value.isEmpty) throw const EnvelopeError(malformed);
  if (value.runes.length > maxChars) throw const EnvelopeError(tooLarge);
  for (final rune in value.runes) {
    if (rune < 32 || rune == 127) throw const EnvelopeError(malformed);
  }
}

/// Same order of checks as Python's `_validate_fields`: structure, then each field in the order given, then the `@`.
void validateFields(String operation, Map<String, String> fields) {
  final names = operations[operation];
  if (names == null || fields.length != names.length || !names.every(fields.containsKey)) {
    throw const EnvelopeError(malformed);
  }
  for (final entry in fields.entries) {
    checkText(entry.value, entry.key == 'email' ? maxEmailChars : maxPasswordChars);
  }
  if (!fields['email']!.contains('@')) throw const EnvelopeError(malformed);
}
