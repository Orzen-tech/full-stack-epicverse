import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart' show kReleaseMode, visibleForTesting;
import 'package:shared_preferences/shared_preferences.dart';

import '../security/secure_session_store.dart';

/// Holds the server-issued MFA session token, bound to the Firebase UID it was issued for, in memory and (after a
/// verified code) in platform-backed secure storage so it survives an app restart. The backend alone decides whether
/// a session is valid: it checks the token's hash, the user, the Firebase sign-in time, expiry and revocation. This
/// class is only a carrier; a restored token is never treated as proof by itself, and Firebase authentication alone
/// is never treated as MFA proof.
///
/// The token is never logged. It is cleared on logout, when MFA is disabled, when a different user is seen, on
/// expiry, and whenever the server reports the session invalid.
class MfaSessionManager {
  MfaSessionManager._();

  /// Fallback only if the server omits `expires_in` (it currently sends 12 hours).
  static const _defaultLifetime = Duration(hours: 12);
  static const _firstRunKey = 'mfa_store_initialized';

  static String? _uid;
  static String? _token;
  static int? _expiresAtMs;

  static SecureKeyValueStore _store = PlatformSecureStore();

  /// Test seam, never set by app code (a test guards this) and ignored in release builds.
  @visibleForTesting
  static void useStoreForTest(SecureKeyValueStore? store) {
    if (kReleaseMode) return;
    _store = store ?? PlatformSecureStore();
  }

  /// Test seam: forget the in-memory session only, as if the app process had been killed. Ignored in release builds.
  @visibleForTesting
  static void simulateRestartForTest() {
    if (kReleaseMode) return;
    _clearMemory();
  }

  static int _nowMs() => DateTime.now().millisecondsSinceEpoch;

  /// Loads a previously stored session at startup. On the first launch after an install the store is wiped first:
  /// the iOS Keychain can outlive an uninstall, while SharedPreferences cannot. Never throws.
  static Future<void> restore() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!(prefs.getBool(_firstRunKey) ?? false)) {
        await _deletePersisted();
        await prefs.setBool(_firstRunKey, true);
        return;
      }
      final stored = PersistedMfaSession.decode(await _store.read(PersistedMfaSession.storageKey));
      if (stored == null || stored.expiresAtMs <= _nowMs()) {
        await _deletePersisted();
        return;
      }
      _uid = stored.uid;
      _token = stored.token;
      _expiresAtMs = stored.expiresAtMs;
    } catch (_) {
      // Unreadable storage must never block startup; the user simply gets a fresh code challenge.
      _clearMemory();
    }
  }

  static void setSession(String uid, String token, {int? expiresInSeconds}) {
    final lifetime = (expiresInSeconds != null && expiresInSeconds > 0)
        ? Duration(seconds: expiresInSeconds)
        : _defaultLifetime;
    _uid = uid;
    _token = token;
    _expiresAtMs = _nowMs() + lifetime.inMilliseconds;
    _persist(); // best effort; the in-memory session already works
  }

  /// Returns the token only for the same UID that is still unexpired. A different user clears everything. A null
  /// UID (signed out, or Firebase not restored yet) returns nothing and leaves the stored session untouched.
  static String? getSessionForUid(String? uid) {
    if (uid == null) return null;
    if (_uid != null && uid != _uid) {
      clear();
      return null;
    }
    if (_token == null) return null;
    if (_expiresAtMs != null && _expiresAtMs! <= _nowMs()) {
      clear();
      return null;
    }
    return _token;
  }

  static String? currentSession() => getSessionForUid(FirebaseAuth.instance.currentUser?.uid);

  /// Fire-and-forget clear of memory and storage (for callers that cannot await).
  static void clear() {
    _clearMemory();
    _deletePersisted();
  }

  /// Awaited variant, used on logout and when MFA is disabled.
  static Future<void> clearAll() async {
    _clearMemory();
    await _deletePersisted();
  }

  static void _clearMemory() {
    _uid = null;
    _token = null;
    _expiresAtMs = null;
  }

  static Future<void> _persist() async {
    final uid = _uid, token = _token, exp = _expiresAtMs;
    if (uid == null || token == null || exp == null) return;
    try {
      await _store.write(
          PersistedMfaSession.storageKey, PersistedMfaSession(uid: uid, token: token, expiresAtMs: exp).encode());
    } catch (_) {}
  }

  static Future<void> _deletePersisted() async {
    try {
      await _store.delete(PersistedMfaSession.storageKey);
    } catch (_) {}
  }
}
