// Minimal assertion harness (the `test` package is not in the offline cache). Failures print the test NAME only, never values.
import 'dart:typed_data';

import 'package:epicverse_enc_envelope/epicverse_enc_envelope.dart';

int _passed = 0;
int _failed = 0;

void check(String name, bool Function() body) {
  try {
    if (body()) {
      _passed++;
    } else {
      _failed++;
      print('FAIL  $name');
    }
  } on Object catch (e) {
    _failed++;
    print('ERROR $name (${e.runtimeType})');
  }
}

/// Passes only if [fn] throws an [EnvelopeError] with exactly [code].
void expectCode(String name, String code, void Function() fn) {
  check(name, () {
    try {
      fn();
    } on EnvelopeError catch (e) {
      return e.code == code;
    }
    return false;
  });
}

Uint8List fromHex(String hex) {
  final out = Uint8List(hex.length ~/ 2);
  for (var i = 0; i < out.length; i++) {
    out[i] = int.parse(hex.substring(2 * i, 2 * i + 2), radix: 16);
  }
  return out;
}

String toHex(List<int> bytes) => bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();

bool sameBytes(List<int> a, List<int> b) {
  if (a.length != b.length) return false;
  for (var i = 0; i < a.length; i++) {
    if (a[i] != b[i]) return false;
  }
  return true;
}

int finish(String suite) {
  print('$suite: passed $_passed, failed $_failed');
  return _failed == 0 ? 0 : 1;
}
