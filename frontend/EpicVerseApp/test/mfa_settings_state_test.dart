import 'dart:io';

import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  group('MFA Settings capability', () {
    test('a missing session-status route (null) is a legacy backend; any status is modern', () {
      expect(MfaSettingsState.capabilityFor(null), MfaSettingsCapability.legacy);
      expect(
          MfaSettingsState.capabilityFor(
              const SessionStatus(emailVerified: true, mfaEnabled: false, mfaSessionValid: false)),
          MfaSettingsCapability.modern);
    });

    test('the toggle is enabled only for a confirmed modern backend', () {
      for (final c in MfaSettingsCapability.values) {
        expect(MfaSettingsState(c).toggleEnabled, c == MfaSettingsCapability.modern, reason: '$c');
      }
    });

    test('legacy shows the required message; unknown/unavailable also stay disabled', () {
      expect(const MfaSettingsState(MfaSettingsCapability.legacy).message, 'Available after our next server update.');
      expect(const MfaSettingsState(MfaSettingsCapability.unknown).message, isNotNull);
      expect(const MfaSettingsState(MfaSettingsCapability.unavailable).message, isNotNull);
      expect(const MfaSettingsState(MfaSettingsCapability.modern).message, isNull);
    });

    test('retry is offered only after a failed check', () {
      for (final c in MfaSettingsCapability.values) {
        expect(MfaSettingsState(c).canRetry, c == MfaSettingsCapability.unavailable, reason: '$c');
      }
    });
  });

  group('rejection of inappropriate legacy fallbacks (static guard)', () {
    final lib = Directory('lib');
    final dartFiles = lib.listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.dart'));

    test('no app code calls the legacy /user/update-mfa endpoint', () {
      final offenders = <String>[];
      for (final f in dartFiles) {
        if (f.uri.path.endsWith('core/services/logger_service.dart')) continue; // log redaction rule only
        final code = f.readAsLinesSync().where((l) => !l.trimLeft().startsWith('//')).join(' '); // comments may name it
        if (code.contains('update-mfa')) offenders.add(f.path);
      }
      expect(offenders, isEmpty);
    });

    test('test-only hooks are never set by app code', () {
      final offenders = <String>[];
      // A call is a violation unless it is the definition itself (preceded by `void `); an assignment likewise.
      final calls = RegExp(r'(?<!void )(setMfaStatusTransportForTest|setEmailVerificationTransportForTest|useStoreForTest|simulateRestartForTest|useChallengeForTest|useForTest)\(');
      final assignment = RegExp(r'identityForTest\s*=(?!=)');
      for (final f in dartFiles) {
        final code = f.readAsLinesSync().where((l) => !l.trimLeft().startsWith('//')).join(' ');
        if (calls.hasMatch(code) || assignment.hasMatch(code)) offenders.add(f.path);
      }
      expect(offenders, isEmpty);
    });

    test('no app code puts an ID token in a URL', () {
      final offenders = <String>[];
      final re = RegExp(r'[?&]token=');
      for (final f in dartFiles) {
        if (re.hasMatch(f.readAsStringSync())) offenders.add(f.path);
      }
      expect(offenders, isEmpty);
    });
  });
}
