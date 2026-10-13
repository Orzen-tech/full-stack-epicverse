import 'package:epicverse/core/network/websocket_service.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('AI Companion WebSocket handshake', () {
    test('the ID token is a bearer header and never part of the URL', () {
      final q = buildWsQuery(uid: 'u1', mode: 'Mode 1', sessionId: 's1', listening: false, language: 'English');
      expect(q, 'uid=u1&mode=Mode%201&session_id=s1&listening=false&language=English');
      expect(q.contains('token'), isFalse);
      final h = buildWsHandshakeHeaders(idToken: 'abc', mfaSession: null);
      expect(h['Authorization'], 'Bearer abc');
    });

    test('X-MFA-Session is sent only when an MFA session exists', () {
      expect(buildWsHandshakeHeaders(idToken: 'abc', mfaSession: null).containsKey('X-MFA-Session'), isFalse);
      expect(buildWsHandshakeHeaders(idToken: 'abc', mfaSession: 'sess').containsKey('X-MFA-Session'), isTrue);
      expect(buildWsHandshakeHeaders(idToken: 'abc', mfaSession: 'sess')['X-MFA-Session'], 'sess');
    });

    test('values are URL-encoded', () {
      final q = buildWsQuery(uid: 'u', mode: 'a&b=c', sessionId: 's', listening: true, language: 'Hindi हिन्दी');
      expect(q.contains('a&b=c'), isFalse);
      expect(q.contains('mode=a%26b%3Dc'), isTrue);
    });

    test('backend refusals are recognised', () {
      expect(wsSecurityRefusalCode('{"type":"error","code":"MFA_SESSION_REQUIRED","message":"Unauthorized"}'), 'MFA_SESSION_REQUIRED');
      expect(wsSecurityRefusalCode('{"type":"error","code":"EMAIL_VERIFICATION_REQUIRED"}'), 'EMAIL_VERIFICATION_REQUIRED');
    });
  });
}
