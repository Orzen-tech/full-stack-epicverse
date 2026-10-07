import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/security/email_verification_flow.dart';

AppException _http(int status, AppExceptionType type, {dynamic body}) {
  final req = RequestOptions(path: '/x');
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

Response<dynamic> _ok(dynamic data) =>
    Response(requestOptions: RequestOptions(path: '/x'), statusCode: 200, data: data);

class _Call {
  final String path;
  final dynamic data;
  _Call(this.path, this.data);
}

void main() {
  late List<_Call> calls;

  void stub(Future<Response<dynamic>> Function(String path, dynamic data) handler) {
    calls = [];
    setEmailVerificationTransportForTest((path, {data}) {
      calls.add(_Call(path, data));
      return handler(path, data);
    });
  }

  tearDown(() => setEmailVerificationTransportForTest(null));

  Map<String, String> fieldsOf(dynamic data) =>
      {for (final f in (data as FormData).fields) f.key: f.value};

  group('EmailOtp.parseProof', () {
    test('accepts only a string shaped like a one-time proof', () {
      expect(EmailOtp.parseProof({'email_verification_proof': 'evp1_abcDEF-123_x'}), 'evp1_abcDEF-123_x');
      expect(EmailOtp.parseProof({'email_verification_proof': 'abc'}), isNull);
      expect(EmailOtp.parseProof({'email_verification_proof': 12345}), isNull);
      expect(EmailOtp.parseProof({'status': 'success'}), isNull);
      expect(EmailOtp.parseProof('evp1_abc'), isNull);
      expect(EmailOtp.parseProof(null), isNull);
    });
  });

  group('EmailOtp.verify', () {
    test('returns the proof and posts the code as form fields (never in the URL)', () async {
      stub((p, d) async => _ok({'status': 'success', 'email_verification_proof': 'evp1_zzz'}));
      final r = await EmailOtp.verify('a@example.com', '123456');
      expect(r.error, isNull);
      expect(r.proof, 'evp1_zzz');
      expect(calls.single.path, '/auth/verify-otp');
      expect(calls.single.path.contains('?'), isFalse);
      expect(fieldsOf(calls.single.data), {'identifier': 'a@example.com', 'otp': '123456'});
    });

    test('a pre-H1 server returns no proof: success with a null proof', () async {
      stub((p, d) async => _ok({'status': 'success', 'message': 'OTP verified'}));
      final r = await EmailOtp.verify('a@example.com', '123456');
      expect(r.error, isNull);
      expect(r.proof, isNull);
    });

    test('maps rejections to user-facing messages and returns no proof', () async {
      stub((p, d) async => throw _http(400, AppExceptionType.validation));
      var r = await EmailOtp.verify('a@example.com', '000000');
      expect(r.error, 'Invalid or expired verification code. Please try again.');
      expect(r.proof, isNull);

      stub((p, d) async => throw _http(429, AppExceptionType.rateLimit));
      r = await EmailOtp.verify('a@example.com', '000000');
      expect(r.error, 'Too many failed attempts. Please request a new code.');

      stub((p, d) async => throw _http(503, AppExceptionType.server));
      r = await EmailOtp.verify('a@example.com', '000000');
      expect(r.error, 'Verification is temporarily unavailable. Please try again.');
    });
  });

  group('EmailVerificationFlow.isMissingRoute', () {
    test('true only for a genuine missing-route 404 (FastAPI "Not Found")', () {
      expect(EmailVerificationFlow.isMissingRoute(
          _http(404, AppExceptionType.notFound, body: {'detail': 'Not Found'})), isTrue);
    });

    test('false for a 404 that means something else', () {
      expect(EmailVerificationFlow.isMissingRoute(
          _http(404, AppExceptionType.notFound, body: {'detail': 'User not found'})), isFalse);
      expect(EmailVerificationFlow.isMissingRoute(
          _http(404, AppExceptionType.notFound, body: {'detail': {'code': 'X'}})), isFalse);
      expect(EmailVerificationFlow.isMissingRoute(
          _http(404, AppExceptionType.notFound, body: 'Not Found')), isFalse);
      expect(EmailVerificationFlow.isMissingRoute(_http(404, AppExceptionType.notFound)), isFalse);
    });

    test('false for every other failure', () {
      for (final e in [
        _http(401, AppExceptionType.authentication, body: {'detail': 'Not Found'}),
        _http(403, AppExceptionType.authorization, body: {'detail': 'Not Found'}),
        _http(500, AppExceptionType.server, body: {'detail': 'Not Found'}),
        const AppException(userMessage: 'x', type: AppExceptionType.network),
      ]) {
        expect(EmailVerificationFlow.isMissingRoute(e), isFalse);
      }
    });
  });

  group('EmailVerificationFlow.requestChallenge', () {
    test('sent: returns the challenge id and calls the authenticated route', () async {
      stub((p, d) async => _ok({'status': 'sent', 'challenge_id': 'c-1', 'expires_in': 300}));
      final r = await EmailVerificationFlow.requestChallenge();
      expect(r.outcome, EmailChallengeOutcome.sent);
      expect(r.challengeId, 'c-1');
      expect(calls.single.path, EmailVerificationFlow.requestPath);
      expect(calls.single.data, isNull); // nothing client-controlled is sent
    });

    test('already_verified is reported, not treated as a failure', () async {
      stub((p, d) async => _ok({'status': 'already_verified'}));
      expect((await EmailVerificationFlow.requestChallenge()).outcome, EmailChallengeOutcome.alreadyVerified);
    });

    test('a genuine missing route selects the legacy path', () async {
      stub((p, d) async => throw _http(404, AppExceptionType.notFound, body: {'detail': 'Not Found'}));
      expect((await EmailVerificationFlow.requestChallenge()).outcome, EmailChallengeOutcome.legacyServer);
    });

    test('"user not found" 404 and every other error never select the legacy path', () async {
      for (final e in [
        _http(404, AppExceptionType.notFound, body: {'detail': 'User not found'}),
        _http(409, AppExceptionType.conflict, body: {'detail': {'code': 'EMAIL_MISMATCH'}}),
        _http(429, AppExceptionType.rateLimit),
        _http(503, AppExceptionType.server),
        _http(401, AppExceptionType.authentication),
        const AppException(userMessage: 'x', type: AppExceptionType.timeout),
      ]) {
        stub((p, d) async => throw e);
        final r = await EmailVerificationFlow.requestChallenge();
        expect(r.outcome, EmailChallengeOutcome.failed, reason: '${e.statusCode} ${e.type}');
        expect(r.error, isNotNull);
      }
    });

    test('messages are specific for rate limiting and conflict', () async {
      stub((p, d) async => throw _http(429, AppExceptionType.rateLimit));
      expect((await EmailVerificationFlow.requestChallenge()).error,
          'Too many code requests. Please wait a few minutes and try again.');
      stub((p, d) async => throw _http(409, AppExceptionType.conflict));
      expect((await EmailVerificationFlow.requestChallenge()).error,
          "This account's email could not be confirmed. Please contact support.");
    });

    test('a 200 without a challenge id is a failure', () async {
      stub((p, d) async => _ok({'status': 'sent'}));
      expect((await EmailVerificationFlow.requestChallenge()).outcome, EmailChallengeOutcome.failed);
    });
  });

  group('EmailVerificationFlow.confirm', () {
    test('success returns null and posts only the challenge id and code, as form fields', () async {
      stub((p, d) async => _ok({'status': 'verified'}));
      expect(await EmailVerificationFlow.confirm('c-1', '123456'), isNull);
      expect(calls.single.path, EmailVerificationFlow.confirmPath);
      expect(calls.single.path.contains('?'), isFalse);
      expect(fieldsOf(calls.single.data), {'challenge_id': 'c-1', 'otp': '123456'});
    });

    test('rejections map to user-facing messages', () async {
      stub((p, d) async => throw _http(400, AppExceptionType.validation));
      expect(await EmailVerificationFlow.confirm('c-1', '000000'),
          'Invalid or expired code. Please try again or request a new code.');
      stub((p, d) async => throw _http(429, AppExceptionType.rateLimit));
      expect(await EmailVerificationFlow.confirm('c-1', '000000'),
          'Too many failed attempts. Please request a new code.');
      stub((p, d) async => throw const AppException(userMessage: 'x', type: AppExceptionType.network));
      expect(await EmailVerificationFlow.confirm('c-1', '000000'),
          'No internet connection. Please try again.');
      stub((p, d) async => throw _http(500, AppExceptionType.server));
      expect(await EmailVerificationFlow.confirm('c-1', '000000'), 'Verification failed. Please try again.');
    });
  });
}
