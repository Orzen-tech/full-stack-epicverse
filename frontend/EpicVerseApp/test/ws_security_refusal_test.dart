import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/errors/error_mapper.dart';
import 'package:epicverse/core/network/websocket_service.dart';

DioException _http(int status, Map<String, dynamic> body) {
  final req = RequestOptions(path: '/feedback');
  return DioException(
    requestOptions: req,
    type: DioExceptionType.badResponse,
    response: Response(requestOptions: req, statusCode: status, data: body),
  );
}

void main() {
  group('wsSecurityRefusalCode', () {
    test('recognises the two F-09 refusal codes', () {
      expect(wsSecurityRefusalCode('{"type":"error","code":"MFA_SESSION_REQUIRED","message":"Unauthorized"}'),
          'MFA_SESSION_REQUIRED');
      expect(wsSecurityRefusalCode('{"type":"error","code":"EMAIL_VERIFICATION_REQUIRED","message":"Unauthorized"}'),
          'EMAIL_VERIFICATION_REQUIRED');
    });

    test('ignores ordinary messages, other errors and garbage', () {
      expect(wsSecurityRefusalCode('{"type":"error","message":"Unauthorized"}'), isNull);
      expect(wsSecurityRefusalCode('{"type":"error","code":"SOMETHING_ELSE"}'), isNull);
      expect(wsSecurityRefusalCode('{"type":"connection_success","code":"MFA_SESSION_REQUIRED"}'), isNull);
      expect(wsSecurityRefusalCode('{"type":"SESSION_KICKED"}'), isNull);
      expect(wsSecurityRefusalCode('not json'), isNull);
      expect(wsSecurityRefusalCode('[1,2]'), isNull);
    });
  });

  group('HTTP mapping', () {
    test('403 EMAIL_VERIFICATION_REQUIRED gets a verify-email message', () {
      final e = ErrorMapper.fromException(_http(403,
          {'detail': {'code': 'EMAIL_VERIFICATION_REQUIRED', 'message': 'Email verification required'}}));
      expect(e.userMessage, 'Please verify your email to continue.');
      expect(e.type, AppExceptionType.authorization);
    });

    test('plain 403 is unchanged', () {
      final e = ErrorMapper.fromException(_http(403, {'detail': 'Forbidden'}));
      expect(e.userMessage, "You don't have permission to perform this action.");
    });
  });
}
