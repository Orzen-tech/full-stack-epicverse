import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';

import 'encoding.dart';
import 'errors.dart';
import 'validation.dart';

final Uint8List _oaepLabel = Uint8List.fromList(ascii.encode('epicverse-enc-auth-v1'));

Random? _random;

/// Fresh bytes from the platform CSPRNG (dart:math Random.secure). If the platform has none, sealing FAILS; it never falls back
/// to a weaker generator.
Uint8List _randomBytes(int length) {
  try {
    final random = _random ??= Random.secure();
    return Uint8List.fromList(List<int>.generate(length, (_) => random.nextInt(256)));
  } on UnsupportedError {
    throw const EnvelopeError(noSecureRandom);
  }
}

/// Client side. Validates like the Python sealer, then returns the envelope as one compact JSON string.
/// [nonce] must be a fresh nonce the gateway issued for [operation]. Throws [EnvelopeError] (a code only, never a value) and
/// has NO other path: there is no function in this package that sends or stores credentials in any other form.
String sealCredentials({
  required RSAPublicKey publicKey,
  required String kid,
  required String operation,
  required String nonce,
  required Map<String, String> fields,
  required int nowSeconds,
}) {
  validateFields(operation, fields);
  final plaintext = buildPlaintext(fields);
  if (plaintext.length > maxPlaintextBytes) throw const EnvelopeError(tooLarge);
  return sealBytes(publicKey, kid, operation, nonce, nowSeconds, plaintext);
}

/// Lower level: encrypts already-built plaintext bytes (used by tests to build envelopes the validating front door refuses).
String sealBytes(RSAPublicKey publicKey, String kid, String operation, String nonce, int ts, Uint8List plaintext) {
  final modulus = publicKey.modulus;
  if (modulus == null || modulus.bitLength < rsaMinBits) throw const EnvelopeError(malformed);
  if (!kidPattern.hasMatch(kid) || !operations.containsKey(operation) || !noncePattern.hasMatch(nonce)) {
    throw const EnvelopeError(malformed);
  }
  if (ts < 0 || ts >= maxTimestamp) throw const EnvelopeError(malformed);

  final key = _randomBytes(aesKeyBytes);
  final iv = _randomBytes(ivBytes);
  try {
    final gcm = GCMBlockCipher(AESEngine())
      ..init(true, AEADParameters(KeyParameter(key), tagBits, iv, buildAad(kid, operation, nonce, ts)));
    final ciphertext = gcm.process(plaintext); // ciphertext followed by the 16-byte tag
    final oaep = OAEPEncoding.withSHA256(RSAEngine(), _oaepLabel)..init(true, PublicKeyParameter<RSAPublicKey>(publicKey));
    final wrapped = oaep.process(key);
    if (wrapped.length != (modulus.bitLength + 7) ~/ 8) throw const EnvelopeError(encryptFailed);
    return '{"v":$envelopeVersion,"kid":"$kid","op":"$operation","nonce":"$nonce","ts":$ts,'
        '"ek":"${b64uEncode(wrapped)}","iv":"${b64uEncode(iv)}","ct":"${b64uEncode(ciphertext)}"}';
  } on EnvelopeError {
    rethrow;
  } on Object {
    throw const EnvelopeError(encryptFailed); // never echo library messages
  } finally {
    key.fillRange(0, key.length, 0); // best effort; Dart strings holding the password cannot be wiped
  }
}
