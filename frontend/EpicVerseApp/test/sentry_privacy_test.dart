import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:sentry_flutter/sentry_flutter.dart';
import 'package:epicverse/core/services/logger_service.dart';

/// R3: Sentry must not attach default PII, and endpoint tags must not carry
/// per-user identifiers.
void main() {
  test('main.dart can never enable sendDefaultPii', () {
    final src = File('lib/main.dart')
        .readAsLinesSync()
        .where((l) => !l.trim().startsWith('//'))
        .join('\n');
    expect(RegExp(r'sendDefaultPii\s*=\s*true').hasMatch(src), isFalse);
    expect(RegExp(r'sendDefaultPii\s*=\s*false').hasMatch(src), isTrue);
    // unrelated setting is deliberately untouched
    expect(src, contains('tracesSampleRate = 1.0'));
  });

  group('normalizeEndpoint', () {
    String n(String s) => LoggerService.normalizeEndpoint(s);

    test('per-user paths lose the UID', () {
      expect(n('/user/AbC123uid'), '/user/{uid}');
      expect(n('/user/AbC123uid/cancel-deletion'), '/user/{uid}/cancel-deletion');
      expect(n('/user/AbC123uid/mfa/status'), '/user/{uid}/mfa/status');
    });

    test('fixed routes keep their names', () {
      expect(n('/user/update-mfa'), '/user/update-mfa');
      expect(n('/user/mfa/challenge'), '/user/mfa/challenge');
    });

    test('invite codes are replaced', () {
      expect(n('/validate-invite/EPIC-SECRET1'), '/validate-invite/{code}');
    });

    test('query string and fragment are dropped', () {
      expect(n('/user/uid1?token=abc#x'), '/user/{uid}');
      expect(n('/faq?lang=en'), '/faq');
    });

    test('other paths are unchanged', () {
      expect(n('/feedback'), '/feedback');
      expect(n('/sync-user'), '/sync-user');
    });
  });

  test('Sentry capture still works and tags carry the normalized endpoint',
      () async {
    final events = <SentryEvent>[];
    await Sentry.init((o) {
      o.dsn = 'https://public@example.invalid/1';
      o.sendDefaultPii = false;
      o.beforeSend = (event, hint) {
        events.add(event);
        return null; // never leaves the test process
      };
    });
    LoggerService.logError(
      Exception('boom'),
      endpoint: '/user/RealFirebaseUid123?x=1',
      method: 'GET',
      statusCode: 500,
      screenName: 'Test',
    );
    await Future<void>.delayed(const Duration(milliseconds: 200));
    await Sentry.close();
    expect(events, hasLength(1));
    expect(events.single.tags?['endpoint'], '/user/{uid}');
    expect(events.single.tags?['status_code'], '500');
    expect(events.single.tags.toString(), isNot(contains('RealFirebaseUid123')));
    expect(events.single.request?.url, isNull);
  });
}
