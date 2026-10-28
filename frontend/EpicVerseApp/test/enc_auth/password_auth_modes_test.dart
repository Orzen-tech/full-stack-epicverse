// Build-mode behaviour of the PasswordAuth facade. Run this file twice:
//   flutter test test/enc_auth/password_auth_modes_test.dart                                   (ENC_AUTH_MODE unset = off)
//   flutter test test/enc_auth/password_auth_modes_test.dart --dart-define=ENC_AUTH_MODE=required
// Each group runs only in the mode it describes.
import 'package:epicverse/core/security/enc_auth/enc_auth_config.dart';
import 'package:epicverse/core/security/enc_auth/enc_envelope_sealer.dart';
import 'package:epicverse/core/security/enc_auth/password_auth.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_test/flutter_test.dart';

Future<Object?> outcome(Future<Object?> Function() run) async {
  try {
    await run();
    return null;
  } catch (e) {
    return e;
  }
}

List<Future<Object?> Function()> operations() => [
      () => PasswordAuth.signInWithEmailAndPassword(email: 'a@example.invalid', password: 'Passw0rd!x'),
      () => PasswordAuth.signUpOrRecover(email: 'a@example.invalid', password: 'Passw0rd!x', emailProof: 'p'),
      () => PasswordAuth.reauthenticate('Passw0rd!x'),
      () => PasswordAuth.changePassword(current: 'Passw0rd!x', next: 'Passw0rd!y'),
    ];

void main() {
  tearDown(() {
    PasswordAuth.useForTest(null);
    EncAuthRuntime.clearForTest();
  });

  test('the mode switch is consistent and defaults to off', () {
    expect(EncAuthConfig.isOff, !EncAuthConfig.isRequired);
    const raw = String.fromEnvironment('ENC_AUTH_MODE', defaultValue: 'off');
    expect(EncAuthConfig.isOff, raw == 'off', reason: 'anything but exactly "off" means required');
  });

  // With a valid ENC_AUTH_KID / ENC_AUTH_PUBLIC_KEY_B64 the built-in sealer exists and the encrypted path would call the real API, so the
  // operations are never invoked in that case here: it is covered with a scripted gateway (encrypted_password_auth_test.dart) and by
  // enc_auth_builtin_sealer_test.dart.
  final hasValidKey = EncAuthRuntime.buildSealer(EncAuthConfig.keyId, EncAuthConfig.publicKeySpkiBase64) != null;

  group('required mode (ENC_AUTH_MODE=required) without a valid key', () {
    final skip = EncAuthConfig.isRequired && !hasValidKey ? null : 'only in a required-mode run without a valid key';

    test('with no sealer registered every operation fails closed, with a fixed error, and no Firebase password API is touched', () async {
      for (final run in operations()) {
        final error = await outcome(run);
        expect(error, isA<FirebaseAuthException>(), reason: 'a plaintext attempt would have failed with a Firebase platform error instead');
        expect((error as FirebaseAuthException).code, 'enc-auth-unavailable');
      }
    }, skip: skip);

    test('the legacy path cannot be reached by registering or clearing a sealer either', () async {
      EncAuthRuntime.clearForTest();
      final error = await outcome(() => PasswordAuth.signInWithEmailAndPassword(email: 'a@example.invalid', password: 'Passw0rd!x'));
      expect((error as FirebaseAuthException).code, 'enc-auth-unavailable');
    }, skip: skip);
  });

  group('off mode (the default - a normal build)', () {
    final skip = EncAuthConfig.isOff ? null : 'only in an off-mode run';

    test('the unchanged Firebase path is used: the failure comes from Firebase itself, never from the encrypted gateway', () async {
      for (final run in operations()) {
        final error = await outcome(run);
        expect(error, isNotNull);
        if (error is FirebaseAuthException) expect(error.code, isNot('enc-auth-unavailable'));
      }
    }, skip: skip);
  });
}
