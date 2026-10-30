// G4 interoperability gate (TEST TOOL, not library code): RSA-OAEP-SHA256 / MGF1-SHA256 with a label, Dart -> Python.
// Reads one JSON line on stdin: {"n": "<hex modulus>", "e": 65537, "label": "...", "count": 2000, "use_label": true}
// and prints "<cek hex> <ek hex>" per line. Only a PUBLIC key is ever given to this program. Dummy random data only.
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'dart:typed_data';

import 'package:pointycastle/export.dart';

String _hex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

void main() {
  final cfg = jsonDecode(stdin.readLineSync()!) as Map<String, dynamic>;
  final pub = RSAPublicKey(BigInt.parse(cfg['n'] as String, radix: 16), BigInt.from(cfg['e'] as int));
  final label = (cfg['use_label'] as bool) ? Uint8List.fromList(utf8.encode(cfg['label'] as String)) : null;
  final random = Random.secure();
  for (var i = 0; i < (cfg['count'] as int); i++) {
    final cek = Uint8List.fromList(List<int>.generate(32, (_) => random.nextInt(256)));
    final engine = OAEPEncoding.withSHA256(RSAEngine(), label)..init(true, PublicKeyParameter<RSAPublicKey>(pub));
    final ek = engine.process(cek);
    stdout.writeln('${_hex(cek)} ${_hex(ek)}');
  }
}
