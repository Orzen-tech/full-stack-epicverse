import 'dart:convert';
import 'dart:typed_data';

import 'enc_auth_config.dart';
import 'enc_envelope_sealer.dart';
import 'sealer/sealer.dart' as g4;

/// The app's [EnvelopeSealer]: the G4 Dart sealer bound to one gateway public key. Throws [EnvelopeSealFailure] (a fixed code, never a
/// value) for a malformed key or a key id that does not match the key; there is no other output form and no plaintext path.
class G4EnvelopeSealer implements EnvelopeSealer {
  /// [spkiBase64] is the DER SubjectPublicKeyInfo of the gateway's public key. The key id must match the one derived from it.
  G4EnvelopeSealer({required String keyId, required String spkiBase64})
      : _keyId = keyId,
        _publicKey = _parse(keyId, spkiBase64);

  /// From the --dart-define values ENC_AUTH_KID and ENC_AUTH_PUBLIC_KEY_B64.
  factory G4EnvelopeSealer.fromConfig() =>
      G4EnvelopeSealer(keyId: EncAuthConfig.keyId, spkiBase64: EncAuthConfig.publicKeySpkiBase64);

  final String _keyId;
  final dynamic _publicKey; // the sealer's RSA public key object; dynamic so that only the sealer folder touches the crypto library

  static dynamic _parse(String keyId, String spkiBase64) {
    final Uint8List der;
    try {
      der = base64.decode(spkiBase64);
    } on FormatException {
      throw const EnvelopeSealFailure('BAD_KEY');
    }
    try {
      if (g4.keyIdForSpki(der) != keyId) throw const EnvelopeSealFailure('KEY_ID_MISMATCH');
      return g4.parseRsaPublicKeySpki(der);
    } on g4.EnvelopeError catch (e) {
      throw EnvelopeSealFailure(e.code);
    }
  }

  @override
  String seal({
    required String operation,
    required String nonce,
    required int nowSeconds,
    required Map<String, String> fields,
  }) {
    try {
      return g4.sealCredentials(
        publicKey: _publicKey,
        kid: _keyId,
        operation: operation,
        nonce: nonce,
        fields: fields,
        nowSeconds: nowSeconds,
      );
    } on g4.EnvelopeError catch (e) {
      throw EnvelopeSealFailure(e.code);
    }
  }
}
