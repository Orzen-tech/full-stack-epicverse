import 'package:epicverse/core/security/mfa_enable_flow.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

// MFA enable orchestration: an explicit password re-authentication before EVERY attempt, one clear retry when the
// server says the sign-in is no longer recent, and no retries (and no extra code requests) for anything else.

void main() {
  final navKey = GlobalKey<NavigatorState>();
  late List<String> log;
  late List<String> messages;

  Future<MfaEnableResult> run(WidgetTester tester, List<MfaChallengeOutcome> challengeOutcomes,
      {List<bool>? reauthResults}) async {
    await tester.pumpWidget(MaterialApp(navigatorKey: navKey, home: const SizedBox()));
    var challengeCalls = 0;
    var reauthCalls = 0;
    MfaEnableFlow.useChallengeForTest((nav) async {
      log.add('challenge');
      return MfaChallengeResult(challengeOutcomes[challengeCalls++]);
    });
    return MfaEnableFlow.run(
      nav: navKey.currentState!,
      reauthenticate: () async {
        log.add('reauth');
        final results = reauthResults ?? List.filled(5, true);
        return results[reauthCalls++];
      },
      notify: messages.add,
    );
  }

  setUp(() {
    log = [];
    messages = [];
  });
  tearDown(() => MfaEnableFlow.useChallengeForTest(null));

  testWidgets('happy path: password first, then one code challenge, no messages', (tester) async {
    final r = await run(tester, [MfaChallengeOutcome.verified]);
    expect(r, MfaEnableResult.enabled);
    expect(log, ['reauth', 'challenge']);
    expect(messages, isEmpty);
  });

  testWidgets('cancelled or wrong password: no code is requested at all', (tester) async {
    final r = await run(tester, [MfaChallengeOutcome.verified], reauthResults: [false]);
    expect(r, MfaEnableResult.cancelled);
    expect(log, ['reauth']);
  });

  testWidgets('stale sign-in during code entry: clear message, password asked again, safe retry succeeds', (tester) async {
    final r = await run(tester, [MfaChallengeOutcome.reauthRequired, MfaChallengeOutcome.verified]);
    expect(r, MfaEnableResult.enabled);
    expect(log, ['reauth', 'challenge', 'reauth', 'challenge'], reason: 'every attempt starts with the password');
    expect(messages, hasLength(1));
    expect(messages.single, contains('confirm your password again'));
  });

  testWidgets('the retry is limited to one: a second stale sign-in stops with a clear message', (tester) async {
    final r = await run(tester, [MfaChallengeOutcome.reauthRequired, MfaChallengeOutcome.reauthRequired, MfaChallengeOutcome.verified]);
    expect(r, MfaEnableResult.reauthExpired);
    expect(log, ['reauth', 'challenge', 'reauth', 'challenge']);
    expect(messages, hasLength(2));
    expect(messages.last, contains('sign-in expired'));
  });

  testWidgets('user declines the password prompt on the retry: stops, no second challenge', (tester) async {
    final r = await run(tester, [MfaChallengeOutcome.reauthRequired, MfaChallengeOutcome.verified], reauthResults: [true, false]);
    expect(r, MfaEnableResult.cancelled);
    expect(log, ['reauth', 'challenge', 'reauth']);
  });

  for (final c in {
    MfaChallengeOutcome.rateLimited: MfaEnableResult.temporary,
    MfaChallengeOutcome.unavailable: MfaEnableResult.temporary,
    MfaChallengeOutcome.cancelled: MfaEnableResult.cancelled,
    MfaChallengeOutcome.failed: MfaEnableResult.failed,
  }.entries) {
    testWidgets('${c.key.name}: no automatic retry, so no extra code request (existing limits untouched)', (tester) async {
      final r = await run(tester, [c.key, MfaChallengeOutcome.verified]);
      expect(r, c.value);
      expect(log, ['reauth', 'challenge']);
      expect(messages, isEmpty);
    });
  }
}
