// TEST TOOL used by the Python interop tests. Reads JSON lines on stdin:
//   {"spki": "<base64 DER public key>", "kid": ..., "op": ..., "nonce": ..., "ts": ..., "fields": {...}}
// and prints one JSON line per request: {"envelope": "..."} or {"error": "<CODE>"}. Only PUBLIC keys are ever given to it, and
// it never prints input values. No network, no files.
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:epicverse_enc_envelope/epicverse_enc_envelope.dart';
import 'package:pointycastle/export.dart';

void main() {
  final keys = <String, RSAPublicKey>{};
  String? line;
  while ((line = stdin.readLineSync(encoding: utf8)) != null) {
    if (line!.trim().isEmpty) continue;
    String out;
    try {
      final req = jsonDecode(line) as Map<String, dynamic>;
      final spki = req['spki'] as String;
      final key = keys[spki] ??= parseRsaPublicKeySpki(Uint8List.fromList(base64.decode(spki)));
      final envelope = sealCredentials(
        publicKey: key,
        kid: req['kid'] as String,
        operation: req['op'] as String,
        nonce: req['nonce'] as String,
        fields: Map<String, String>.from(req['fields'] as Map),
        nowSeconds: req['ts'] as int,
      );
      out = jsonEncode({'envelope': envelope});
    } on EnvelopeError catch (e) {
      out = jsonEncode({'error': e.code});
    } on Object {
      out = jsonEncode({'error': 'INTERNAL'});
    }
    stdout.writeln(out);
  }
}
