import 'dart:io';

import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:flutter_test/flutter_test.dart';

// EV-11: a real logout asks the server to revoke the account's Firebase tokens; cleanup and forced sign-outs must not (revocation is
// account-wide and would sign out the device that just replaced this one).

String _code(String path) => File(path).readAsLinesSync().where((l) => !l.trim().startsWith('//')).join('\n');

void main() {
  group('logoutRequestFor', () {
    test('a real logout always calls the server and asks for token revocation (MFA or not)', () {
      for (final hasMfa in [true, false]) {
        final r = MfaFlow.logoutRequestFor(revokeServer: true, hasMfaSession: hasMfa);
        expect([r.call, r.query], [true, {'revoke_tokens': 'true'}], reason: 'hasMfaSession=$hasMfa');
      }
    });

    test('revokeServer:false never asks for token revocation; it still revokes an existing MFA session as before', () {
      final withMfa = MfaFlow.logoutRequestFor(revokeServer: false, hasMfaSession: true);
      expect([withMfa.call, withMfa.query], [true, null]);
      final noMfa = MfaFlow.logoutRequestFor(revokeServer: false, hasMfaSession: false);
      expect(noMfa.call, isFalse);
    });
  });

  group('call sites', () {
    test('the kicked-session sign-out does not revoke server-side', () {
      expect(_code('lib/main.dart'), contains('await MfaFlow.signOut(revokeServer: false);'));
      expect(_code('lib/main.dart'), isNot(contains('await MfaFlow.signOut();')));
    });

    test('failed-login cleanup in login and splash does not revoke server-side', () {
      // The login screen calls the local-only sign-out directly, twice.
      final login = _code('lib/presentation/screens/login_screen.dart');
      expect('MfaFlow.signOut(revokeServer: false)'.allMatches(login).length, 2);
      expect(login, isNot(contains('MfaFlow.signOut()')));

      // The splash never calls MfaFlow itself: every sign-out goes through SplashAuthDeps.signOut ...
      final splash = _code('lib/presentation/screens/splash_screen.dart');
      expect(splash, isNot(contains('MfaFlow.signOut(')));
      expect(splash, contains('deps.signOut()'));

      // ... and the production wiring of that dependency is the local-only form, and the only sign-out there.
      final deps = _code('lib/core/security/splash_auth.dart');
      expect('MfaFlow.signOut('.allMatches(deps).length, 1);
      expect(deps, contains('signOut: () => MfaFlow.signOut(revokeServer: false)'));
    });

    test('deliberate sign-outs keep the default (revoking) behaviour', () {
      for (final f in [
        'lib/presentation/screens/settings_screen.dart',
        'lib/presentation/screens/mfa_required_gate.dart',
        'lib/presentation/screens/verification_pending_screen.dart',
      ]) {
        expect(_code(f), isNot(contains('revokeServer: false')), reason: f);
      }
    });
  });
}
