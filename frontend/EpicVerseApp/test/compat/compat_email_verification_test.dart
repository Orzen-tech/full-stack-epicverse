// Real-HTTP contract test: the app's REAL email-verification client code (EmailOtp, EmailVerificationFlow, ApiClient)
// against the email-verification backend (COMPAT_BACKEND=X = the proof tree). Isolated local containers, FAKE users only.
// Skipped unless the local runner passes --dart-define=COMPAT_BACKEND=X and --dart-define=API_BASE_URL=http://127.0.0.1:<port>.
import 'package:dio/dio.dart';
import 'package:epicverse/core/network/api_client.dart';
import 'package:epicverse/core/security/email_verification_flow.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/core/security/session_gate.dart';
import 'package:flutter_test/flutter_test.dart';

import 'compat_support.dart';

const _invite = 'EPIC-TEST01';

Future<Map> _profile(String uid, String email) async {
  signInAs(uid, email);
  return (await apiClient.get('/user/$uid')).data as Map;
}

/// What the app's signup screen does before the account exists: send the code, then verify it. Returns the proof.
Future<String?> _verifyEmailLikeSignup(String email) async {
  await apiClient.post('/auth/send-email-otp', data: FormData.fromMap({'identifier': email}));
  final result = await EmailOtp.verify(email, await latestOtp(email));
  expect(result.error, isNull);
  return result.proof;
}

/// What the app does right after Firebase creates the account: create the profile with the invite code.
Future<void> _syncUser(String uid, String email) async {
  signInAs(uid, email);
  await apiClient.post('/sync-user', data: {
    'firebase_id': uid,
    'display_name': 'Fake New',
    'email': email,
    'invite_code': _invite,
    'session_id': 'contract-session',
  });
}

void main() {
  if (compatBackend != 'X') {
    test('email-verification contract tests (need the local contract runner)', () {}, skip: 'COMPAT_BACKEND is not X');
    return;
  }

  setUp(() async {
    await resetBackend();
    signOutFake(); // installs the identity override, so a test never reaches FirebaseAuth
  });
  tearDown(signOutFake);

  group('signup (the registration screen)', () {
    test('the proof returned by verify-otp is accepted by the app and verifies exactly the new account', () async {
      const uid = 'fake-new-1';
      const email = 'fake-new-1@example.invalid';
      final proof = await _verifyEmailLikeSignup(email);
      expect(proof, isNotNull, reason: 'the app only keeps a proof that starts with evp1_');
      expect(proof, startsWith(EmailOtp.proofPrefix));
      await _syncUser(uid, email);
      expect((await _profile(uid, email))['email_verified'], false, reason: 'the profile starts unverified');
      expect(await EmailVerificationFlow.markVerified(proof), isTrue);
      expect((await _profile(uid, email))['email_verified'], true);
      expect(await EmailVerificationFlow.markVerified(proof), isTrue, reason: 'a retry after a lost response is harmless');
    });

    test('the original bypass is closed for the real client: no proof, a forged proof, or another email\'s proof', () async {
      const uid = 'fake-new-2';
      const email = 'fake-new-2@example.invalid';
      await _syncUser(uid, email);
      expect(await EmailVerificationFlow.markVerified(null), isFalse);
      expect(await EmailVerificationFlow.markVerified('evp1_${'A' * 43}'), isFalse);
      final other = await _verifyEmailLikeSignup('someone-else@example.invalid');
      signInAs(uid, email);
      expect(await EmailVerificationFlow.markVerified(other), isFalse);
      expect((await _profile(uid, email))['email_verified'], false);
    });

    test('a wrong code is rejected with the app\'s own message and gives no proof', () async {
      const email = 'fake-new-3@example.invalid';
      await apiClient.post('/auth/send-email-otp', data: FormData.fromMap({'identifier': email}));
      final good = await latestOtp(email);
      final wrong = good == '000000' ? '111111' : '000000';
      final result = await EmailOtp.verify(email, wrong);
      expect(result.proof, isNull);
      expect(result.error, 'Invalid or expired verification code. Please try again.');
    });
  });

  group('existing signed-in user (login / splash for an unverified account)', () {
    const uid = 'fake-unverified';
    const email = 'unverified@example.invalid';

    test('the backend does not block an unverified account from its profile or session routes', () async {
      final profile = await _profile(uid, email);
      expect(profile['email_verified'], false);
      final status = await MfaFlow.fetchSessionStatus();
      expect(status, isNotNull);
      expect(SessionGate.emailVerified(status, profile), isFalse, reason: 'the app then starts the verification flow');
      await apiClient.post('/auth/update-session', data: FormData.fromMap({'session_id': 'contract-session'}));
    });

    test('request, wrong code, right code: the account becomes verified and the app sees it', () async {
      signInAs(uid, email);
      final start = await EmailVerificationFlow.requestChallenge();
      expect(start.outcome, EmailChallengeOutcome.sent);
      expect(start.challengeId, isNotEmpty);
      final otp = await latestOtp(email);
      final wrong = otp == '000000' ? '111111' : '000000';
      expect(await EmailVerificationFlow.confirm(start.challengeId!, wrong), isNotNull);
      expect((await _profile(uid, email))['email_verified'], false);
      expect(await EmailVerificationFlow.confirm(start.challengeId!, otp), isNull);
      final profile = await _profile(uid, email);
      expect(profile['email_verified'], true);
      expect(SessionGate.emailVerified(await MfaFlow.fetchSessionStatus(), profile), isTrue);
      final again = await EmailVerificationFlow.requestChallenge();
      expect(again.outcome, EmailChallengeOutcome.alreadyVerified);
    });
  });

  group('accounts that were already verified are untouched', () {
    test('profile, session status and session update still work and nothing asks for verification', () async {
      const uid = 'fake-verified';
      const email = 'verified@example.invalid';
      final profile = await _profile(uid, email);
      expect(profile['email_verified'], true);
      final status = await MfaFlow.fetchSessionStatus();
      expect(SessionGate.emailVerified(status, profile), isTrue);
      await apiClient.post('/auth/update-session', data: FormData.fromMap({'session_id': 'contract-session'}));
      expect(((await apiClient.get('/auth/check-session', queryParameters: {'session_id': 'contract-session'})).data as Map)['valid'], true);
      expect((await EmailVerificationFlow.requestChallenge()).outcome, EmailChallengeOutcome.alreadyVerified);
    });
  });
}
