// Source guards for the encrypted-auth integration: the plaintext Firebase password APIs live in ONE file, nothing in lib/ imports the
// local sealer package (so a release build cannot link it), the pinned apiClient is the only transport, and nothing logs credentials.
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

String _norm(String path) => path.replaceAll(r'\', '/');

List<File> _lib() => Directory('lib')
    .listSync(recursive: true)
    .whereType<File>()
    .where((f) => f.path.endsWith('.dart'))
    .toList();

String _code(File f) => f.readAsLinesSync().where((l) => !l.trim().startsWith('//')).join('\n');

const _legacy = 'lib/core/security/enc_auth/legacy_password_auth.dart';
const _callSites = [
  'lib/presentation/screens/login_screen.dart',
  'lib/presentation/screens/create_profile_screen.dart',
  'lib/presentation/screens/change_password_screen.dart',
  'lib/core/security/password_reauth.dart',
];

void main() {
  final passwordApis = RegExp(
    r'FirebaseAuth\.instance\s*\.\s*(signInWithEmailAndPassword|createUserWithEmailAndPassword|sendPasswordResetEmail|fetchSignInMethodsForEmail)'
    r'|\.reauthenticateWithCredential\s*\(|\.updatePassword\s*\(|EmailAuthProvider|\.linkWithCredential\s*\(',
  );

  test('plaintext Firebase password APIs are called from exactly one file: the legacy backend', () {
    final offenders = [for (final f in _lib()) if (passwordApis.hasMatch(_code(f))) _norm(f.path)];
    expect(offenders, [_legacy]);
  });

  test('the four call sites use PasswordAuth and no longer touch a Firebase password API', () {
    for (final path in _callSites) {
      final src = _code(File(path));
      expect(passwordApis.hasMatch(src), isFalse, reason: '$path still calls a Firebase password API');
      expect(src, contains('PasswordAuth.'), reason: '$path does not use the facade');
    }
  });

  test('the legacy backend is reachable only through the build-time switch in password_auth.dart', () {
    final users = [for (final f in _lib()) if (_code(f).contains('LegacyPasswordAuth') && _norm(f.path) != _legacy) _norm(f.path)];
    expect(users, ['lib/core/security/enc_auth/password_auth.dart']);
    final facade = _code(File('lib/core/security/enc_auth/password_auth.dart'));
    expect(RegExp(r'EncAuthConfig\.isRequired\s*\?\s*EncryptedPasswordAuth\.production\(\)\s*:\s*const LegacyPasswordAuth\(\)').hasMatch(facade), isTrue);
    expect('const LegacyPasswordAuth()'.allMatches(facade).length, 1);
  });

  test('the default mode is off and the mode is a compile-time constant', () {
    final config = _code(File('lib/core/security/enc_auth/enc_auth_config.dart'));
    expect(config, contains("String.fromEnvironment('ENC_AUTH_MODE', defaultValue: 'off')"));
    expect(config, contains('static const bool isOff = _mode == \'off\';'));
    expect(config, contains('static const bool isRequired = !isOff;'));
  });

  test('lib/ never imports the old development package or the proof-of-concept folder, and only the vendored sealer folder uses pointycastle', () {
    const sealerDir = 'lib/core/security/enc_auth/sealer/';
    for (final f in _lib()) {
      final src = _code(f); // comments may mention the package; code and imports may not
      expect(src.contains('epicverse_enc_envelope'), isFalse, reason: _norm(f.path));
      expect(src.contains('enc_auth_poc'), isFalse, reason: _norm(f.path));
      if (!_norm(f.path).startsWith(sealerDir)) {
        expect(src.contains('pointycastle'), isFalse, reason: '${_norm(f.path)} must reach the crypto only through the sealer folder');
      }
    }
  });

  test('the vendored sealer folder has no network, file, platform, Firebase or logging code', () {
    final files = Directory('lib/core/security/enc_auth/sealer').listSync().whereType<File>().where((f) => f.path.endsWith('.dart'));
    expect(files, isNotEmpty);
    for (final f in files) {
      final src = _code(f);
      final imports = RegExp(r"^import '([^']+)';", multiLine: true).allMatches(src).map((m) => m.group(1)!).toList();
      for (final i in imports) {
        expect(i == 'dart:convert' || i == 'dart:math' || i == 'dart:typed_data' || i.startsWith('package:pointycastle/') || RegExp(r'^[a-z_]+\.dart$').hasMatch(i), isTrue,
            reason: '${_norm(f.path)} imports $i');
      }
      expect(RegExp(r'\bprint\(|debugPrint|dart:io|dart:html|HttpClient|Dio\(|firebase|flutter/').hasMatch(src), isFalse, reason: _norm(f.path));
    }
  });

  test('pointycastle is a direct dependency pinned to 4.0.0 and the old development path package is gone', () {
    final pubspec = File('pubspec.yaml').readAsStringSync();
    final dev = pubspec.indexOf('\ndev_dependencies:');
    final direct = pubspec.indexOf('\n  pointycastle: 4.0.0');
    expect(dev, greaterThan(0));
    expect(direct, greaterThan(0));
    expect(direct, lessThan(dev), reason: 'pointycastle must be under dependencies, not dev_dependencies');
    expect('pointycastle:'.allMatches(pubspec).length, 1);
    expect(pubspec.contains('epicverse_enc_envelope'), isFalse);
    expect(pubspec.contains('enc_auth_poc'), isFalse);
    expect(RegExp(r'^\s+path:\s', multiLine: true).hasMatch(pubspec), isFalse, reason: 'no path dependency may remain');
    final lock = File('pubspec.lock').readAsStringSync();
    expect(lock.contains('epicverse_enc_envelope'), isFalse);
    expect(RegExp(r'pointycastle:\s+dependency: "direct main"[\s\S]*?version: "4\.0\.0"').hasMatch(lock), isTrue);
  });

  test('a sealer can be registered only by the runtime itself; it is refused in release builds; app code, tools and entry points never register one', () {
    for (final f in _lib()) {
      final src = _code(f);
      if (_norm(f.path) == 'lib/core/security/enc_auth/enc_envelope_sealer.dart') continue;
      expect(src.contains('registerSealer('), isFalse, reason: _norm(f.path));
      expect(src.contains('PasswordAuth.useForTest('), isFalse, reason: _norm(f.path));
    }
    final runtime = _code(File('lib/core/security/enc_auth/enc_envelope_sealer.dart'));
    expect(RegExp(r'static void registerSealer\([^)]*\) \{\s*if \(kReleaseMode\) \{\s*throw StateError').hasMatch(runtime), isTrue);
    expect(runtime, contains('if (!EncAuthConfig.isRequired) return null;'));
    expect(runtime, contains('buildSealer(EncAuthConfig.keyId, EncAuthConfig.publicKeySpkiBase64)'));
    for (final f in Directory('tool').listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.dart'))) {
      expect(_code(f).contains('registerSealer('), isFalse, reason: _norm(f.path));
    }
    expect(File('tool/enc_auth_local/g4_sealer.dart').existsSync(), isFalse, reason: 'a second sealer adapter must not exist');
  });

  test('the gateway client uses the pinned apiClient and opens no other connection', () {
    final dir = Directory('lib/core/security/enc_auth');
    for (final f in dir.listSync().whereType<File>().where((f) => f.path.endsWith('.dart'))) {
      final src = _code(f);
      expect(RegExp(r'\bDio\(|HttpClient\(|http\.Client|WebSocket|badCertificateCallback').hasMatch(src), isFalse, reason: _norm(f.path));
    }
    expect(_code(File('lib/core/security/enc_auth/enc_auth_client.dart')), contains('ApiClient'));
    expect(_code(File('lib/core/security/enc_auth/encrypted_password_auth.dart')), contains('ApiClientEncAuthTransport(apiClient)'));
  });

  test('the encrypted path logs nothing and there is no plaintext fallback or retry in it', () {
    final dir = Directory('lib/core/security/enc_auth');
    for (final f in dir.listSync().whereType<File>().where((f) => f.path.endsWith('.dart'))) {
      final path = _norm(f.path);
      final src = _code(f);
      if (path != _legacy) {
        expect(RegExp(r'\bdebugPrint\(|\bprint\(|\blog\(|Sentry|Logger').hasMatch(src), isFalse, reason: path);
      }
      if (path != _legacy && !path.endsWith('password_auth.dart')) {
        expect(src.contains('LegacyPasswordAuth'), isFalse, reason: path);
        expect(RegExp(r'\bretry\w*\(|for \(var attempt|while \(true\)', caseSensitive: false).hasMatch(src), isFalse, reason: path);
      }
    }
  });

  test('the legacy file logs only a Firebase error CODE (as the screen did before), never a value', () {
    final legacy = _code(File(_legacy));
    final prints = RegExp(r"debugPrint\([^;]*;").allMatches(legacy).map((m) => m.group(0)!).toList();
    expect(prints, hasLength(1));
    expect(prints.single, contains(r'${e.code}'));
    expect(prints.single.contains('password'), isFalse);
    expect(prints.single.contains('email'), isFalse);
  });

  test('release configuration is untouched: the Android pin config and the pinned client still pin the production roots', () {
    final pinning = File('lib/core/network/ssl_pinning_service.dart').readAsStringSync();
    expect(pinning, contains('SecurityContext(withTrustedRoots: false)'));
    expect(File('android/app/src/main/res/xml/network_security_config.xml').readAsStringSync(), contains('<pin-set'));
  });
}
