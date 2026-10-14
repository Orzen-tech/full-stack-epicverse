import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/core/security/session_gate.dart';
import 'package:epicverse/presentation/screens/mfa_required_gate.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

// Temporary failures (network, 429, 5xx) must never be mistaken for "the backend predates F-09" (which disables the
// MFA toggle and drops any stored session) and must never fail open for an MFA-enabled profile.

Response<dynamic> _ok(dynamic body) =>
    Response(requestOptions: RequestOptions(path: '/auth/session-status'), statusCode: 200, data: body);

AppException _ex(AppExceptionType type, int? status, {dynamic body, Map<String, List<String>>? headers}) {
  final req = RequestOptions(path: '/auth/session-status');
  return AppException(
    userMessage: 'x',
    statusCode: status,
    type: type,
    originalException: DioException(
      requestOptions: req,
      type: status == null ? DioExceptionType.connectionError : DioExceptionType.badResponse,
      response: status == null
          ? null
          : Response(requestOptions: req, statusCode: status, data: body, headers: Headers.fromMap(headers ?? {})),
    ),
  );
}

void main() {
  tearDown(() => setMfaStatusTransportForTest(null));

  group('probeSessionStatus separates legacy / modern / temporarily unavailable', () {
    test('missing route (genuine 404 Not Found) is a LEGACY backend', () async {
      setMfaStatusTransportForTest((p) async => throw _ex(AppExceptionType.notFound, 404, body: {'detail': 'Not Found'}));
      final r = await MfaFlow.probeSessionStatus();
      expect([r.legacy, r.unavailable, r.status], [true, false, null]);
    });

    test('a 404 that means something else is NOT a legacy backend', () async {
      setMfaStatusTransportForTest((p) async => throw _ex(AppExceptionType.notFound, 404, body: {'detail': 'User not found'}));
      final r = await MfaFlow.probeSessionStatus();
      expect([r.legacy, r.unavailable], [false, true]);
    });

    test('no network / timeout / 5xx are unavailable, never legacy', () async {
      for (final e in [
        _ex(AppExceptionType.network, null),
        _ex(AppExceptionType.timeout, null),
        _ex(AppExceptionType.server, 503, body: {'detail': 'x'}),
        _ex(AppExceptionType.server, 500),
      ]) {
        setMfaStatusTransportForTest((p) async => throw e);
        final r = await MfaFlow.probeSessionStatus();
        expect([r.legacy, r.unavailable, r.status], [false, true, null], reason: '${e.type}');
      }
    });

    test('a 429 is unavailable and carries Retry-After', () async {
      setMfaStatusTransportForTest((p) async => throw _ex(AppExceptionType.rateLimit, 429, headers: {'retry-after': ['120']}));
      final r = await MfaFlow.probeSessionStatus();
      expect([r.unavailable, r.retryAfterSeconds], [true, 120]);
    });

    test('an unreadable body is unavailable (fail closed), not legacy', () async {
      setMfaStatusTransportForTest((p) async => _ok([1, 2, 3]));
      final r = await MfaFlow.probeSessionStatus();
      expect([r.legacy, r.unavailable], [false, true]);
    });

    test('a modern answer is passed through exactly', () async {
      setMfaStatusTransportForTest((p) async => _ok({'email_verified': true, 'mfa_enabled': true, 'mfa_session_valid': false}));
      final r = await MfaFlow.probeSessionStatus();
      expect([r.legacy, r.unavailable, r.status!.mfaEnabled, r.status!.mfaSessionValid], [false, false, true, false]);
    });
  });

  group('challenge failure classification', () {
    test('only rate limit and connectivity/server problems are temporary', () {
      expect(MfaFlow.failureFor(_ex(AppExceptionType.rateLimit, 429)), MfaChallengeOutcome.rateLimited);
      for (final t in [AppExceptionType.network, AppExceptionType.timeout, AppExceptionType.server]) {
        expect(MfaFlow.failureFor(_ex(t, t == AppExceptionType.server ? 503 : null)), MfaChallengeOutcome.unavailable, reason: '$t');
      }
      for (final t in [AppExceptionType.validation, AppExceptionType.authorization, AppExceptionType.notFound]) {
        expect(MfaFlow.failureFor(_ex(t, 400)), MfaChallengeOutcome.failed, reason: '$t');
      }
    });

    test('isTemporary drives "keep the Firebase sign-in"', () {
      expect(const MfaChallengeResult(MfaChallengeOutcome.rateLimited).isTemporary, isTrue);
      expect(const MfaChallengeResult(MfaChallengeOutcome.unavailable).isTemporary, isTrue);
      for (final o in [MfaChallengeOutcome.verified, MfaChallengeOutcome.cancelled, MfaChallengeOutcome.failed]) {
        expect(MfaChallengeResult(o).isTemporary, isFalse, reason: '$o');
      }
    });

    test('Retry-After is read only when it is a number', () {
      expect(MfaFlow.retryAfterOf(_ex(AppExceptionType.rateLimit, 429, headers: {'retry-after': ['45']})), 45);
      expect(MfaFlow.retryAfterOf(_ex(AppExceptionType.rateLimit, 429, headers: {'retry-after': ['soon']})), isNull);
      expect(MfaFlow.retryAfterOf(_ex(AppExceptionType.rateLimit, 429)), isNull);
    });
  });

  group('MFA gate when the status cannot be read (never fail open for an MFA profile)', () {
    test('only a missing value or an explicit false lets the user through', () {
      expect(SessionGate.needsGateWhenStatusUnavailable({}), isFalse);
      expect(SessionGate.needsGateWhenStatusUnavailable({'mfa_enabled': null}), isFalse);
      expect(SessionGate.needsGateWhenStatusUnavailable({'mfa_enabled': false}), isFalse);
      for (final v in [true, 'true', 'false', 1, 0, [], {}]) {
        expect(SessionGate.needsGateWhenStatusUnavailable({'mfa_enabled': v}), isTrue, reason: '$v');
      }
    });
  });

  group('MfaRequiredGate screen', () {
    test('cooldown and messages', () {
      expect(MfaRequiredGate.cooldownFor(false, 300), 0);
      expect(MfaRequiredGate.cooldownFor(true, null), 60);
      expect(MfaRequiredGate.cooldownFor(true, 1), 5);
      expect(MfaRequiredGate.cooldownFor(true, 99999), 600);
      expect(MfaRequiredGate.messageFor(true, 120), contains('2 minutes'));
      expect(MfaRequiredGate.messageFor(true, 30), contains('1 minute'));
      expect(MfaRequiredGate.messageFor(false, 0), contains('connection'));
    });

    testWidgets('network problem: Retry is available at once and re-runs the checks', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: MfaRequiredGate(rateLimited: false, retryBuilder: () => const Scaffold(body: Text('RERAN'))),
      ));
      expect(find.text('Verification needed'), findsOneWidget);
      expect(find.text('Sign out'), findsOneWidget);
      final retry = tester.widget<ElevatedButton>(find.byType(ElevatedButton));
      expect(retry.onPressed, isNotNull);
      await tester.tap(find.text('Retry'));
      await tester.pumpAndSettle();
      expect(find.text('RERAN'), findsOneWidget);
    });

    testWidgets('429: Retry is disabled during the wait, shows a countdown, then enables', (tester) async {
      await tester.pumpWidget(MaterialApp(
        home: MfaRequiredGate(rateLimited: true, retryAfterSeconds: 6, retryBuilder: () => const SizedBox()),
      ));
      expect(tester.widget<ElevatedButton>(find.byType(ElevatedButton)).onPressed, isNull);
      expect(find.textContaining('Too many code requests'), findsOneWidget);
      expect(find.textContaining('Retry in'), findsOneWidget);
      await tester.pump(const Duration(seconds: 7));
      expect(tester.widget<ElevatedButton>(find.byType(ElevatedButton)).onPressed, isNotNull);
      expect(find.text('Retry'), findsOneWidget);
    });
  });
}
