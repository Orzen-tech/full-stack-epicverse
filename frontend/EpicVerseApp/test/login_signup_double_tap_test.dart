import 'dart:async';
import 'dart:io';

import 'package:epicverse/core/security/device_integrity.dart';
import 'package:epicverse/presentation/screens/login_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Finding #4 follow-up: the device check must not open a window in which a
/// second tap starts a second sign-in/signup, and the button must always
/// recover (blocked, unknown, safe or failure).
void main() {
  late List<Completer<IntegrityResult>> probes;

  setUp(() {
    DeviceIntegrity.resetForTest();
    DeviceIntegrity.reporter = (_) {};
    probes = [];
    // Every probe waits until the test completes it, so the in-between state is observable.
    DeviceIntegrity.probe = () {
      final c = Completer<IntegrityResult>();
      probes.add(c);
      return c.future;
    };
  });
  tearDown(DeviceIntegrity.resetForTest);

  Future<void> pumpLogin(WidgetTester tester) async {
    tester.view.physicalSize = const Size(900, 2000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const ProviderScope(child: MaterialApp(home: LoginScreen())));
    final fields = find.byType(TextFormField);
    await tester.enterText(fields.at(0), 'user@example.com');
    await tester.enterText(fields.at(1), 'a-password');
    await tester.pump();
  }

  GestureTapCallback signInHandler(WidgetTester tester) => tester
      .widget<GestureDetector>(
        find.ancestor(of: find.text('Sign In'), matching: find.byType(GestureDetector)).first,
      )
      .onTap!;

  bool spinning() => find.byType(CircularProgressIndicator).evaluate().isNotEmpty;

  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 100));
    }
  }

  testWidgets('loading starts immediately, before the device check answers', (tester) async {
    await pumpLogin(tester);
    await tester.tap(find.text('Sign In'));
    await tester.pump();
    expect(probes, hasLength(1));
    expect(spinning(), isTrue); // the button is already in its in-progress state
    probes.single.complete(const IntegrityResult(IntegrityState.compromised));
    await settle(tester);
  });

  testWidgets('rapid repeated taps start exactly one attempt (not just one shared probe)', (tester) async {
    // Count attempts through the handler's own log lines: the integrity check already
    // shares one probe between simultaneous callers, so a probe count alone would not
    // reveal a duplicate sign-in.
    final logs = <String>[];
    final original = debugPrint;
    debugPrint = (String? message, {int? wrapWidth}) {
      if (message != null) logs.add(message);
    };
    try {
      int count(String text) => logs.where((l) => l.contains(text)).length;

      await pumpLogin(tester);
      final handler = signInHandler(tester); // grabbed while the button is still enabled
      handler();
      handler(); // direct re-entry, bypassing the disabled-button UI
      handler();
      await tester.pump();
      await tester.tap(find.byType(CircularProgressIndicator)); // and real taps on the spinner
      await tester.tap(find.byType(CircularProgressIndicator));
      await tester.pump();
      expect(count('Sign-In tapped'), 1, reason: 'only the first tap may start an attempt');
      expect(probes, hasLength(1));

      // Let the single check pass: exactly one Firebase sign-in is attempted.
      probes.single.complete(const IntegrityResult(IntegrityState.safe));
      await settle(tester);
      expect(count('Sign-In tapped'), 1);
      expect(count('Firebase signInWithEmailAndPassword'), 1);
      expect(spinning(), isFalse);
    } finally {
      debugPrint = original; // the framework checks this before teardown runs
    }
  });

  testWidgets('compromised: Security Alert shown, loading cleared, nothing else started', (tester) async {
    await pumpLogin(tester);
    await tester.tap(find.text('Sign In'));
    await tester.pump();
    probes.single.complete(const IntegrityResult(IntegrityState.compromised, reasons: ['jailbreak']));
    await settle(tester);
    expect(find.text('Security Alert'), findsOneWidget);
    expect(spinning(), isFalse);
    expect(find.text('Sign In'), findsOneWidget); // button back to its normal state
  });

  for (final state in [IntegrityState.unknown, IntegrityState.safe]) {
    testWidgets('$state: sign-in proceeds (Firebase is absent in tests, so it fails) and the button recovers',
        (tester) async {
      await pumpLogin(tester);
      await tester.tap(find.text('Sign In'));
      await tester.pump();
      probes.single.complete(IntegrityResult(state));
      await settle(tester);
      expect(find.text('Security Alert'), findsNothing);
      expect(spinning(), isFalse, reason: 'must not be left stuck loading after the attempt ends');
      expect(find.text('Sign In'), findsOneWidget);
    });
  }

  testWidgets('after an attempt finishes, the next tap starts a fresh device check', (tester) async {
    await pumpLogin(tester);
    await tester.tap(find.text('Sign In'));
    await tester.pump();
    probes[0].complete(const IntegrityResult(IntegrityState.unknown));
    await settle(tester);
    expect(spinning(), isFalse);

    await tester.tap(find.text('Sign In'));
    await tester.pump();
    expect(probes, hasLength(2)); // a safe/unknown result is never reused across attempts
    probes[1].complete(const IntegrityResult(IntegrityState.safe));
    await settle(tester);
    expect(spinning(), isFalse);
  });

  testWidgets('an invalid form never starts a device check or loading', (tester) async {
    tester.view.physicalSize = const Size(900, 2000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
    await tester.pumpWidget(const ProviderScope(child: MaterialApp(home: LoginScreen())));
    await tester.tap(find.text('Sign In'));
    await tester.pump();
    expect(probes, isEmpty);
    expect(spinning(), isFalse);
  });

  group('signup (structural guard: the screen needs a live Firebase to construct)', () {
    late String body;
    setUp(() {
      final src = File('lib/presentation/screens/create_profile_screen.dart').readAsStringSync();
      final start = src.indexOf('Future<void> _submitForm()');
      expect(start, greaterThanOrEqualTo(0));
      body = src.substring(start, src.indexOf('Future<void> _completeExistingFirebaseUser'));
    });

    test('re-entry is ignored while a signup or its device check runs', () {
      expect(body, contains('if (_isLoading) return;'));
      expect(body.indexOf('if (_isLoading) return;'), lessThan(body.indexOf('_formKey.currentState!.validate()')));
    });

    test('loading is set BEFORE the device check, and the check runs inside the try', () {
      final loading = body.indexOf('setState(() => _isLoading = true)');
      final tryAt = body.indexOf('try {', loading);
      final check = body.indexOf('blockIfCompromised(context)');
      final finallyAt = body.indexOf('finally {');
      expect(loading, greaterThan(0));
      expect(tryAt, greaterThan(loading));
      expect(check, greaterThan(tryAt), reason: 'the check must be inside the try');
      expect(check, lessThan(finallyAt));
      expect(check, lessThan(body.indexOf('createUserWithEmailAndPassword')));
    });

    test('finally always clears the loading state', () {
      final fin = body.substring(body.indexOf('finally {'));
      expect(fin, contains('setState(() => _isLoading = false)'));
    });
  });

  group('login (structural guard)', () {
    test('loading is set before the check, which sits inside the try with the finally reset', () {
      final src = File('lib/presentation/screens/login_screen.dart').readAsStringSync();
      final body = src.substring(src.indexOf('Future<void> _handleLogin()'));
      final loading = body.indexOf('setState(() => _isLoading = true)');
      final tryAt = body.indexOf('try {', loading);
      final check = body.indexOf('blockIfCompromised(context)');
      expect(body.indexOf('if (_isLoading) return;'), lessThan(loading));
      expect(check, greaterThan(tryAt));
      expect(check, lessThan(body.indexOf('signInWithEmailAndPassword')));
      expect(body.substring(body.indexOf('finally {')), contains('setState(() => _isLoading = false)'));
    });
  });
}
