import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/network/api_client.dart';
import 'package:epicverse/core/network/api_config.dart';
import 'package:epicverse/core/network/mfa_reprompt.dart';
import 'package:epicverse/core/security/device_integrity.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/core/security/splash_auth.dart';
import 'package:epicverse/presentation/screens/mfa_required_gate.dart';
import 'package:epicverse/presentation/screens/splash_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

// Splash start-up must fail closed: the Dashboard opens only after a valid profile AND every session-status,
// email and MFA gate. Real SplashScreen, ApiClient (fake local transport), ErrorMapper, MFA re-prompt hook and
// MfaRequiredGate; only Firebase-dependent pieces and the two destination screens are faked.

class _FakeAdapter implements HttpClientAdapter {
  final replies = <Object>[]; // ResponseBody or an error to throw, consumed in order
  int calls = 0;
  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) async {
    calls++;
    final next = replies.isEmpty ? _json(200, _profile) : replies.removeAt(0);
    if (next is ResponseBody) return next;
    throw next;
  }

  @override
  void close({bool force = false}) {}
}

ResponseBody _json(int status, Object body, {Map<String, List<String>> headers = const {}}) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
        ...headers,
      },
    );

const _uid = 'test-uid-1';
const _profile = {'uid': _uid, 'display_name': 'Tester', 'email': 'tester@example.test', 'mfa_enabled': false};
const _ok = SessionStatus(emailVerified: true, mfaEnabled: false, mfaSessionValid: false);
Map<String, Object> _mfaBody() => {
      'detail': {'code': 'MFA_SESSION_REQUIRED', 'message': 'MFA verification required'}
    };

class SplashWorld {
  final adapter = _FakeAdapter();
  late final ApiClient client =
      ApiClient(baseUrl: 'https://api.example.test', httpClientAdapter: adapter, healthReporter: (_) {});
  String? uid = _uid;
  SessionProbe probe = const SessionProbe(status: _ok);
  bool emailResult = true;
  MfaChallengeResult mfaResult = const MfaChallengeResult(MfaChallengeOutcome.verified);
  bool repromptResult = true;
  int dashboards = 0, welcomes = 0, signOuts = 0, reprompts = 0, emailRuns = 0, mfaRuns = 0, probes = 0;

  SplashAuthDeps get deps => SplashAuthDeps(
        currentUid: () => uid,
        client: () => client,
        probeSessionStatus: () async {
          probes++;
          return probe;
        },
        verifyEmail: (_) async {
          emailRuns++;
          return emailResult;
        },
        verifyMfa: (_) async {
          mfaRuns++;
          return mfaResult;
        },
        signOut: () async => signOuts++,
        reprompt: () async {
          reprompts++;
          return repromptResult;
        },
        dashboard: () {
          dashboards++;
          return const Scaffold(body: Text('DASHBOARD'));
        },
        welcome: () {
          welcomes++;
          return const Scaffold(body: Text('WELCOME'));
        },
      );
}

late SplashWorld w;

Future<void> _settle(WidgetTester t) async {
  await t.pump(); // integrity check
  await t.pump(const Duration(seconds: 3)); // splash delay
  for (var i = 0; i < 25; i++) {
    await t.pump(const Duration(milliseconds: 100)); // fetch, gates, navigation and its transition
  }
}

Future<void> _runSplash(WidgetTester t) async {
  await t.pumpWidget(const ProviderScope(child: MaterialApp(home: SplashScreen())));
  await _settle(t);
}

Future<bool?> _loggedInFlag() async => (await SharedPreferences.getInstance()).getBool('isLoggedIn');

