import 'dart:convert';
import 'dart:typed_data';

import 'package:epicverse_enc_envelope/epicverse_enc_envelope.dart';

import 'harness.dart';
import 'vectors.dart';

Uint8List _der(String b64) => base64.decode(b64);

void run() {
  check('RSA-3072 SPKI parses: 3072 bits, e = 65537', () {
    final key = parseRsaPublicKeySpki(_der(rsa3072Spki));
    return key.modulus!.bitLength == 3072 && key.exponent == BigInt.from(65537);
  });
  check('key id equals the Python key id (3072)', () => keyIdForSpki(_der(rsa3072Spki)) == rsa3072Kid);
  check('key id equals the Python key id (4096)', () => keyIdForSpki(_der(rsa4096Spki)) == rsa4096Kid);
  check('RSA-4096 SPKI parses', () => parseRsaPublicKeySpki(_der(rsa4096Spki)).modulus!.bitLength == 4096);

  expectCode('RSA-2048 key is refused', malformedCode, () => parseRsaPublicKeySpki(_der(rsa2048Spki)));
  expectCode('EC P-256 key is refused', malformedCode, () => parseRsaPublicKeySpki(_der(p256Spki)));
  expectCode('empty input is refused', malformedCode, () => parseRsaPublicKeySpki(Uint8List(0)));
  expectCode('garbage is refused', malformedCode, () => parseRsaPublicKeySpki(Uint8List.fromList(List<int>.filled(64, 7))));

  final good = _der(rsa3072Spki);
  expectCode('trailing byte is refused', malformedCode, () => parseRsaPublicKeySpki(Uint8List.fromList([...good, 0])));
  for (final cut in [1, 5, 40, good.length - 1]) {
    expectCode('truncated at ${good.length - cut} bytes is refused', malformedCode,
        () => parseRsaPublicKeySpki(Uint8List.sublistView(good, 0, good.length - cut)));
  }
  check('flipping every 7th byte never yields a usable key of another size', () {
    for (var i = 0; i < good.length; i += 7) {
      final bad = Uint8List.fromList(good)..[i] ^= 0x55;
      try {
        final key = parseRsaPublicKeySpki(bad);
        if (key.modulus!.bitLength < 3072) return false;
      } on EnvelopeError catch (e) {
        if (e.code != malformedCode) return false;
      }
    }
    return true;
  });
}

const String malformedCode = 'MALFORMED';
