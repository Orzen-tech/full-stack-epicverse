// The built-in sealer and its creation by EncAuthRuntime. Mode-independent tests run always; the build-mode groups run only in the mode they
// describe. Run this file three ways:
//   flutter test test/enc_auth/enc_auth_builtin_sealer_test.dart                                         (off, the default)
//   flutter test test/enc_auth/enc_auth_builtin_sealer_test.dart --dart-define=ENC_AUTH_MODE=required     (required, no key)
//   flutter test test/enc_auth/enc_auth_builtin_sealer_test.dart --dart-define=ENC_AUTH_MODE=required --dart-define=ENC_AUTH_KID=<kid> \
//       --dart-define=ENC_AUTH_PUBLIC_KEY_B64=<spki>                                                     (required, valid public TEST key)
import 'dart:convert';

import 'package:epicverse/core/security/enc_auth/enc_auth_config.dart';
import 'package:epicverse/core/security/enc_auth/enc_envelope_sealer.dart';
import 'package:epicverse/core/security/enc_auth/g4_envelope_sealer.dart';
import 'package:epicverse/core/security/enc_auth/password_auth.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_test/flutter_test.dart';

import 'enc_auth_test_support.dart';
import 'sealer/vectors.dart' show p256Spki, rsa2048Kid, rsa2048Spki, rsa4096Kid, rsa4096Spki;

const String _nonce = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA';

Future<Object?> outcome(Future<Object?> Function() run) async {
  try {
    await run();
    return null;
  } catch (e) {
    return e;
  }
}

bool get _hasValidKey => EncAuthRuntime.buildSealer(EncAuthConfig.keyId, EncAuthConfig.publicKeySpkiBase64) != null;

