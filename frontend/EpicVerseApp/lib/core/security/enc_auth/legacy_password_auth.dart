import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart' show debugPrint;
import 'password_auth.dart';

/// The existing Firebase password path, moved here unchanged so it is the ONE place that calls the plaintext Firebase password APIs
/// (a test enforces that). It is used only when `ENC_AUTH_MODE` is `off`, which is every normal build today.
class LegacyPasswordAuth implements PasswordAuthBackend {
  const LegacyPasswordAuth();

  @override
  Future<UserCredential> signIn({required String email, required String password}) =>
      FirebaseAuth.instance.signInWithEmailAndPassword(email: email, password: password);

  @override
  Future<SignUpOutcome> signUpOrRecover({required String email, required String password, String? emailProof}) async {
    try {
      await FirebaseAuth.instance.createUserWithEmailAndPassword(email: email, password: password);
      return SignUpOutcome.created;
    } on FirebaseAuthException catch (e) {
      if (e.code != 'email-already-in-use') rethrow;
    }
    // The account exists: prove ownership with Firebase itself (the password goes only to Firebase, never to our backend).
    try {
      await FirebaseAuth.instance.signInWithEmailAndPassword(email: email, password: password);
    } on FirebaseAuthException catch (e) {
      debugPrint('[EpicVerse][REG] recovery sign-in failed code=${e.code}');
      return SignUpOutcome.alreadyRegistered;
    }
    return SignUpOutcome.recovered;
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

  @override
  Future<void> changePassword({required String current, required String next}) async {
    final user = FirebaseAuth.instance.currentUser;
    final email = user?.email;
    if (user == null || email == null || email.isEmpty) {
      throw FirebaseAuthException(code: 'requires-recent-login');
    }
    await user.reauthenticateWithCredential(EmailAuthProvider.credential(email: email, password: current));
    if (next == current) throw FirebaseAuthException(code: 'password-unchanged');
    await user.updatePassword(next);
  }
}
