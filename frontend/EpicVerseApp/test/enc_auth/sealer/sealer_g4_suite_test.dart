// The G4 Dart sealer's own known-answer / envelope / key suite, run against the VENDORED copy in lib/. Every expected value comes from the Python
// reference implementation (vectors.dart is generated from it). `check` calls below register ordinary flutter tests.
import 'envelope_cases.dart' as envelope;
import 'kat_cases.dart' as kat;
import 'key_cases.dart' as key;

void main() {
  kat.run();
  key.run();
  envelope.run();
}
