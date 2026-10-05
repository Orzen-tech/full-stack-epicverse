import 'package:firebase_auth/firebase_auth.dart';

/// Holds the server-issued MFA session token in memory only (never persisted,
/// never logged), bound to the Firebase UID it was issued for. The backend
/// alone decides whether a session is valid; this is just a carrier.
class MfaSessionManager {
  MfaSessionManager._();

  static String? _uid;
  static String? _token;

  static void setSession(String uid, String token) {
    _uid = uid;
    _token = token;
  }

  /// Returns the token only for the same UID; a different (or no) user clears it.
  static String? getSessionForUid(String? uid) {
    if (uid == null || uid != _uid) {
      clear();
      return null;
    }
    return _token;
  }

  static String? currentSession() => getSessionForUid(FirebaseAuth.instance.currentUser?.uid);

  static void clear() {
    _uid = null;
    _token = null;
  }
}
