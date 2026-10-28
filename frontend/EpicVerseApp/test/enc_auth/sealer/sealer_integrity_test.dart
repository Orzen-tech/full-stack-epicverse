// The vendored sealer in lib/ must be byte-identical to the tested G4 proof-of-concept sealer: same algorithm, same envelope format, same
// validation. The pins are SHA-256 of the five files (line endings normalised). Any edit to a vendored file fails here and needs a new review.
import 'dart:convert';
import 'dart:io';

import 'package:crypto/crypto.dart';
import 'package:flutter_test/flutter_test.dart';

const Map<String, String> _pins = {
  'encoding.dart': 'd15551a408b1cf28d289121ed1341b530b07504b3e88de281525d8a959bb4534',
  'envelope.dart': '6b767aaa053af1aff57fbbfbb769ec890616ad9354e7c23bc888279904ec90f1',
  'errors.dart': '9e3b651a3943609d7e185b360b94c365bd2139a9926c0c4ca5a00fd882c1ea98',
  'spki_key.dart': '599658e583167061b83e765e4dc830c0783c608bd91b217816ab0eb7b784b4f3',
  'validation.dart': '45ad3b9ee82fe0c8f5d30166f7640959bebf0a1bc27d0fa94f5728932b37181c',
};

const String _vendored = 'lib/core/security/enc_auth/sealer';
const String _poc = '../../scripts/enc_auth_poc/g4_dart/lib/src';

String _sha(File f) => sha256.convert(utf8.encode(f.readAsStringSync().replaceAll('\r\n', '\n'))).toString();

void main() {
  test('each vendored sealer file matches its pinned SHA-256', () {
    for (final entry in _pins.entries) {
      expect(_sha(File('$_vendored/${entry.key}')), entry.value, reason: entry.key);
    }
  });

  test('the sealer folder holds exactly the five pinned files and the barrel, nothing else', () {
    final names = Directory(_vendored).listSync().whereType<File>().map((f) => f.uri.pathSegments.last).toSet();
    expect(names, {..._pins.keys, 'sealer.dart'});
    expect(Directory(_vendored).listSync().whereType<Directory>(), isEmpty);
  });

  test('the barrel exports the same public API as the proof-of-concept package (no more, no less)', () {
    final barrel = File('$_vendored/sealer.dart').readAsStringSync();
    final exports = RegExp(r"export '([a-z_]+\.dart)' show ([^;]+);").allMatches(barrel).map((m) => '${m[1]}:${m[2]}').toSet();
    expect(exports, {
      'encoding.dart:b64uEncode, buildAad, buildPlaintext',
      'envelope.dart:sealCredentials',
      'errors.dart:EnvelopeError',
      'spki_key.dart:keyIdForSpki, parseRsaPublicKeySpki',
      'validation.dart:operations',
    });
    final pocBarrel = File('../../scripts/enc_auth_poc/g4_dart/lib/epicverse_enc_envelope.dart');
    if (pocBarrel.existsSync()) {
      final pocExports = RegExp(r"export 'src/([a-z_]+\.dart)' show ([^;]+);").allMatches(pocBarrel.readAsStringSync()).map((m) => '${m[1]}:${m[2]}').toSet();
      expect(exports, pocExports);
    }
  });

  test('where the proof-of-concept folder is present, the vendored files equal its originals', () {
    if (!Directory(_poc).existsSync()) {
      markTestSkipped('proof-of-concept folder not present in this checkout; the pins above still apply');
      return;
    }
    for (final name in _pins.keys) {
      expect(_sha(File('$_poc/$name')), _pins[name], reason: 'PoC original $name');
    }
  });

  test('the vendored files import only dart core libraries, pointycastle and each other', () {
    final allowed = RegExp(r"^import '(dart:(convert|math|typed_data)|package:pointycastle/(export|asn1)\.dart|[a-z_]+\.dart)';$");
    for (final name in _pins.keys) {
      for (final line in File('$_vendored/$name').readAsLinesSync().where((l) => l.startsWith('import '))) {
        expect(allowed.hasMatch(line), isTrue, reason: '$name: $line');
      }
    }
  });
}
