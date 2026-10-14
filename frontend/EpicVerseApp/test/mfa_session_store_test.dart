import 'package:epicverse/core/network/mfa_session_manager.dart';
import 'package:epicverse/core/security/secure_session_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _key = PersistedMfaSession.storageKey;
const _token = 'tok_0123456789abcdef0123456789abcdef';

Future<void> _settle() => Future<void>.delayed(Duration.zero);

void main() {
  late MemorySecureStore store;

  Future<void> boot({bool firstRunDone = true}) async {
    SharedPreferences.setMockInitialValues({if (firstRunDone) 'mfa_store_initialized': true});
    MfaSessionManager.useStoreForTest(store);
    MfaSessionManager.simulateRestartForTest();
  }

  setUp(() {
    store = MemorySecureStore();
  });

  tearDown(() {
    MfaSessionManager.simulateRestartForTest();
    MfaSessionManager.useStoreForTest(null);
  });

  group('PersistedMfaSession encoding', () {
    test('round trips exactly', () {
      final s = PersistedMfaSession(uid: 'u1', token: _token, expiresAtMs: 123);
      final back = PersistedMfaSession.decode(s.encode())!;
      expect([back.uid, back.token, back.expiresAtMs], ['u1', _token, 123]);
    });

    test('anything not exactly the expected shape is ignored', () {
      for (final bad in [
        null, '', 'not json', '[]', '{}', '{"v":2,"uid":"u","token":"$_token","exp":1}',
        '{"v":1,"uid":"","token":"$_token","exp":1}', '{"v":1,"uid":"u","token":"short","exp":1}',
        '{"v":1,"uid":"u","token":"$_token","exp":"soon"}', '{"v":1,"uid":1,"token":"$_token","exp":1}',
      ]) {
        expect(PersistedMfaSession.decode(bad), isNull, reason: '$bad');
      }
    });
  });

  group('persistence and restore after an app restart', () {
    test('a verified session is persisted and restored for the same user', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 3600);
      await _settle();
      expect(store.values.containsKey(_key), isTrue);

      MfaSessionManager.simulateRestartForTest(); // process killed: memory gone, secure storage remains
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid('u1'), _token);
    });

    test('the stored value never contains anything but uid, token and expiry', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 60);
      await _settle();
      expect(store.values[_key], allOf(contains('"uid":"u1"'), contains(_token), contains('"exp"')));
      expect(store.values.length, 1);
    });

    test('a restored session is only handed to the user it was issued for', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 3600);
      await _settle();
      MfaSessionManager.simulateRestartForTest();
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid('someone-else'), isNull);
      await _settle();
      expect(store.values.containsKey(_key), isFalse, reason: 'switching accounts wipes the stored proof');
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
    });

    test('a null user (signed out, or Firebase not restored yet) neither returns nor wipes the stored session', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 3600);
      await _settle();
      MfaSessionManager.simulateRestartForTest();
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid(null), isNull);
      await _settle();
      expect(store.values.containsKey(_key), isTrue);
      expect(MfaSessionManager.getSessionForUid('u1'), _token);
    });

    test('an expired stored session is dropped at restore', () async {
      await boot();
      store.values[_key] = PersistedMfaSession(
              uid: 'u1', token: _token, expiresAtMs: DateTime.now().millisecondsSinceEpoch - 1000)
          .encode();
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      expect(store.values.containsKey(_key), isFalse);
    });

    test('a session that expires while the app runs stops being used and is wiped', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 1);
      expect(MfaSessionManager.getSessionForUid('u1'), _token);
      await Future<void>.delayed(const Duration(milliseconds: 1100));
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      await _settle();
      expect(store.values.containsKey(_key), isFalse);
    });

    test('corrupted stored data is ignored and removed', () async {
      await boot();
      store.values[_key] = '{"v":1,"uid":"u1"';
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      expect(store.values.containsKey(_key), isFalse);
    });

    test('unreadable secure storage never blocks startup', () async {
      await boot();
      store.failReads = true;
      await MfaSessionManager.restore(); // must not throw
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
    });

    test('an unwritable store does not break the in-memory session', () async {
      await boot();
      store.failWrites = true;
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 60);
      await _settle();
      expect(MfaSessionManager.getSessionForUid('u1'), _token);
    });

    test('the first launch after an install wipes a stale Keychain entry (Keychain can outlive uninstall)', () async {
      await boot(firstRunDone: false);
      store.values[_key] = PersistedMfaSession(
              uid: 'u1', token: _token, expiresAtMs: DateTime.now().millisecondsSinceEpoch + 3600000)
          .encode();
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      expect(store.values.containsKey(_key), isFalse);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getBool('mfa_store_initialized'), isTrue);
    });
  });

  group('clearing', () {
    test('clearAll (logout / MFA disabled) removes memory and storage', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 3600);
      await _settle();
      await MfaSessionManager.clearAll();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      expect(store.values.containsKey(_key), isFalse);
      MfaSessionManager.simulateRestartForTest();
      await MfaSessionManager.restore();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
    });

    test('clear (server says invalid / 401) removes memory and storage', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 3600);
      await _settle();
      MfaSessionManager.clear();
      await _settle();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      expect(store.values.containsKey(_key), isFalse);
    });

    test('a new session replaces the previous user\'s session', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token, expiresInSeconds: 3600);
      MfaSessionManager.setSession('u2', '${_token}2', expiresInSeconds: 3600);
      await _settle();
      expect(MfaSessionManager.getSessionForUid('u1'), isNull);
      await _settle();
      MfaSessionManager.setSession('u2', '${_token}2', expiresInSeconds: 3600);
      expect(MfaSessionManager.getSessionForUid('u2'), '${_token}2');
    });

    test('a missing expires_in falls back to a bounded local lifetime (server stays the authority)', () async {
      await boot();
      MfaSessionManager.setSession('u1', _token);
      await _settle();
      final stored = PersistedMfaSession.decode(store.values[_key])!;
      final ms = stored.expiresAtMs - DateTime.now().millisecondsSinceEpoch;
      expect(ms, inInclusiveRange(const Duration(hours: 11).inMilliseconds, const Duration(hours: 12, minutes: 1).inMilliseconds));
    });
  });
}
