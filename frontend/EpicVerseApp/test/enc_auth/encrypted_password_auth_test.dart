// The four encrypted operations: UID/session behaviour, signup recovery, the "unknown outcome" and "no session" paths, and the
// guarantee that a failure never reaches the Firebase session. Gateway and Firebase session are scripted; no network.
import 'package:epicverse/core/security/enc_auth/enc_auth_client.dart';
import 'package:epicverse/core/security/enc_auth/encrypted_password_auth.dart';
import 'package:epicverse/core/security/enc_auth/password_auth.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_test/flutter_test.dart';

import 'enc_auth_test_support.dart';

const String email = 'person.under.test@example.invalid';
const String password = 'Correct-Horse-9!Battery';
const String newPassword = 'N3w-Staple-Horse!Battery';

EncryptedPasswordAuth auth(FakeGateway gateway, RecordingSession session) =>
    EncryptedPasswordAuth(client: EncAuthClient(transport: gateway, sealer: testSealer()), session: session);

Future<FirebaseAuthException> failure(Future<Object?> Function() run) async {
  try {
    await run();
  } on FirebaseAuthException catch (e) {
    return e;
  }
  fail('expected a FirebaseAuthException');
}

void main() {
  group('login', () {
    test('signs in with the custom token the gateway returned, and only that', () async {
      final gateway = FakeGateway(opBody: {'custom_token': 'token-for-the-verified-uid'});
      final session = RecordingSession();
      final credential = await auth(gateway, session).signIn(email: email, password: password);
      expect(credential, isA<UserCredential>());
      expect(session.calls, ['signInWithCustomToken']);
      expect(session.tokens, ['token-for-the-verified-uid']);
      expect(gateway.posts.last.path, '/auth/enc/login');
    });

    test('a wrong password signs nobody in and touches no Firebase session', () async {
      final gateway = FakeGateway(opStatus: 400, opBody: {'error': 'invalid-credential'});
      final session = RecordingSession();
      final error = await failure(() => auth(gateway, session).signIn(email: email, password: password));
      expect(error.code, 'invalid-credential');
      expect(session.calls, isEmpty);
    });

    test('a success answer without a usable custom token is an error, never a silent success', () async {
      for (final body in <Map<String, dynamic>>[{}, {'custom_token': ''}, {'custom_token': 5}, {'custom_token': null}]) {
        final session = RecordingSession();
        await failure(() => auth(FakeGateway(opBody: body), session).signIn(email: email, password: password));
        expect(session.calls, isEmpty);
      }
    });
  });

  group('signup', () {
    test('created: signs in with the token and reports created', () async {
      final session = RecordingSession();
      final gateway = FakeGateway(opBody: {'custom_token': 't1', 'created': true});
      final outcome = await auth(gateway, session).signUpOrRecover(email: email, password: password, emailProof: 'evp1_proof');
      expect(outcome, SignUpOutcome.created);
      expect(session.tokens, ['t1']);
      expect(gateway.posts.last.headers['X-Email-Proof'], 'evp1_proof');
    });

    test('recovered: the gateway proved ownership of an unfinished signup with the same password', () async {
      final session = RecordingSession();
      final outcome = await auth(FakeGateway(opBody: {'custom_token': 't2', 'created': false, 'recovered': true}), session)
          .signUpOrRecover(email: email, password: password, emailProof: 'evp1_proof');
      expect(outcome, SignUpOutcome.recovered);
      expect(session.tokens, ['t2']);
    });

    test('already registered with a different password: nobody is signed in', () async {
      final session = RecordingSession();
      final outcome = await auth(FakeGateway(opStatus: 400, opBody: {'error': 'email-already-in-use'}), session)
          .signUpOrRecover(email: email, password: password, emailProof: 'evp1_proof');
      expect(outcome, SignUpOutcome.alreadyRegistered);
      expect(session.calls, isEmpty);
    });

    test('an expired or missing e-mail proof is an error and signs nobody in', () async {
      final session = RecordingSession();
      final error = await failure(() => auth(FakeGateway(opStatus: 403, opBody: {'error': 'email-proof-invalid'}), session)
          .signUpOrRecover(email: email, password: password));
      expect(error.code, 'email-proof-invalid');
      expect(session.calls, isEmpty);
    });

    test('other failures are rethrown, not turned into a recovery', () async {
      for (final spec in [(400, 'weak-password'), (429, 'too-many-requests'), (503, 'AUTH_UNAVAILABLE')]) {
        final session = RecordingSession();
        await failure(() => auth(FakeGateway(opStatus: spec.$1, opBody: {'error': spec.$2}), session)
            .signUpOrRecover(email: email, password: password, emailProof: 'p'));
        expect(session.calls, isEmpty);
      }
    });
  });

  group('re-authentication', () {
    test('uses the signed-in e-mail, signs in with the fresh token and then refreshes the ID token', () async {
      final session = RecordingSession();
      final gateway = FakeGateway(opBody: {'custom_token': 'fresh'});
      await auth(gateway, session).reauthenticate(password);
      expect(session.calls, ['signInWithCustomToken', 'refreshIdToken']);
      expect(gateway.posts.last.path, '/auth/enc/reauth');
    });

    test('a wrong password changes nothing', () async {
      final session = RecordingSession();
      final error = await failure(() => auth(FakeGateway(opStatus: 400, opBody: {'error': 'invalid-credential'}), session).reauthenticate('x'));
      expect(error.code, 'invalid-credential');
      expect(session.calls, isEmpty);
    });

    test('nobody signed in: nothing is sent', () async {
      final gateway = FakeGateway();
      final session = RecordingSession(email: null);
      await expectLater(auth(gateway, session).reauthenticate(password), throwsStateError);
      expect(gateway.posts, isEmpty);
    });
  });

  group('password change', () {
    test('success: the old sessions are gone, so the app signs in with the fresh token', () async {
      final session = RecordingSession();
      final gateway = FakeGateway(opBody: {'password_changed': true, 'custom_token': 'new-session', 'sessions_revoked': true});
      await auth(gateway, session).changePassword(current: password, next: newPassword);
      expect(session.tokens, ['new-session']);
      expect(gateway.posts.last.path, '/auth/enc/change_password');
    });

    test('changed but no session could be issued: the user is signed out, never left half signed in', () async {
      final session = RecordingSession();
      await auth(FakeGateway(opBody: {'password_changed': true, 'custom_token': null, 'retry_signin': true}), session)
          .changePassword(current: password, next: newPassword);
      expect(session.calls, ['signOut']);
    });

    test('the same password: the gateway answers password-unchanged and nothing is signed in', () async {
      final session = RecordingSession();
      final error = await failure(() => auth(FakeGateway(opStatus: 400, opBody: {'error': 'password-unchanged'}), session)
          .changePassword(current: password, next: password));
      expect(error.code, 'password-unchanged');
      expect(session.calls, isEmpty);
    });

    test('an unknown outcome is reported as such, not as "unchanged"', () async {
      final error = await failure(() => auth(FakeGateway(opStatus: 503, opBody: {'error': 'AUTH_UNAVAILABLE', 'password_changed': 'unknown'}), RecordingSession())
          .changePassword(current: password, next: newPassword));
      expect(error.code, 'password-change-unknown');
    });

    test('wrong current password, weak new password, expired bearer', () async {
      for (final spec in [(400, 'invalid-credential', 'invalid-credential'), (400, 'weak-password', 'weak-password'), (401, 'AUTH_FAILED', 'user-token-expired')]) {
        final session = RecordingSession();
        final error = await failure(() => auth(FakeGateway(opStatus: spec.$1, opBody: {'error': spec.$2}), session)
            .changePassword(current: password, next: newPassword));
        expect(error.code, spec.$3);
        expect(session.calls, isEmpty);
      }
    });

    test('nobody signed in: requires-recent-login and nothing is sent', () async {
      final gateway = FakeGateway();
      final error = await failure(() => auth(gateway, RecordingSession(email: null)).changePassword(current: password, next: newPassword));
      expect(error.code, 'requires-recent-login');
      expect(gateway.posts, isEmpty);
    });
  });

  group('credentials never reach the Firebase session', () {
    test('the session port only ever receives custom tokens', () async {
      final session = RecordingSession();
      final gateway = FakeGateway(opBody: {'custom_token': 'only-a-token', 'created': true});
      final a = auth(gateway, session);
      await a.signIn(email: email, password: password);
      await a.signUpOrRecover(email: email, password: password, emailProof: 'p');
      await a.reauthenticate(password);
      await a.changePassword(current: password, next: newPassword);
      expect(session.tokens.toSet(), {'only-a-token'});
      expect(session.calls.where((c) => !['signInWithCustomToken', 'refreshIdToken', 'signOut'].contains(c)), isEmpty);
    });
  });
}
