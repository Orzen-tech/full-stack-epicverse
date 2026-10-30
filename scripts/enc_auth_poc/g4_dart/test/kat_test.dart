// Known-answer tests: every expected value comes from the Python reference (vectors.dart is generated from it).
import 'dart:convert';
import 'dart:typed_data';

import 'package:epicverse_enc_envelope/epicverse_enc_envelope.dart';
import 'package:pointycastle/export.dart';

import 'harness.dart';
import 'vectors.dart';

void run() {
  for (var i = 0; i < aadVectors.length; i++) {
    final v = aadVectors[i];
    check('AAD vector $i', () => toHex(buildAad(v[0] as String, v[1] as String, v[2] as String, v[3] as int)) == v[4]);
  }

  for (var i = 0; i < gcmVectors.length; i++) {
    final v = gcmVectors[i];
    final key = fromHex(v[0]), iv = fromHex(v[1]), aad = fromHex(v[2]), pt = fromHex(v[3]);
    check('AES-256-GCM encrypt equals Python ciphertext||tag, vector $i', () {
      final gcm = GCMBlockCipher(AESEngine())..init(true, AEADParameters(KeyParameter(key), 128, iv, aad));
      return toHex(gcm.process(pt)) == v[4];
    });
    check('AES-256-GCM decrypts the Python ciphertext, vector $i', () {
      final gcm = GCMBlockCipher(AESEngine())..init(false, AEADParameters(KeyParameter(key), 128, iv, aad));
      return sameBytes(gcm.process(fromHex(v[4])), pt);
    });
    check('AES-256-GCM rejects a flipped bit, vector $i', () {
      final bad = fromHex(v[4]);
      bad[bad.length - 1] ^= 1;
      final gcm = GCMBlockCipher(AESEngine())..init(false, AEADParameters(KeyParameter(key), 128, iv, aad));
      try {
        gcm.process(bad);
      } on InvalidCipherTextException {
        return true;
      }
      return false;
    });
  }

  for (var i = 0; i < b64uVectors.length; i++) {
    final v = b64uVectors[i];
    check('base64url vector $i', () => b64uEncode(fromHex(v[0])) == v[1] && !v[1].contains('='));
  }

  for (var i = 0; i < plaintextVectors.length; i++) {
    final v = plaintextVectors[i];
    final fields = Map<String, String>.from(v[0] as Map);
    check('plaintext JSON is byte-identical to Python, vector $i', () => utf8.decode(buildPlaintext(fields)) == v[1]);
    check('plaintext JSON is pure ASCII, vector $i', () => buildPlaintext(fields).every((b) => b < 0x80));
    check('plaintext JSON round-trips through jsonDecode, vector $i', () {
      final decoded = jsonDecode(utf8.decode(buildPlaintext(fields))) as Map<String, dynamic>;
      return decoded.length == fields.length && fields.entries.every((e) => decoded[e.key] == e.value);
    });
  }

  check('SHA-256 of the empty input (library sanity)',
      () => toHex(SHA256Digest().process(Uint8List(0))) == 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
}
