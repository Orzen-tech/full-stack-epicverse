import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/core/security/session_gate.dart';
import 'package:flutter_test/flutter_test.dart';

// null status = the backend predates F-09 (no /auth/session-status route); the profile then carries the legacy fields.
SessionStatus _s({bool verified = true, bool mfa = false, bool session = false}) =>
    SessionStatus(emailVerified: verified, mfaEnabled: mfa, mfaSessionValid: session);

void main() {
  group('SessionGate.emailVerified', () {
    test('modern backend: the server status wins over the profile', () {
      expect(SessionGate.emailVerified(_s(verified: true), {'email_verified': false}), isTrue);
      expect(SessionGate.emailVerified(_s(verified: false), {'email_verified': true}), isFalse);
    });
    test('legacy backend (null status): the profile field decides; missing means unverified', () {
      expect(SessionGate.emailVerified(null, {'email_verified': true}), isTrue);
      expect(SessionGate.emailVerified(null, {'email_verified': false}), isFalse);
      expect(SessionGate.emailVerified(null, {}), isFalse);
      expect(SessionGate.emailVerified(null, {'email_verified': 'true'}), isFalse); // never trusts a non-bool
    });
  });

  group('MFA login decisions', () {
    test('modern MFA login: a server challenge is needed only when MFA is on and no session is valid', () {
      expect(SessionGate.needsServerMfaChallenge(_s(mfa: true, session: false)), isTrue);
      expect(SessionGate.needsServerMfaChallenge(_s(mfa: true, session: true)), isFalse);
      expect(SessionGate.needsServerMfaChallenge(_s(mfa: false)), isFalse);
    });
    test('legacy MFA login: the old client-side prompt only on a legacy backend with MFA on', () {
      expect(SessionGate.needsLegacyClientMfa(null, {'mfa_enabled': true}), isTrue);
      expect(SessionGate.needsLegacyClientMfa(null, {'mfa_enabled': false}), isFalse);
      expect(SessionGate.needsLegacyClientMfa(null, {}), isFalse);
      expect(SessionGate.needsLegacyClientMfa(null, {'mfa_enabled': null}), isFalse);
    });
    test('malformed MFA values fail closed (the prompt is kept), never skipped', () {
      for (final bad in ['true', 'false', 'yes', 1, 0, [], {}]) {
        expect(SessionGate.needsLegacyClientMfa(null, {'mfa_enabled': bad}), isTrue, reason: '$bad');
      }
    });
    test('malformed verification values fail closed (treated as unverified)', () {
      for (final bad in ['true', 1, 'yes', [], {}, null]) {
        expect(SessionGate.emailVerified(null, {'email_verified': bad}), isFalse, reason: '$bad');
      }
    });
    test('the two paths are mutually exclusive: a modern backend never uses the legacy prompt', () {
      for (final mfa in [true, false]) {
        for (final session in [true, false]) {
          final status = _s(mfa: mfa, session: session);
          expect(SessionGate.needsLegacyClientMfa(status, {'mfa_enabled': true}), isFalse);
        }
      }
      expect(SessionGate.needsServerMfaChallenge(null), isFalse);
    });
  });
}