void _expectRetryGate(String title) {
  expect(find.byType(MfaRequiredGate), findsOneWidget);
  expect(find.text(title), findsOneWidget);
  expect(find.text('DASHBOARD'), findsNothing);
  expect(w.dashboards, 0);
}

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({'isLoggedIn': true});
    DeviceIntegrity.resetForTest();
    DeviceIntegrity.probe = () async => const IntegrityResult(IntegrityState.safe);
    ApiConfig.identityForTest = () async => null;
    MfaReprompt.handler = null;
    w = SplashWorld();
    SplashScreen.depsForTest = w.deps;
  });

  tearDown(() {
    SplashScreen.depsForTest = null;
    MfaReprompt.handler = null;
    ApiConfig.identityForTest = null;
    DeviceIntegrity.resetForTest();
  });

  Future<void> unmount(WidgetTester t) async {
    await t.pumpWidget(const SizedBox.shrink());
    await t.pump(const Duration(seconds: 1));
  }

  group('pure decisions', () {
    AppException ex(AppExceptionType type, int? status, [Object? body]) {
      final req = RequestOptions(path: '/user/x');
      return AppException(
        userMessage: 'x',
        statusCode: status,
        type: type,
        originalException: DioException(
          requestOptions: req,
          type: DioExceptionType.badResponse,
          response: Response(requestOptions: req, statusCode: status, data: body),
        ),
      );
    }

    test('classifyProfileFailure', () {
      expect(classifyProfileFailure(ex(AppExceptionType.notFound, 404)), SplashFailure.incompleteSignup);
      expect(classifyProfileFailure(ex(AppExceptionType.authentication, 401)), SplashFailure.signedOut);
      expect(classifyProfileFailure(ex(AppExceptionType.recentSignInRequired, 401)), SplashFailure.signedOut);
      expect(classifyProfileFailure(ex(AppExceptionType.mfaSessionRequired, 401)), SplashFailure.mfaRequired);
      expect(classifyProfileFailure(ex(AppExceptionType.authorization, 403, _mfaBody())), SplashFailure.mfaRepromptNeeded);
      expect(classifyProfileFailure(ex(AppExceptionType.authorization, 403, {'detail': 'Forbidden'})), SplashFailure.blocked);
      expect(classifyProfileFailure(ex(AppExceptionType.rateLimit, 429)), SplashFailure.rateLimited);
      for (final t in [AppExceptionType.timeout, AppExceptionType.network, AppExceptionType.server, AppExceptionType.unknown]) {
        expect(classifyProfileFailure(ex(t, null)), SplashFailure.retry, reason: '$t');
      }
      expect(classifyProfileFailure(const FormatException('bad')), SplashFailure.retry);
      expect(classifyProfileFailure(StateError('x')), SplashFailure.retry);
    });

    test('validatedProfile accepts only a 200 object for this account', () {
      expect(validatedProfile(200, {'uid': _uid}, _uid)['uid'], _uid);
      expect(validatedProfile(200, {'display_name': 'no id field'}, _uid), isA<Map<String, dynamic>>());
      expect(() => validatedProfile(204, {'uid': _uid}, _uid), throwsFormatException);
      expect(() => validatedProfile(200, ['not', 'a', 'map'], _uid), throwsFormatException);
      expect(() => validatedProfile(200, 'text', _uid), throwsFormatException);
      expect(() => validatedProfile(200, {'uid': 'someone-else'}, _uid), throwsFormatException);
      expect(() => validatedProfile(200, {'id': 'someone-else'}, _uid), throwsFormatException);
    });
  });

  group('splash navigation', () {
    testWidgets('1. HTTP 200 + all gates pass -> Dashboard exactly once', (t) async {
      await _runSplash(t);
      expect(find.text('DASHBOARD'), findsOneWidget);
      expect(w.dashboards, 1);
      expect(w.probes, 1);
      expect(w.adapter.calls, 1);
      await unmount(t);
    });

    testWidgets('2. HTTP 401 -> local sign-out, flag cleared, Welcome', (t) async {
      w.adapter.replies.add(_json(401, {'detail': 'Invalid Firebase Auth token'}));
      await _runSplash(t);
      expect(find.text('WELCOME'), findsOneWidget);
      expect(w.signOuts, 1);
      expect(await _loggedInFlag(), isFalse);
      expect(w.dashboards, 0);
      await unmount(t);
    });

    testWidgets('3a. 401 MFA_SESSION_REQUIRED -> ApiClient re-prompt succeeds -> gates -> Dashboard', (t) async {
      var handlerCalls = 0;
      MfaReprompt.handler = () async {
        handlerCalls++;
        return true;
      };
      w.adapter.replies.add(_json(401, _mfaBody()));
      await _runSplash(t);
      expect(handlerCalls, 1);
      expect(w.adapter.calls, 2);
      expect(w.probes, 1, reason: 'continues through the normal gates');
      expect(w.dashboards, 1);
      await unmount(t);
    });

    testWidgets('3b. 403 MFA_SESSION_REQUIRED -> splash re-prompts once -> verified -> Dashboard', (t) async {
      w.adapter.replies.add(_json(403, _mfaBody()));
      await _runSplash(t);
      expect(w.reprompts, 1);
      expect(w.adapter.calls, 2);
      expect(w.dashboards, 1);
      await unmount(t);
    });

    testWidgets('4a. 401 MFA re-prompt cancelled/failed -> blocked MFA gate, sign-in kept', (t) async {
      MfaReprompt.handler = () async => false;
      w.adapter.replies.add(_json(401, _mfaBody()));
      await _runSplash(t);
      _expectRetryGate('Verification needed');
      expect(w.signOuts, 0);
      await unmount(t);
    });

    testWidgets('4b. 403 MFA re-prompt fails -> MFA gate; server still refusing after success -> no loop', (t) async {
      w.repromptResult = false;
      w.adapter.replies.add(_json(403, _mfaBody()));
      await _runSplash(t);
      _expectRetryGate('Verification needed');
      expect(w.adapter.calls, 1);
      await unmount(t);

      w = SplashWorld();
      SplashScreen.depsForTest = w.deps;
      w.adapter.replies.addAll([_json(403, _mfaBody()), _json(403, _mfaBody())]);
      await _runSplash(t);
      _expectRetryGate('Verification needed');
      expect(w.reprompts, 1, reason: 'one re-prompt only');
      expect(w.adapter.calls, 2);
      await unmount(t);
    });

    testWidgets('4c. other 403 -> blocked gate, NOT signed out', (t) async {
      w.adapter.replies.add(_json(403, {'detail': 'You can only view your own account.'}));
      await _runSplash(t);
      _expectRetryGate("Can't continue");
      expect(w.signOuts, 0);
      await unmount(t);
    });

    testWidgets('5. timeout -> retry gate, no Dashboard, sign-in kept', (t) async {
      w.adapter.replies.add(DioException(requestOptions: RequestOptions(path: '/'), type: DioExceptionType.receiveTimeout));
      await _runSplash(t);
      _expectRetryGate("Can't reach EpicVerse");
      expect(w.signOuts, 0);
      expect(await _loggedInFlag(), isTrue);
      await unmount(t);
    });

    testWidgets('6. network error -> retry gate', (t) async {
      w.adapter.replies.add(const SocketException('connection refused'));
      await _runSplash(t);
      _expectRetryGate("Can't reach EpicVerse");
      expect(w.signOuts, 0);
      await unmount(t);
    });

    testWidgets('7. HTTP 503 -> retry gate', (t) async {
      w.adapter.replies.add(_json(503, {'detail': 'down'}));
      await _runSplash(t);
      _expectRetryGate("Can't reach EpicVerse");
      await unmount(t);
    });

    testWidgets('8. HTTP 429 -> gate with the server cool-down (no immediate retry)', (t) async {
      w.adapter.replies.add(_json(429, {'detail': 'slow down'}, headers: {
        'retry-after': ['30']
      }));
      await _runSplash(t);
      _expectRetryGate('Please wait');
      expect(find.textContaining('Retry in'), findsOneWidget);
      expect(w.signOuts, 0);
      await unmount(t);
    });

    testWidgets('9. malformed 200 (not an object / other account / bad field types) -> retry gate', (t) async {
      for (final body in <Object>[
        ['not', 'a', 'map'],
        {'uid': 'someone-else'},
        {'uid': _uid, 'preferred_languages': 5},
      ]) {
        w = SplashWorld();
        SplashScreen.depsForTest = w.deps;
        w.adapter.replies.add(_json(200, body));
        await _runSplash(t);
        _expectRetryGate("Can't reach EpicVerse");
        expect(w.probes, 0, reason: 'no gate runs on unusable data');
        await unmount(t);
      }
    });

    testWidgets('10. no Firebase user + stale login flag -> flag cleared, Welcome, no request', (t) async {
      w.uid = null;
      await _runSplash(t);
      expect(find.text('WELCOME'), findsOneWidget);
      expect(await _loggedInFlag(), isFalse);
      expect(w.adapter.calls, 0);
      expect(w.dashboards, 0);
      await unmount(t);
    });

    testWidgets('11. Retry re-runs the splash check once and navigates once', (t) async {
      w.adapter.replies.add(_json(503, {'detail': 'down'})); // then the default 200 profile
      await _runSplash(t);
      _expectRetryGate("Can't reach EpicVerse");
      await t.tap(find.text('Retry'));
      await _settle(t);
      expect(find.text('DASHBOARD'), findsOneWidget);
      expect(w.adapter.calls, 2, reason: 'exactly one new check');
      expect(w.dashboards, 1, reason: 'exactly one navigation');
      await unmount(t);
    });

    testWidgets('404 (incomplete signup) -> Welcome as before, not signed out', (t) async {
      w.adapter.replies.add(_json(404, {'detail': 'User not found'}));
      await _runSplash(t);
      expect(find.text('WELCOME'), findsOneWidget);
      expect(await _loggedInFlag(), isFalse);
      expect(w.signOuts, 0);
      await unmount(t);
    });
  });

  group('12. existing email and MFA gates unchanged', () {
    testWidgets('unverified email: verified -> Dashboard; not verified -> sign-out + Welcome', (t) async {
      w.probe = const SessionProbe(status: SessionStatus(emailVerified: false, mfaEnabled: false, mfaSessionValid: false));
      await _runSplash(t);
      expect(w.emailRuns, 1);
      expect(w.dashboards, 1);
      await unmount(t);

      w = SplashWorld()
        ..probe = const SessionProbe(status: SessionStatus(emailVerified: false, mfaEnabled: false, mfaSessionValid: false))
        ..emailResult = false;
      SplashScreen.depsForTest = w.deps;
      await _runSplash(t);
      expect(find.text('WELCOME'), findsOneWidget);
      expect(w.signOuts, 1);
      expect(w.dashboards, 0);
      await unmount(t);
    });

    testWidgets('server MFA challenge: verified -> Dashboard; failed/cancelled -> Welcome; temporary -> gate', (t) async {
      const needsMfa = SessionProbe(status: SessionStatus(emailVerified: true, mfaEnabled: true, mfaSessionValid: false));
      final cases = <MfaChallengeOutcome, String>{
        MfaChallengeOutcome.verified: 'DASHBOARD',
        MfaChallengeOutcome.failed: 'WELCOME',
        MfaChallengeOutcome.cancelled: 'WELCOME',
        MfaChallengeOutcome.unavailable: 'GATE',
      };
      for (final entry in cases.entries) {
        w = SplashWorld()
          ..probe = needsMfa
          ..mfaResult = MfaChallengeResult(entry.key);
        SplashScreen.depsForTest = w.deps;
        await _runSplash(t);
        expect(w.mfaRuns, 1, reason: '${entry.key}');
        if (entry.value == 'GATE') {
          _expectRetryGate('Verification needed');
        } else {
          expect(find.text(entry.value), findsOneWidget, reason: '${entry.key}');
          expect(w.dashboards, entry.value == 'DASHBOARD' ? 1 : 0, reason: '${entry.key}');
        }
        await unmount(t);
      }
    });

    testWidgets('session-status unavailable: MFA profile -> gate; non-MFA profile -> Dashboard (as before)', (t) async {
      w
        ..probe = const SessionProbe(unavailable: true)
        ..adapter.replies.add(_json(200, {..._profile, 'mfa_enabled': true}));
      await _runSplash(t);
      _expectRetryGate('Verification needed');
      await unmount(t);

      w = SplashWorld()..probe = const SessionProbe(unavailable: true);
      SplashScreen.depsForTest = w.deps;
      await _runSplash(t);
      expect(w.dashboards, 1);
      await unmount(t);
    });
  });
}
