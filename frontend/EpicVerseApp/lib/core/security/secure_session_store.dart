import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Minimal key/value interface over platform-backed secure storage, so the MFA session persistence can be tested
/// with an in-memory fake and the real plugin is only touched on devices.
abstract class SecureKeyValueStore {
  Future<String?> read(String key);
  Future<void> write(String key, String value);
  Future<void> delete(String key);
}

/// iOS Keychain (this device only: never synced or restored to another device, available after first unlock)
/// and Android Keystore-backed AES-GCM. Android backups are disabled in the manifest.
class PlatformSecureStore implements SecureKeyValueStore {
  PlatformSecureStore()
      : _storage = const FlutterSecureStorage(
          iOptions: IOSOptions(accessibility: KeychainAccessibility.first_unlock_this_device),
          aOptions: AndroidOptions(),
        );

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read(String key) => _storage.read(key: key);

  @override
  Future<void> write(String key, String value) => _storage.write(key: key, value: value);

  @override
  Future<void> delete(String key) => _storage.delete(key: key);
}

/// In-memory store used by tests.
class MemorySecureStore implements SecureKeyValueStore {
  final Map<String, String> values = {};
  bool failReads = false;
  bool failWrites = false;

  @override
  Future<String?> read(String key) async {
    if (failReads) throw StateError('read failed');
    return values[key];
  }

  @override
  Future<void> write(String key, String value) async {
    if (failWrites) throw StateError('write failed');
    values[key] = value;
  }

  @override
  Future<void> delete(String key) async => values.remove(key);
}

/// What is persisted: the server-issued MFA session token, the Firebase UID it was issued for, and a local expiry
/// (a convenience only: the server remains the authority on expiry, user/sign-in binding and revocation).
class PersistedMfaSession {
  PersistedMfaSession({required this.uid, required this.token, required this.expiresAtMs});

  static const storageKey = 'epicverse.mfa_session.v1';

  final String uid;
  final String token;
  final int expiresAtMs;

  String encode() => jsonEncode({'v': 1, 'uid': uid, 'token': token, 'exp': expiresAtMs});

  /// Returns null for anything that is not exactly the expected shape (corrupted or foreign data is ignored).
  static PersistedMfaSession? decode(String? raw) {
    if (raw == null || raw.isEmpty) return null;
    try {
      final d = jsonDecode(raw);
      if (d is! Map || d['v'] != 1) return null;
      final uid = d['uid'], token = d['token'], exp = d['exp'];
      if (uid is! String || uid.isEmpty || token is! String || token.length < 16 || exp is! int) return null;
      return PersistedMfaSession(uid: uid, token: token, expiresAtMs: exp);
    } catch (_) {
      return null;
    }
  }
}
