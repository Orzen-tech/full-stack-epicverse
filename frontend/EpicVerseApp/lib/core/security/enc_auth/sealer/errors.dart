/// Error codes. They never carry a value taken from the caller's input, so an error can be logged or shown safely.
const String malformed = 'MALFORMED';
const String tooLarge = 'TOO_LARGE';
const String encryptFailed = 'ENCRYPT_FAILED';
const String noSecureRandom = 'NO_SECURE_RANDOM';

class EnvelopeError implements Exception {
  const EnvelopeError(this.code);

  final String code;

  @override
  String toString() => 'EnvelopeError($code)';
}
