// Backend-contract tests. They drive the app's REAL networking stack, error mapping and flow helpers against local,
// isolated copies of the deployed backend (L = d733776) and the newer backend (M = e121583), with FAKE users only.
// Skipped unless run via the local contract runner (see compat_support.dart).
import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/network/api_client.dart';
import 'package:epicverse/core/network/mfa_session_manager.dart';
import 'package:epicverse/core/network/websocket_service.dart';
import 'package:epicverse/core/security/email_verification_flow.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/core/security/session_gate.dart';
import 'package:flutter_test/flutter_test.dart';

import 'compat_support.dart';

const _verified = ('fake-verified', 'verified@example.invalid');
const _unverified = ('fake-unverified', 'unverified@example.invalid');
const _mfaUser = ('fake-mfa', 'mfa@example.invalid');

Future<Map> _profile((String, String) u) async {
  signInAs(u.$1, u.$2);
  final r = await apiClient.get('/user/${u.$1}');
  return r.data as Map;
}

/// Opens the AI Companion WebSocket exactly as the app builds it and reports how the backend's auth gate answered:
/// 'ACCEPTED' or 'REFUSED:<code>'. Only the handshake is observed; nothing is streamed.
Future<String> _wsProbe(String uid, String email, {bool withToken = true, String? mfaSession}) async {
  final base = const String.fromEnvironment('API_BASE_URL').replaceFirst('http', 'ws');
  final query = buildWsQuery(uid: uid, mode: 'Mode 1', sessionId: 'compat', listening: false, language: 'English');
  final headers = withToken
      ? buildWsHandshakeHeaders(idToken: fakeIdToken(uid, email), mfaSession: mfaSession)
      : <String, String>{};
  final ws = await WebSocket.connect('$base/api/v1/ws/realtime?$query', headers: headers);
  var verdict = 'ACCEPTED';
  final done = Completer<void>();
  final sub = ws.listen((m) {
    if (m is String) {
      final code = wsSecurityRefusalCode(m);
      if (code != null) verdict = 'REFUSED:$code';
      try {
        final d = jsonDecode(m);
        if (d is Map && d['type'] == 'error' && d['message'] == 'Unauthorized' && code == null) {
          verdict = 'REFUSED:Unauthorized';
        }
      } catch (_) {}
    }
  }, onDone: () {
    if (ws.closeCode == 1008 && verdict == 'ACCEPTED') verdict = 'REFUSED:1008';
    if (!done.isCompleted) done.complete();
  });
  await Future.any([done.future, Future<void>.delayed(const Duration(seconds: 3))]);
  await sub.cancel();
  await ws.close();
  return verdict;
}

/// Signup step 1: the open pre-registration OTP request (no session exists yet).
Future<void> _requestSignupOtp(String uid) async {
  signOutFake();
  await apiClient.post('/auth/send-email-otp', data: FormData.fromMap({'identifier': '$uid@example.invalid'}));
}

/// Signup step 3: the account exists in the identity service; create the profile with the invite code.
Future<void> _syncUser(String uid) async {
  final email = '$uid@example.invalid';
  signInAs(uid, email);
  await apiClient.post('/sync-user', data: {
    'firebase_id': uid,
    'display_name': 'Fake New',
    'email': email,
    'phone_number': null,
    'invite_code': 'EPIC-TEST01',
    'profile_picture': null,
    'session_id': 'compat-session',
  });
}

