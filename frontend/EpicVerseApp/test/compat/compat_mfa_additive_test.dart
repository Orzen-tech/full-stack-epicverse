// Real-HTTP tests of the app's MFA enable / disable / login / persistence against the ADDITIVE backend (A) and, for
// rollback behaviour, the deployed backend (L). Isolated local containers, fake users only. Skipped otherwise.
import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/network/api_client.dart';
import 'package:epicverse/core/network/mfa_session_manager.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/core/security/secure_session_store.dart';
import 'package:epicverse/core/security/session_gate.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'compat_support.dart';

const _verified = ('fake-verified', 'verified@example.invalid');
const _mfaUser = ('fake-mfa', 'mfa@example.invalid');
const _key = PersistedMfaSession.storageKey;

Future<Map> _profile((String, String) u) async {
  signInAs(u.$1, u.$2);
  return (await apiClient.get('/user/${u.$1}')).data as Map;
}

/// Full modern MFA login with a Firebase sign-in [age] seconds old; returns the stored session token.
Future<String> _login((String, String) u, {int age = 30}) async {
  signInAs(u.$1, u.$2, ageSeconds: age);
  String? id;
  expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (v) => id = v), isNull);
  expect(await MfaFlow.verifyChallenge(MfaFlow.loginVerifyPath, id!, await latestOtp(u.$2), u.$1), isNull);
  return MfaSessionManager.getSessionForUid(u.$1)!;
}

Future<void> _restart(MemorySecureStore store) async {
  MfaSessionManager.simulateRestartForTest(); // process killed: memory gone, secure storage remains
  await MfaSessionManager.restore();
}

