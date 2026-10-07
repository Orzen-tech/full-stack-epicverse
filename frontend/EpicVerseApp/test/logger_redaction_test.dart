import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:epicverse/core/services/logger_service.dart';

void main() {
  group('LoggerService.redact', () {
    test('masks every sensitive key, case-insensitively and when nested', () {
      final out = LoggerService.redact({
        'otp': '654321',
        'proof': 'evp1_AAA',
        'email_verification_proof': 'evp1_BBB',
        'challenge_id': 'c-123',
        'mfa_session_token': 'sess-xyz',
        'identifier': 'a@example.com',
        'EMAIL': 'a@example.com',
        'Password': 'hunter2',
        'display_name': 'Visible Name',
        'nested': {'otp': '111111', 'keep': 'ok'},
        'list': [
          {'proof': 'evp1_CCC', 'keep': 1}
        ],
      }) as Map;
      for (final key in ['otp', 'proof', 'email_verification_proof', 'challenge_id',
          'mfa_session_token', 'identifier', 'EMAIL', 'Password']) {
        expect(out[key], LoggerService.redactedMarker, reason: key);
      }
      expect(out['display_name'], 'Visible Name');
      expect((out['nested'] as Map)['otp'], LoggerService.redactedMarker);
      expect((out['nested'] as Map)['keep'], 'ok');
      expect(((out['list'] as List).single as Map)['proof'], LoggerService.redactedMarker);
      expect(((out['list'] as List).single as Map)['keep'], 1);
    });

    test('FormData is reduced to field names; values never appear', () {
      final form = FormData.fromMap({'identifier': 'a@example.com', 'otp': '654321', 'proof': 'evp1_AAA'});
      final out = LoggerService.redact(form).toString();
      expect(out, contains('identifier'));
      expect(out, contains('otp'));
      for (final secret in ['a@example.com', '654321', 'evp1_AAA']) {
        expect(out.contains(secret), isFalse, reason: secret);
      }
    });

    test('scrub removes anything shaped like a proof from free text', () {
      final out = LoggerService.scrub('failed with evp1_AbC-123_xyz and again evp1_QQ');
      expect(out.contains('evp1_'), isFalse);
      expect(out, contains(LoggerService.redactedMarker));
      expect(LoggerService.redact('x evp1_ZZZ y'), 'x ${LoggerService.redactedMarker} y');
    });

    test('non-sensitive scalars and null pass through', () {
      expect(LoggerService.redact(null), isNull);
      expect(LoggerService.redact(42), 42);
      expect(LoggerService.redact('plain'), 'plain');
    });
  });

  group('LoggerService.logError output', () {
    late DebugPrintCallback original;
    late List<String> printed;

    setUp(() {
      original = debugPrint;
      printed = [];
      debugPrint = (String? message, {int? wrapWidth}) => printed.add(message ?? '');
    });
    tearDown(() => debugPrint = original);

    test('never prints OTPs, proofs, challenge ids, tokens or emails from a failed request', () {
      LoggerService.logError(
        Exception('request failed for evp1_SECRETPROOF123'),
        endpoint: '/auth/mark-verified',
        method: 'POST',
        statusCode: 403,
        requestData: FormData.fromMap({'proof': 'evp1_SECRETPROOF123', 'otp': '654321'}),
        responseData: {
          'detail': {'code': 'EMAIL_PROOF_INVALID'},
          'mfa_session_token': 'sess-SECRET',
          'email_verification_proof': 'evp1_SECRETPROOF123',
          'identifier': 'victim@example.com',
        },
        customReason: 'context evp1_SECRETPROOF123',
        screenName: 'CreateProfileScreen',
      );
      final text = printed.join('\n');
      expect(text, contains('/auth/mark-verified')); // still useful for debugging
      expect(text, contains('EMAIL_PROOF_INVALID'));
      for (final secret in ['evp1_SECRETPROOF123', '654321', 'sess-SECRET', 'victim@example.com']) {
        expect(text.contains(secret), isFalse, reason: secret);
      }
    });
  });
}