void main() {
  if (!compatEnabled) {
    // Plain `flutter test`: these need the isolated local backends (see the local contract runner).
    test('backend contract tests (need the local contract runner)', () {}, skip: 'COMPAT_BACKEND not set');
    return;
  }
  tearDown(() {
    signOutFake();
    MfaSessionManager.clear();
  });

  group('session-status fallback', () {
    test('status route: missing on the deployed backend (null), present on the newer one', () async {
      signInAs(_verified.$1, _verified.$2);
      final status = await MfaFlow.fetchSessionStatus();
      if (compatIsLegacy) {
        expect(status, isNull);
        expect(MfaSettingsState.capabilityFor(status), MfaSettingsCapability.legacy);
      } else {
        expect(status, isNotNull);
        expect(status!.emailVerified, isTrue);
        expect(status.mfaEnabled, isFalse);
        expect(MfaSettingsState.capabilityFor(status), MfaSettingsCapability.modern);
      }
    });

    test('profile fields and server status drive the login decisions', () async {
      final p = await _profile(_mfaUser);
      final status = await MfaFlow.fetchSessionStatus();
      expect(SessionGate.emailVerified(status, p), isTrue);
      expect(SessionGate.needsLegacyClientMfa(status, p), compatIsLegacy);
      expect(SessionGate.needsServerMfaChallenge(status), !compatIsLegacy);
    });
  });

  group('MFA login', () {
    test('legacy MFA login: authenticated send-otp, then verify-otp; no proof, no MFA session', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2);
      final sendError = await EmailOtp.resend(_mfaUser.$2);
      final otp = await latestOtp(_mfaUser.$2);
      final verify = await EmailOtp.verify(_mfaUser.$2, otp);
      expect(sendError, isNull);
      expect(verify.error, isNull);
      expect(verify.proof, isNull); // the deployed backend returns no proof
      expect(MfaSessionManager.getSessionForUid(_mfaUser.$1), isNull);
    }, skip: compatIsLegacy ? null : 'only the deployed backend uses the legacy MFA prompt');

    test('modern MFA login: challenge, verify, MFA session, session-status valid', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2);
      expect((await MfaFlow.fetchSessionStatus())!.mfaSessionValid, isFalse);
      String? challengeId;
      expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (id) => challengeId = id), isNull);
      final otp = await latestOtp(_mfaUser.$2);
      expect(await MfaFlow.verifyChallenge(MfaFlow.loginVerifyPath, challengeId!, otp, _mfaUser.$1), isNull);
      expect(MfaSessionManager.getSessionForUid(_mfaUser.$1), isNotNull);
      expect((await MfaFlow.fetchSessionStatus())!.mfaSessionValid, isTrue);
    }, skip: compatIsLegacy ? 'the deployed backend has no MFA challenge routes' : null);

    test('modern MFA login: a wrong code is rejected and no session is stored', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2);
      String? challengeId;
      expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (id) => challengeId = id), isNull);
      final err = await MfaFlow.verifyChallenge(MfaFlow.loginVerifyPath, challengeId!, '000000', _mfaUser.$1);
      expect(err, isNotNull);
      expect(MfaSessionManager.getSessionForUid(_mfaUser.$1), isNull);
    }, skip: compatIsLegacy ? 'the deployed backend has no MFA challenge routes' : null);
  });

  group('AI Companion WebSocket authorization', () {
    test('no token is refused on both backends', () async {
      expect((await _wsProbe('fake-verified', _verified.$2, withToken: false)).startsWith('REFUSED'), isTrue);
    });

    test('verified user without MFA is accepted on both backends', () async {
      expect(await _wsProbe(_verified.$1, _verified.$2), 'ACCEPTED');
    });

    test('unverified email: accepted by the deployed backend, refused by the newer one', () async {
      final v = await _wsProbe('fake-new-5', 'fake-new-5@example.invalid');
      expect(v, compatIsLegacy ? 'ACCEPTED' : 'REFUSED:EMAIL_VERIFICATION_REQUIRED');
    });

    test('MFA user: no session is refused (newer backend); a valid session is accepted', () async {
      if (compatIsLegacy) {
        expect(await _wsProbe(_mfaUser.$1, _mfaUser.$2), 'ACCEPTED'); // the deployed backend ignores MFA
        return;
      }
      expect(await _wsProbe(_mfaUser.$1, _mfaUser.$2), 'REFUSED:MFA_SESSION_REQUIRED');
      signInAs(_mfaUser.$1, _mfaUser.$2);
      String? challengeId;
      await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (id) => challengeId = id);
      final otp = await latestOtp(_mfaUser.$2);
      expect(await MfaFlow.verifyChallenge(MfaFlow.loginVerifyPath, challengeId!, otp, _mfaUser.$1), isNull);
      final session = MfaSessionManager.getSessionForUid(_mfaUser.$1);
      expect(await _wsProbe(_mfaUser.$1, _mfaUser.$2, mfaSession: session), 'ACCEPTED');
    });

    test('the deployed backend ignores the extra X-MFA-Session header', () async {
      expect(await _wsProbe(_verified.$1, _verified.$2, mfaSession: 'unused-session'), 'ACCEPTED');
    }, skip: compatIsLegacy ? null : 'deployed backend only');
  });

  group('signup', () {
    test('signup WITHOUT a verification proof', () async {
      const uid = 'fake-new-1';
      final email = '$uid@example.invalid';
      await _requestSignupOtp(uid);
      final verify = await EmailOtp.verify(email, await latestOtp(email));
      expect(verify.error, isNull);
      if (compatIsLegacy) expect(verify.proof, isNull);
      await _syncUser(uid);
      final marked = await EmailVerificationFlow.markVerified(null); // the app presents whatever proof it holds: none
      if (compatIsLegacy) {
        expect(marked, isTrue); // the deployed backend accepts the authenticated call
        expect((await _profile((uid, email)))['email_verified'], true);
      } else {
        expect(marked, isFalse); // newer backend: no proof, not verified...
        expect((await _profile((uid, email)))['email_verified'], false);
        // ...so the app runs the authenticated recovery before the dashboard:
        final start = await EmailVerificationFlow.requestChallenge();
        expect(start.outcome, EmailChallengeOutcome.sent);
        expect(await EmailVerificationFlow.confirm(start.challengeId!, await latestOtp(email)), isNull);
        expect((await _profile((uid, email)))['email_verified'], true);
      }
    });

    test('signup WITH a verification proof', () async {
      const uid = 'fake-new-2';
      final email = '$uid@example.invalid';
      await _requestSignupOtp(uid);
      final verify = await EmailOtp.verify(email, await latestOtp(email));
      expect(verify.error, isNull);
      expect(verify.proof != null, !compatIsLegacy); // proofs exist only on the newer backend
      await _syncUser(uid);
      expect(await EmailVerificationFlow.markVerified(verify.proof), isTrue);
      expect((await _profile((uid, email)))['email_verified'], true);
    });
  });

  group('existing unverified-account recovery', () {
    test('requestChallenge: missing route falls back (deployed), challenge sent (newer); account becomes verified', () async {
      signInAs(_unverified.$1, _unverified.$2);
      final start = await EmailVerificationFlow.requestChallenge();
      expect(start.outcome, compatIsLegacy ? EmailChallengeOutcome.legacyServer : EmailChallengeOutcome.sent);
      if (compatIsLegacy) {
        // the app's fallback: authenticated send-otp + verify-otp
        expect(await EmailOtp.resend(_unverified.$2), isNull);
        expect((await EmailOtp.verify(_unverified.$2, await latestOtp(_unverified.$2))).error, isNull);
      } else {
        expect(await EmailVerificationFlow.confirm(start.challengeId!, await latestOtp(_unverified.$2)), isNull);
      }
      expect((await _profile(_unverified))['email_verified'], true);
    });

    test('a wrong code does not verify the account', () async {
      const uid = 'fake-new-3';
      final email = '$uid@example.invalid';
      await _requestSignupOtp(uid);
      await EmailOtp.verify(email, await latestOtp(email));
      await _syncUser(uid);
      if (compatIsLegacy) {
        expect(await EmailOtp.resend(email), isNull);
        expect((await EmailOtp.verify(email, '000000')).error, isNotNull);
      } else {
        final start = await EmailVerificationFlow.requestChallenge();
        expect(await EmailVerificationFlow.confirm(start.challengeId!, '000000'), isNotNull);
      }
      expect((await _profile((uid, email)))['email_verified'], false);
    });
  });

  group('MFA Settings', () {
    test('deployed backend: legacy detected, toggle disabled, MFA state untouched', () async {
      final before = (await _profile(_mfaUser))['mfa_enabled'];
      final status = await MfaFlow.fetchSessionStatus();
      final state = MfaSettingsState(MfaSettingsState.capabilityFor(status));
      expect(state.toggleEnabled, isFalse);
      expect(state.message, 'Available after our next server update.');
      expect((await _profile(_mfaUser))['mfa_enabled'], before);
      expect(before, true);
    }, skip: compatIsLegacy ? null : 'deployed backend only');

    test('newer backend: modern detected; enable via OTP; disable via MFA session; legacy toggle refused', () async {
      signInAs(_verified.$1, _verified.$2);
      final state = MfaSettingsState(MfaSettingsState.capabilityFor(await MfaFlow.fetchSessionStatus()));
      expect(state.toggleEnabled, isTrue);
      String? challengeId;
      expect(await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (id) => challengeId = id), isNull);
      final otp = await latestOtp(_verified.$2);
      expect(await MfaFlow.verifyChallenge(MfaFlow.enableConfirmPath, challengeId!, otp, _verified.$1), isNull);
      expect((await _profile(_verified))['mfa_enabled'], true);
      await apiClient.post('/user/mfa/disable');
      expect((await _profile(_verified))['mfa_enabled'], false);
      // The legacy endpoint can no longer enable MFA on the newer backend, which is why the app never calls it.
      signInAs(_verified.$1, _verified.$2);
      await expectLater(
        apiClient.post('/user/update-mfa', data: FormData.fromMap({'mfa_enabled': true})),
        throwsA(isA<AppException>()),
      );
      expect((await _profile(_verified))['mfa_enabled'], false);
    }, skip: compatIsLegacy ? 'newer backend only' : null);
  });
}
