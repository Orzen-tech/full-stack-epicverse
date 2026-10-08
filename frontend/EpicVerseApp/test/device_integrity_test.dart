import 'dart:async';
import 'dart:io';

import 'package:epicverse/core/security/device_integrity.dart';
import 'package:epicverse/presentation/widgets/security_alert_dialog.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// Finding #4: shared integrity decision (safe / compromised / unknown).
void main() {
  late List<IntegrityResult> reported;

  setUp(() {
    DeviceIntegrity.resetForTest();
    reported = [];
    DeviceIntegrity.reporter = reported.add;
    DeviceIntegrity.isDebugMode = () => false; // behave like a non-debug build
  });
  tearDown(DeviceIntegrity.resetForTest);

  Map<Object?, Object?> signals({
    List<String> strong = const [],
    List<String> weak = const [],
    bool debugged = false,
  }) =>
      {'strong': strong, 'weak': weak, 'debugged': debugged};

  void android({Future<bool> Function()? jb, Future<int?> Function()? tracer}) {
    DeviceIntegrity.platform = () => IntegrityPlatform.android;
    DeviceIntegrity.pluginJailbroken = jb ?? () async => false;
    DeviceIntegrity.androidTracerPid = tracer ?? () async => 0;
  }

  void ios({
    Future<bool> Function()? jb,
    Future<Map<Object?, Object?>?> Function(bool)? native,
    bool release = true,
  }) {
    DeviceIntegrity.platform = () => IntegrityPlatform.ios;
    DeviceIntegrity.isReleaseMode = () => release;
    DeviceIntegrity.pluginJailbroken = jb ?? () async => false;
    DeviceIntegrity.iosSignals = native ?? (_) async => signals();
  }

  group('Android (existing algorithm preserved)', () {
    test('clean device is safe', () async {
      android();
      expect((await DeviceIntegrity.check()).state, IntegrityState.safe);
    });

    test('plugin root detection is compromised', () async {
      android(jb: () async => true);
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.compromised);
      expect(r.reasons, ['jailbreak']);
    });

    test('TracerPid > 0 is compromised', () async {
      android(tracer: () async => 4242);
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.compromised);
      expect(r.reasons, ['tracer_pid']);
    });

    test('TracerPid is not consulted in debug builds', () async {
      var asked = false;
      android(tracer: () async {
        asked = true;
        return 4242;
      });
      DeviceIntegrity.isDebugMode = () => true;
      expect((await DeviceIntegrity.check()).state, IntegrityState.safe);
      expect(asked, isFalse);
    });

    test('a failed plugin check is unknown, not safe', () async {
      android(jb: () async => throw FakePlatformFailure());
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.unknown);
      expect(r.reasons, contains('jailbreak_check_failed'));
    });

    test('a null TracerPid answer is unknown', () async {
      android(tracer: () async => null);
      expect((await DeviceIntegrity.check()).state, IntegrityState.unknown);
    });

    test('a positive signal wins over a failed source', () async {
      android(jb: () async => true, tracer: () async => throw FakePlatformFailure());
      expect((await DeviceIntegrity.check()).state, IntegrityState.compromised);
    });
  });

  group('iOS', () {
    test('clean device is safe', () async {
      ios();
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.safe);
      expect(r.weakSignals, isEmpty);
    });

    test('existing plugin jailbreak detection still blocks', () async {
      ios(jb: () async => true);
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.compromised);
      expect(r.reasons, ['jailbreak']);
    });

    for (final code in ['dyld', 'frida_server_file', 'frida_port']) {
      test('strong indicator $code is compromised', () async {
        ios(native: (_) async => signals(strong: [code]));
        final r = await DeviceIntegrity.check();
        expect(r.state, IntegrityState.compromised);
        expect(r.reasons, [code]);
      });
    }

    test('an attached debugger is compromised', () async {
      ios(native: (_) async => signals(debugged: true));
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.compromised);
      expect(r.reasons, ['debugger']);
    });

    test('weak indicators alone never block (ports 22/44/4444, P_SELECT)', () async {
      ios(native: (_) async => signals(weak: ['local_port', 'p_select']));
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.safe);
      expect(r.weakSignals, ['local_port', 'p_select']);
    });

    test('debugger evaluation follows the build mode (Dart decides, native obeys)', () async {
      bool? asked;
      ios(release: true, native: (evaluate) async {
        asked = evaluate;
        return signals();
      });
      await DeviceIntegrity.check();
      expect(asked, isTrue);

      DeviceIntegrity.resetForTest();
      DeviceIntegrity.isDebugMode = () => false;
      DeviceIntegrity.reporter = reported.add;
      ios(release: false, native: (evaluate) async {
        asked = evaluate;
        return signals();
      });
      await DeviceIntegrity.check();
      expect(asked, isFalse);
    });

    test('native failure is unknown, never safe', () async {
      ios(native: (_) async => throw FakePlatformFailure());
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.unknown);
      expect(r.reasons, contains('native_signals_failed'));
    });

    test('a missing native handler (null answer) is unknown', () async {
      ios(native: (_) async => null);
      expect((await DeviceIntegrity.check()).state, IntegrityState.unknown);
    });

    test('plugin failure with clean signals is unknown', () async {
      ios(jb: () async => throw FakePlatformFailure());
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.unknown);
      expect(r.reasons, contains('jailbreak_check_failed'));
    });

    test('malformed native data is rejected as unknown (no arbitrary text is trusted)', () async {
      final bad = <Map<Object?, Object?>>[
        {'strong': 'dyld', 'weak': [], 'debugged': false},
        {'strong': [], 'weak': [], 'debugged': 'no'},
        {'strong': ['Port 27042 is open at /private/secret'], 'weak': [], 'debugged': false},
        {'strong': List.filled(9, 'dyld'), 'weak': [], 'debugged': false},
        {'strong': [1], 'weak': [], 'debugged': false},
        <Object?, Object?>{},
      ];
      for (final data in bad) {
        DeviceIntegrity.resetForTest();
        DeviceIntegrity.isDebugMode = () => false;
        DeviceIntegrity.reporter = reported.add;
        ios(native: (_) async => data);
        expect((await DeviceIntegrity.check()).state, IntegrityState.unknown, reason: '$data');
      }
    });

    test('a positive plugin answer still wins when native signals fail', () async {
      ios(jb: () async => true, native: (_) async => throw FakePlatformFailure());
      expect((await DeviceIntegrity.check()).state, IntegrityState.compromised);
    });
  });

  group('other platforms and timeouts', () {
    test('non-mobile platform is safe (not applicable)', () async {
      DeviceIntegrity.platform = () => IntegrityPlatform.other;
      expect((await DeviceIntegrity.check()).state, IntegrityState.safe);
    });

    test('a source that never answers times out to unknown', () async {
      DeviceIntegrity.sourceTimeout = const Duration(milliseconds: 40);
      android(jb: () => Completer<bool>().future);
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.unknown);
    });

    test('an unexpected exception from the probe itself is unknown', () async {
      DeviceIntegrity.probe = () async => throw StateError('boom');
      final r = await DeviceIntegrity.check();
      expect(r.state, IntegrityState.unknown);
      expect(r.reasons, ['probe_error']);
    });
  });

  group('deduplication, repeat checks and stickiness', () {
    test('simultaneous callers share a single probe', () async {
      var probes = 0;
      final gate = Completer<void>();
      DeviceIntegrity.probe = () async {
        probes++;
        await gate.future;
        return const IntegrityResult(IntegrityState.safe);
      };
      final a = DeviceIntegrity.check();
      final b = DeviceIntegrity.check();
      final c = DeviceIntegrity.check();
      gate.complete();
      final results = await Future.wait([a, b, c]);
      expect(probes, 1);
      expect(results.map((r) => r.state), everyElement(IntegrityState.safe));
    });

    test('a safe result is NOT reused across boundaries (splash, login, signup each re-check)', () async {
      var probes = 0;
      DeviceIntegrity.probe = () async {
        probes++;
        return const IntegrityResult(IntegrityState.safe);
      };
      await DeviceIntegrity.check(); // splash
      await DeviceIntegrity.check(); // login
      await DeviceIntegrity.check(); // signup
      expect(probes, 3);
    });

    test('unknown is not remembered: the next boundary can be safe', () async {
      final answers = [IntegrityState.unknown, IntegrityState.safe];
      DeviceIntegrity.probe = () async => IntegrityResult(answers.removeAt(0));
      expect((await DeviceIntegrity.check()).state, IntegrityState.unknown);
      expect((await DeviceIntegrity.check()).state, IntegrityState.safe);
    });

    test('compromised is sticky for the process and is not re-probed', () async {
      var probes = 0;
      final answers = [IntegrityState.compromised, IntegrityState.safe];
      DeviceIntegrity.probe = () async {
        probes++;
        return IntegrityResult(answers.removeAt(0), reasons: const ['jailbreak']);
      };
      expect((await DeviceIntegrity.check()).state, IntegrityState.compromised);
      expect((await DeviceIntegrity.check()).state, IntegrityState.compromised);
      expect((await DeviceIntegrity.check()).state, IntegrityState.compromised);
      expect(probes, 1);
    });
  });

  group('diagnostics are privacy-safe and not noisy', () {
    test('safe results report nothing', () async {
      android();
      await DeviceIntegrity.check();
      await DeviceIntegrity.check();
      expect(reported, isEmpty);
    });

    test('the same unknown outcome is reported once per process', () async {
      android(jb: () async => throw FakePlatformFailure());
      for (var i = 0; i < 4; i++) {
        await DeviceIntegrity.check();
      }
      expect(reported, hasLength(1));
      expect(reported.single.state, IntegrityState.unknown);
    });

    test('compromised and weak-only outcomes are reported once; only fixed codes are included', () async {
      ios(native: (_) async => signals(weak: ['local_port']));
      await DeviceIntegrity.check();
      await DeviceIntegrity.check();
      expect(reported, hasLength(1));
      expect(reported.single.weakSignals, ['local_port']);

      ios(native: (_) async => signals(strong: ['dyld']));
      await DeviceIntegrity.check();
      expect(reported, hasLength(2));
      expect(reported.last.reasons, ['dyld']);
    });

    test('nothing is reported from debug builds', () async {
      DeviceIntegrity.isDebugMode = () => true;
      android(jb: () async => true);
      await DeviceIntegrity.check();
      expect(reported, isEmpty);
    });

    test('a failing reporter never breaks the check', () async {
      DeviceIntegrity.reporter = (_) => throw StateError('telemetry down');
      android(jb: () async => true);
      expect((await DeviceIntegrity.check()).state, IntegrityState.compromised);
    });
  });

  group('Security Alert gating (blockIfCompromised)', () {
    Future<bool?> pump(WidgetTester tester, IntegrityState state) async {
      DeviceIntegrity.probe = () async => IntegrityResult(state);
      bool? blocked;
      await tester.pumpWidget(MaterialApp(
        home: Builder(
          builder: (context) => ElevatedButton(
            onPressed: () async => blocked = await blockIfCompromised(context),
            child: const Text('go'),
          ),
        ),
      ));
      await tester.tap(find.text('go'));
      await tester.pump();
      await tester.pump();
      return blocked;
    }

    testWidgets('compromised blocks and shows the non-dismissible alert', (tester) async {
      expect(await pump(tester, IntegrityState.compromised), isTrue);
      expect(find.text('Security Alert'), findsOneWidget);
      await tester.tapAt(const Offset(2, 2)); // barrier tap must not dismiss it
      await tester.pump();
      expect(find.text('Security Alert'), findsOneWidget);
    });

    testWidgets('safe does not block', (tester) async {
      expect(await pump(tester, IntegrityState.safe), isFalse);
      expect(find.text('Security Alert'), findsNothing);
    });

    testWidgets('unknown does not block (normal access preserved)', (tester) async {
      expect(await pump(tester, IntegrityState.unknown), isFalse);
      expect(find.text('Security Alert'), findsNothing);
    });
  });

  group('wiring guards (source order)', () {
    String fn(String path, String signature) {
      final src = File(path).readAsStringSync();
      final start = src.indexOf(signature);
      expect(start, greaterThanOrEqualTo(0), reason: '$signature not found');
      return src.substring(start);
    }

    test('login re-checks the device before Firebase sign-in', () {
      final body = fn('lib/presentation/screens/login_screen.dart', 'Future<void> _handleLogin()');
      expect(body.indexOf('blockIfCompromised(context)'), greaterThan(0));
      expect(body.indexOf('blockIfCompromised(context)'),
          lessThan(body.indexOf('signInWithEmailAndPassword')));
    });

    test('signup re-checks the device before the account is created', () {
      final body = fn('lib/presentation/screens/create_profile_screen.dart', 'Future<void> _submitForm()');
      final check = body.indexOf('blockIfCompromised(context)');
      expect(check, greaterThan(0));
      expect(check, lessThan(body.indexOf('createUserWithEmailAndPassword')));
    });

    test('splash uses the shared helper and keeps no private copy of the logic', () {
      final src = File('lib/presentation/screens/splash_screen.dart').readAsStringSync();
      expect(src, contains('DeviceIntegrity.check()'));
      expect(src, isNot(contains('FlutterJailbreakDetection')));
      expect(src, isNot(contains('_isRuntimeInstrumented')));
    });

    test('native handler classifies ports safely and never blocks on weak ones', () {
      final swift = File('ios/Runner/AppDelegate.swift').readAsStringSync();
      expect(swift, contains('"integritySignals"'));
      expect(swift, contains('failed.failMessage == "Port 27042 is open"'));
      expect(swift, contains('weak.append("local_port")'));
      expect(swift, contains('weak.append("p_select")'));
      expect(swift, isNot(contains('denyDebugger')));
      expect(swift, isNot(contains('embedded.mobileprovision')));
    });
  });
}

/// Stand-in for a MethodChannel failure (the helper treats any error alike).
class FakePlatformFailure implements Exception {}
