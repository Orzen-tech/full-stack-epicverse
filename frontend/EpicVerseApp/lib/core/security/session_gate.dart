import 'mfa_flow.dart';

/// Pure decisions shared by the login and splash flows. `status` is null when the backend predates F-09
/// (no `/auth/session-status` route); the profile (`GET /user/{uid}`) then carries the legacy fields.
class SessionGate {
  SessionGate._();

  /// True when the account's email is verified, per the server.
  static bool emailVerified(SessionStatus? status, Map<dynamic, dynamic> profile) =>
      status?.emailVerified ?? (profile['email_verified'] == true);

  /// True when the server requires an MFA challenge for this sign-in (modern backends only).
  static bool needsServerMfaChallenge(SessionStatus? status) =>
      status != null && status.mfaEnabled && !status.mfaSessionValid;

  /// True only on a pre-F-09 backend for an MFA-enabled profile: the previous client-side OTP prompt.
  /// Fails closed: only a missing value or an explicit `false` means "no MFA"; any other value (including a
  /// malformed one) keeps the prompt. The original inline code threw on such values rather than skipping MFA.
  static bool needsLegacyClientMfa(SessionStatus? status, Map<dynamic, dynamic> profile) {
    if (status != null) return false;
    final value = profile['mfa_enabled'];
    return value != null && value != false;
  }
}
