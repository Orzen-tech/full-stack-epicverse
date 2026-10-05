/// Hook used by ApiClient when the backend answers MFA_SESSION_REQUIRED.
/// The app registers a handler that shows the MFA challenge and returns true
/// only when the backend issued a new MFA session.
class MfaReprompt {
  MfaReprompt._();

  static Future<bool> Function()? handler;
}