void main() {
  if (!(compatAdditive || compatBackend == 'L')) {
    test('additive-backend MFA tests (need the local contract runner)', () {}, skip: 'COMPAT_BACKEND not A/L');
    return;
  }
  late MemorySecureStore store;

  setUp(() async {
    await resetBackend();
    store = MemorySecureStore();
    SharedPreferences.setMockInitialValues({'mfa_store_initialized': true});
    MfaSessionManager.useStoreForTest(store);
    MfaSessionManager.simulateRestartForTest();
  });

  tearDown(() {
    signOutFake();
    MfaSessionManager.simulateRestartForTest();
    MfaSessionManager.useStoreForTest(null);
  });

  group('rollback / deployed backend', () {
    test('the deployed backend is detected as legacy, so the toggle is disabled and no session is used', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2);
      final probe = await MfaFlow.probeSessionStatus();
      expect([probe.legacy, probe.unavailable], [true, false]);
      expect(MfaSettingsState(MfaSettingsState.capabilityFor(probe.status)).toggleEnabled, isFalse);
      expect((await _profile(_mfaUser))['mfa_enabled'], true, reason: 'the stored preference is untouched');
      expect(SessionGate.needsLegacyClientMfa(probe.status, await _profile(_mfaUser)), isTrue);
    }, skip: compatIsLegacy ? null : 'deployed backend only');
  }, skip: compatBackend == 'L' ? null : 'deployed backend only');

  if (!compatAdditive) return; // everything below needs the additive backend's routes

  group('enable MFA', () {
    test('stale sign-in is refused before any code is sent', () async {
      signInAs(_verified.$1, _verified.$2, ageSeconds: 1800);
      final err = await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (_) {});
      expect(err, isNotNull);
      expect((await _profile(_verified))['mfa_enabled'], false);
    });

    test('wrong code does not enable; the right code enables, persists the proof and the server accepts it after a restart', () async {
      signInAs(_verified.$1, _verified.$2, ageSeconds: 30);
      String? id;
      expect(await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (v) => id = v), isNull);
      expect((await _profile(_verified))['mfa_enabled'], false, reason: 'nothing changes before confirmation');
      signInAs(_verified.$1, _verified.$2, ageSeconds: 30);
      final otp = await latestOtp(_verified.$2);
      expect(await MfaFlow.verifyChallenge(MfaFlow.enableConfirmPath, id!, otp == '000000' ? '111111' : '000000', _verified.$1), isNotNull);
      expect(MfaSessionManager.getSessionForUid(_verified.$1), isNull);
      expect((await _profile(_verified))['mfa_enabled'], false);

      signInAs(_verified.$1, _verified.$2, ageSeconds: 30);
      expect(await MfaFlow.verifyChallenge(MfaFlow.enableConfirmPath, id!, otp, _verified.$1), isNull);
      expect((await _profile(_verified))['mfa_enabled'], true);
      await Future<void>.delayed(Duration.zero);
      expect(store.values.containsKey(_key), isTrue, reason: 'proof persisted in secure storage');

      await _restart(store);
      signInAs(_verified.$1, _verified.$2, ageSeconds: 30);
      final status = (await MfaFlow.fetchSessionStatus())!;
      expect([status.mfaEnabled, status.mfaSessionValid], [true, true], reason: 'server validated the restored token');
    });
  });

  group('MFA login and persistence across restarts', () {
    test('restored token is validated by the server for the same sign-in', () async {
      await _login(_mfaUser);
      await Future<void>.delayed(Duration.zero);
      await _restart(store);
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      final s = (await MfaFlow.fetchSessionStatus())!;
      expect([s.mfaEnabled, s.mfaSessionValid, SessionGate.needsServerMfaChallenge(s)], [true, true, false]);
    });

    test('Firebase sign-in alone is not MFA proof: with no stored proof a challenge is required', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2);
      final s = (await MfaFlow.fetchSessionStatus())!;
      expect([s.mfaSessionValid, SessionGate.needsServerMfaChallenge(s)], [false, true]);
    });

    test('a tampered stored token is rejected by the server', () async {
      await _login(_mfaUser);
      await Future<void>.delayed(Duration.zero);
      final stored = PersistedMfaSession.decode(store.values[_key])!;
      store.values[_key] = PersistedMfaSession(uid: stored.uid, token: '${stored.token.substring(0, stored.token.length - 3)}AAA', expiresAtMs: stored.expiresAtMs).encode();
      await _restart(store);
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      expect((await MfaFlow.fetchSessionStatus())!.mfaSessionValid, isFalse);
    });

    test('a different Firebase sign-in (new auth time) does not match the stored proof', () async {
      await _login(_mfaUser, age: 600);
      await Future<void>.delayed(Duration.zero);
      await _restart(store);
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 5); // e.g. after password re-authentication
      expect((await MfaFlow.fetchSessionStatus())!.mfaSessionValid, isFalse);
    });

    test('server-side revocation (logout elsewhere) invalidates the restored proof', () async {
      final token = await _login(_mfaUser);
      await Future<void>.delayed(Duration.zero);
      await apiClient.post('/auth/mfa/logout', options: Options(headers: {'X-MFA-Session': token}));
      await _restart(store);
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      expect((await MfaFlow.fetchSessionStatus())!.mfaSessionValid, isFalse);
    });

    test('logout (clearAll) leaves nothing to restore', () async {
      await _login(_mfaUser);
      await MfaSessionManager.clearAll();
      expect(store.values.containsKey(_key), isFalse);
      await _restart(store);
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      expect((await MfaFlow.fetchSessionStatus())!.mfaSessionValid, isFalse);
    });

    test('account switching: another user never gets the previous user\'s proof, and it is wiped', () async {
      await _login(_mfaUser);
      await Future<void>.delayed(Duration.zero);
      await _restart(store);
      expect(MfaSessionManager.getSessionForUid(_verified.$1), isNull);
      await Future<void>.delayed(Duration.zero);
      expect(store.values.containsKey(_key), isFalse);
    });

    test('wrong code is rejected, no proof is stored', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      String? id;
      expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (v) => id = v), isNull);
      final otp = await latestOtp(_mfaUser.$2);
      expect(await MfaFlow.verifyChallenge(MfaFlow.loginVerifyPath, id!, otp == '000000' ? '111111' : '000000', _mfaUser.$1), isNotNull);
      await Future<void>.delayed(Duration.zero);
      expect([MfaSessionManager.getSessionForUid(_mfaUser.$1), store.values.containsKey(_key)], [null, false]);
    });
  });

  group('disable MFA', () {
    test('without a session: refused with MFA_SESSION_REQUIRED and the preference is unchanged', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      await expectLater(apiClient.post('/user/mfa/disable'),
          throwsA(isA<AppException>().having((e) => e.type, 'type', AppExceptionType.mfaSessionRequired)));
      expect((await _profile(_mfaUser))['mfa_enabled'], true);
    });

    test('valid session but a stale sign-in: refused with RECENT_SIGN_IN_REQUIRED', () async {
      await _login(_mfaUser, age: 1800);
      await expectLater(apiClient.post('/user/mfa/disable'),
          throwsA(isA<AppException>().having((e) => e.type, 'type', AppExceptionType.recentSignInRequired)));
      expect((await _profile(_mfaUser))['mfa_enabled'], true);
    });

    test('password re-authentication changes the sign-in time, so a fresh code is required, then disable succeeds', () async {
      await _login(_mfaUser, age: 600);
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 2); // re-authenticated with the password
      await expectLater(apiClient.post('/user/mfa/disable'),
          throwsA(isA<AppException>().having((e) => e.type, 'type', AppExceptionType.mfaSessionRequired)));
      expect(MfaSessionManager.getSessionForUid(_mfaUser.$1), isNull, reason: 'the stale session is dropped');
      String? id;
      expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (v) => id = v), isNull);
      expect(await MfaFlow.verifyChallenge(MfaFlow.loginVerifyPath, id!, await latestOtp(_mfaUser.$2), _mfaUser.$1), isNull);
      expect((await apiClient.post('/user/mfa/disable')).statusCode, 200);
      await MfaSessionManager.clearAll();
      expect((await _profile(_mfaUser))['mfa_enabled'], false);
      await _restart(store);
      expect(MfaSessionManager.getSessionForUid(_mfaUser.$1), isNull);
    });

    test('the legacy endpoint can no longer be used to switch MFA off or on', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      await expectLater(apiClient.post('/user/update-mfa', data: FormData.fromMap({'mfa_enabled': false})), throwsA(isA<AppException>()));
      expect((await _profile(_mfaUser))['mfa_enabled'], true);
      signInAs(_verified.$1, _verified.$2, ageSeconds: 30);
      await expectLater(apiClient.post('/user/update-mfa', data: FormData.fromMap({'mfa_enabled': true})), throwsA(isA<AppException>()));
      expect((await _profile(_verified))['mfa_enabled'], false);
    });
  });

  group('rate limit (unchanged) and temporary failures', () {
    test('the 4th code request in a window is a 429 that the app classifies as temporary, with Retry-After', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2, ageSeconds: 30);
      for (var i = 0; i < 3; i++) {
        expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (_) {}), isNull);
      }
      AppException? caught;
      try {
        await apiClient.post(MfaFlow.loginChallengePath);
      } on AppException catch (e) {
        caught = e;
      }
      expect(caught, isNotNull);
      expect(MfaFlow.failureFor(caught!), MfaChallengeOutcome.rateLimited);
      expect(MfaFlow.retryAfterOf(caught), isNotNull);
      expect(await MfaFlow.requestChallenge(MfaFlow.loginChallengePath, (_) {}), contains('Too many'));
    });

    test('status probe on the modern backend reports a modern, available server', () async {
      signInAs(_mfaUser.$1, _mfaUser.$2);
      final probe = await MfaFlow.probeSessionStatus();
      expect([probe.legacy, probe.unavailable, probe.status!.mfaEnabled], [false, false, true]);
    });
  });

  group('enable MFA: a sign-in that goes stale (RECENT_SIGN_IN_REQUIRED)', () {
    test('requesting a code with a stale sign-in is classified as re-authentication required', () async {
      signInAs(_verified.$1, _verified.$2, ageSeconds: 1800);
      AppException? caught;
      try {
        await apiClient.post(MfaFlow.enableRequestPath);
      } on AppException catch (e) {
        caught = e;
      }
      expect(caught, isNotNull);
      expect(MfaFlow.failureFor(caught!), MfaChallengeOutcome.reauthRequired);
      expect((await _profile(_verified))['mfa_enabled'], false);
    });

    test('the sign-in goes stale between requesting and confirming: nothing is enabled, then a fresh attempt succeeds', () async {
      signInAs(_verified.$1, _verified.$2, ageSeconds: 30);
      String? id;
      expect(await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (v) => id = v), isNull);
      final firstOtp = await latestOtp(_verified.$2);

      signInAs(_verified.$1, _verified.$2, ageSeconds: 1800); // the code entry took too long
      final stale = await MfaFlow.verifyStep(MfaFlow.enableConfirmPath, id!, firstOtp, _verified.$1);
      expect(stale.failure, MfaChallengeOutcome.reauthRequired);
      expect(stale.error, contains('confirm your password again'));
      expect(MfaSessionManager.getSessionForUid(_verified.$1), isNull);
      expect((await _profile(_verified))['mfa_enabled'], false, reason: 'the refused confirmation enabled nothing');

      // The user confirms the password again (a fresh Firebase sign-in) and starts over with a new code.
      signInAs(_verified.$1, _verified.$2, ageSeconds: 2);
      String? id2;
      expect(await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (v) => id2 = v), isNull);
      final secondOtp = await latestOtp(_verified.$2);
      final ok = await MfaFlow.verifyStep(MfaFlow.enableConfirmPath, id2!, secondOtp, _verified.$1);
      expect(ok.failure, isNull);
      expect((await _profile(_verified))['mfa_enabled'], true);
    });

    test('the retry does not raise the code limit: a 4th code request in the window is still refused', () async {
      signInAs(_verified.$1, _verified.$2, ageSeconds: 5);
      for (var i = 0; i < 3; i++) {
        expect(await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (_) {}), isNull);
      }
      expect(await MfaFlow.requestChallenge(MfaFlow.enableRequestPath, (_) {}), contains('Too many'));
    });
  });
}
