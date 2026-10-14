import 'package:flutter/foundation.dart' show kReleaseMode, visibleForTesting;
import 'package:flutter/material.dart';

import 'mfa_flow.dart';

enum MfaEnableResult { enabled, cancelled, failed, temporary, reauthExpired }

typedef MfaChallengeRunner = Future<MfaChallengeResult> Function(NavigatorState nav);

/// Enabling MFA from Settings.
///
/// Every attempt starts with an EXPLICIT Firebase password re-authentication, so the server's 15-minute
/// recent-sign-in rule cannot run out during the 5-minute code window. If the server still answers
/// RECENT_SIGN_IN_REQUIRED (for example after many resends), nothing was enabled: the user is told why, asked for the
/// password again, and one fresh attempt is made. Server checks and OTP limits are untouched.
class MfaEnableFlow {
  MfaEnableFlow._();

  static const maxAttempts = 2;

  static MfaChallengeRunner _challenge = (nav) => MfaFlow.runChallengeDetailed(
        nav,
        challengePath: MfaFlow.enableRequestPath,
        verifyPath: MfaFlow.enableConfirmPath,
      );

  /// Test seam, never set by app code (a test guards this) and ignored in release builds.
  @visibleForTesting
  static void useChallengeForTest(MfaChallengeRunner? runner) {
    if (kReleaseMode) return;
    _challenge = runner ??
        (nav) => MfaFlow.runChallengeDetailed(
              nav,
              challengePath: MfaFlow.enableRequestPath,
              verifyPath: MfaFlow.enableConfirmPath,
            );
  }

  /// [reauthenticate] must show the password prompt and re-authenticate; it returns false if the user backs out or the
  /// password is wrong (it reports that itself). [notify] shows a message to the user.
  static Future<MfaEnableResult> run({
    required NavigatorState nav,
    required Future<bool> Function() reauthenticate,
    required void Function(String message) notify,
  }) async {
    for (var attempt = 1; attempt <= maxAttempts; attempt++) {
      if (!await reauthenticate()) return MfaEnableResult.cancelled;
      final result = await _challenge(nav);
      switch (result.outcome) {
        case MfaChallengeOutcome.verified:
          return MfaEnableResult.enabled;
        case MfaChallengeOutcome.reauthRequired:
          if (attempt < maxAttempts) {
            notify('Your sign-in timed out. Please confirm your password again to continue.');
            continue;
          }
          notify('We could not finish turning on two-factor authentication because your sign-in expired. Please try again.');
          return MfaEnableResult.reauthExpired;
        case MfaChallengeOutcome.rateLimited:
        case MfaChallengeOutcome.unavailable:
          return MfaEnableResult.temporary;
        case MfaChallengeOutcome.cancelled:
          return MfaEnableResult.cancelled;
        case MfaChallengeOutcome.failed:
          return MfaEnableResult.failed;
      }
    }
    return MfaEnableResult.failed;
  }
}