void main() {
  tearDown(() {
    PasswordAuth.useForTest(null);
    EncAuthRuntime.clearForTest();
  });

  group('creation of a sealer from a key id and a public key (the single code path a required build uses)', () {
    test('a valid public key and its own key id give a working sealer', () {
      final sealer = EncAuthRuntime.buildSealer(testKeyId, testSpki);
      expect(sealer, isA<G4EnvelopeSealer>());
      final envelope = sealer!.seal(
        operation: 'login',
        nonce: _nonce,
        nowSeconds: 1800000000,
        fields: {'email': 'user@example.invalid', 'password': 'Passw0rd!x'},
      );
      final json = jsonDecode(envelope) as Map<String, dynamic>;
      expect(json['v'], 1);
      expect(json['kid'], testKeyId);
      expect(json['op'], 'login');
      expect(json['nonce'], _nonce);
      expect(json['ts'], 1800000000);
      expect(json.keys.toSet(), {'v', 'kid', 'op', 'nonce', 'ts', 'ek', 'iv', 'ct'});
      expect(leaks([envelope], 'Passw0rd!x'), isFalse);
      expect(leaks([envelope], 'user@example.invalid'), isFalse);
    });

    test('every missing, malformed or mismatched configuration gives NO sealer', () {
      final cases = <String, List<String>>{
        'empty key and id': ['', ''],
        'empty key': [testKeyId, ''],
        'empty id': ['', testSpki],
        'not base64': [testKeyId, '!!!not base64!!!'],
        'base64 of junk': [testKeyId, base64.encode(List<int>.generate(64, (i) => i))],
        'truncated key': [testKeyId, testSpki.substring(0, 200)],
        'an EC key, not RSA': ['kx', p256Spki],
        'an RSA-2048 key (below the 3072-bit minimum)': [rsa2048Kid, rsa2048Spki],
        "a valid key with another key's id": [rsa4096Kid, testSpki],
        'a valid key with an altered id': ['${testKeyId}x', testSpki],
        'a valid key with whitespace around it': [testKeyId, ' $testSpki '],
      };
      for (final entry in cases.entries) {
        expect(EncAuthRuntime.buildSealer(entry.value[0], entry.value[1]), isNull, reason: entry.key);
      }
      expect(EncAuthRuntime.buildSealer(rsa4096Kid, rsa4096Spki), isNotNull, reason: 'another valid 4096-bit key works with its own id');
    });

    test('the adapter itself throws only a fixed code, never the key material', () {
      for (final spki in ['!!!', '', testSpki.substring(0, 100)]) {
        try {
          G4EnvelopeSealer(keyId: testKeyId, spkiBase64: spki);
          fail('should have thrown');
        } on EnvelopeSealFailure catch (e) {
          expect(e.code, matches(RegExp(r'^[A-Z_]+$')));
          expect(e.toString().contains(spki.isEmpty ? '#' : spki), isFalse);
        }
      }
      expect(
        () => G4EnvelopeSealer(keyId: rsa4096Kid, spkiBase64: testSpki),
        throwsA(isA<EnvelopeSealFailure>().having((e) => e.code, 'code', 'KEY_ID_MISMATCH')),
      );
    });

    test('sealing refuses what the protocol refuses (unknown operation, bad nonce)', () {
      final sealer = EncAuthRuntime.buildSealer(testKeyId, testSpki)!;
      final fields = {'email': 'a@example.invalid', 'password': 'Passw0rd!x'};
      expect(() => sealer.seal(operation: 'delete_everything', nonce: _nonce, nowSeconds: 1800000000, fields: fields), throwsA(isA<EnvelopeSealFailure>()));
      expect(() => sealer.seal(operation: 'login', nonce: 'short', nowSeconds: 1800000000, fields: fields), throwsA(isA<EnvelopeSealFailure>()));
    });
  });

  group('off mode (the default - a normal build)', () {
    final skip = EncAuthConfig.isOff ? null : 'only in an off-mode run';

    test('no sealer exists unless a test registers one', () {
      expect(EncAuthRuntime.sealer, isNull);
    }, skip: skip);

    test('a registered test sealer is used as given (the test seam is unchanged)', () {
      final fake = testSealer();
      EncAuthRuntime.registerSealer(fake);
      expect(EncAuthRuntime.sealer, same(fake));
    }, skip: skip);
  });

  group('required mode without a valid key (ENC_AUTH_MODE=required, ENC_AUTH_KID / ENC_AUTH_PUBLIC_KEY_B64 absent or wrong)', () {
    final skip = EncAuthConfig.isRequired && !_hasValidKey ? null : 'only in a required-mode run with no valid key';

    test('there is no built-in sealer, and every credential operation fails closed with a fixed error before any request', () async {
      expect(EncAuthRuntime.sealer, isNull);
      final ops = <Future<Object?> Function()>[
        () => PasswordAuth.signInWithEmailAndPassword(email: 'a@example.invalid', password: 'Passw0rd!x'),
        () => PasswordAuth.signUpOrRecover(email: 'a@example.invalid', password: 'Passw0rd!x', emailProof: 'p'),
        () => PasswordAuth.reauthenticate('Passw0rd!x'),
        () => PasswordAuth.changePassword(current: 'Passw0rd!x', next: 'Passw0rd!y'),
      ];
      for (final run in ops) {
        final error = await outcome(run);
        expect(error, isA<FirebaseAuthException>());
        expect((error as FirebaseAuthException).code, 'enc-auth-unavailable');
      }
    }, skip: skip);
  });

  group('required mode with a valid public test key', () {
    final skip = EncAuthConfig.isRequired && _hasValidKey ? null : 'only in a required-mode run with a valid key';

    test('the runtime creates the built-in sealer by itself from the compile-time defines and it seals', () {
      final sealer = EncAuthRuntime.sealer;
      expect(sealer, isA<G4EnvelopeSealer>());
      final envelope = sealer!.seal(operation: 'reauth', nonce: _nonce, nowSeconds: 1800000000, fields: {'email': 'a@example.invalid', 'password': 'Passw0rd!x'});
      expect((jsonDecode(envelope) as Map)['kid'], EncAuthConfig.keyId);
      expect(leaks([envelope], 'Passw0rd!x'), isFalse);
    }, skip: skip);

    test('the sealer is created once and reused', () {
      expect(identical(EncAuthRuntime.sealer, EncAuthRuntime.sealer), isTrue);
    }, skip: skip);
  });
}
