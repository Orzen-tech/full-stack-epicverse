import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart' show kReleaseMode, visibleForTesting;

/// Firebase password re-authentication, behind a tiny interface so the Settings flows can be tested without Firebase.
/// The password goes only to Firebase and is never stored or logged.
abstract class PasswordReauthenticator {
  /// True when a user with an email address is signed in.
  bool get canReauthenticate;

  /// Re-authenticates with the account's own email and [password], then refreshes the ID token so the new sign-in
  /// time reaches the server. Throws (for example [FirebaseAuthException]) when the password is wrong.
  Future<void> reauthenticate(String password);
}

class FirebasePasswordReauthenticator implements PasswordReauthenticator {
  const FirebasePasswordReauthenticator();

  @override
  bool get canReauthenticate {
    final user = FirebaseAuth.instance.currentUser;
    return user != null && (user.email ?? '').isNotEmpty;
  }

  @override
  Future<void> reauthenticate(String password) async {
    final user = FirebaseAuth.instance.currentUser;
    final email = user?.email;
    if (user == null || email == null || email.isEmpty) {
      throw StateError('No signed-in user with an email address');
    }
    await user.reauthenticateWithCredential(EmailAuthProvider.credential(email: email, password: password));
    await user.getIdToken(true);
  }
}

class PasswordReauth {
  PasswordReauth._();

  static PasswordReauthenticator _current = const FirebasePasswordReauthenticator();

  static PasswordReauthenticator get current => _current;

  /// Test seam, never set by app code (a test guards this) and ignored in release builds.
  @visibleForTesting
  static void useForTest(PasswordReauthenticator? fake) {
    if (kReleaseMode) return;
    _current = fake ?? const FirebasePasswordReauthenticator();
  }
}
