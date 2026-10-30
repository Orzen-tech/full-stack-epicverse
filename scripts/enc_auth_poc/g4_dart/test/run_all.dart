import 'dart:io';

import 'envelope_test.dart' as envelope;
import 'harness.dart';
import 'kat_test.dart' as kat;
import 'key_test.dart' as key;

/// Run:  dart --packages=.dart_tool/package_config.json test/run_all.dart   (from scripts/enc_auth_poc/g4_dart)
void main() {
  kat.run();
  key.run();
  envelope.run();
  exit(finish('G4 Dart suite'));
}
