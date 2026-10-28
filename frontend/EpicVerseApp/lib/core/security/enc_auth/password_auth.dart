import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart' show kReleaseMode, visibleForTesting;
import 'enc_auth_config.dart';
import 'encrypted_password_auth.dart';
import 'legacy_password_auth.dart';

/// Result of [PasswordAuth.signUpOrRecover].
enum SignUpOutcome {
  /// A new account was created and the user is signed in.
  created,

  /// The account already existed from an earlier, unfinished signup; ownership was proven with the same password and the user is signed in.
  recovered,

  /// The e-mail is registered and the password did not prove ownership. Nobody is signed in.
  alreadyRegistered,
}

/// The four credential-bearing operations of the app. Exactly one implementation is reachable in a given build:
/// `ENC_AUTH_MODE=required` -> [EncryptedPasswordAuth] only; otherwise the unchanged Firebase password path ([LegacyPasswordAuth]).
abstract class PasswordAuthBackend {
  /// Signs in and returns the Firebase credential.
  Future<UserCredential> signIn({required String email, required String password});

  /// Creates the account, or proves ownership of an unfinished one with the same password. [emailProof] is the OTP proof (memory only).
  Future<SignUpOutcome> signUpOrRecover({required String email, required String password, String? emailProof});

  /// Re-authenticates the signed-in user and refreshes the ID token so the new sign-in time reaches the server.
  Future<void> reauthenticate(String password);

  /// Verifies [current], sets [next]. Throws FirebaseAuthException('password-unchanged') when they are equal.
  Future<void> changePassword({required String current, required String next});
}

class PasswordAuth {
  PasswordAuth._();

  static PasswordAuthBackend? _override;

  // A constant condition: in a `required` build the legacy branch is dead code and is removed from the compiled app.
  static PasswordAuthBackend get _backend =>
      _override ?? (EncAuthConfig.isRequired ? EncryptedPasswordAuth.production() : const LegacyPasswordAuth());

  /// Test seam, never set by app code (a test guards this) and ignored in release builds.
  @visibleForTesting
  static void useForTest(PasswordAuthBackend? fake) {
    if (kReleaseMode) return;
    _override = fake;
  }

  static Future<UserCredential> signInWithEmailAndPassword({required String email, required String password}) =>
      _backend.signIn(email: email, password: password);

  static Future<SignUpOutcome> signUpOrRecover({required String email, required String password, String? emailProof}) =>
      _backend.signUpOrRecover(email: email, password: password, emailProof: emailProof);

  static Future<void> reauthenticate(String password) => _backend.reauthenticate(password);

  static Future<void> changePassword({required String current, required String next}) =>
      _backend.changePassword(current: current, next: next);
}
