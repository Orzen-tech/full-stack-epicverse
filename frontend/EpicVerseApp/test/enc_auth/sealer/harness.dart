// Assertion helpers of the G4 suite, adapted to flutter_test: each `check` becomes one test. Failures print the test NAME (plus
// flutter_test's generic "Expected: true" line), never a value.
import 'dart:typed_data';

import 'package:epicverse/core/security/enc_auth/sealer/sealer.dart';
import 'package:flutter_test/flutter_test.dart' as ft;

void check(String name, bool Function() body) {
  ft.test(name, () => ft.expect(body(), ft.isTrue));
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
