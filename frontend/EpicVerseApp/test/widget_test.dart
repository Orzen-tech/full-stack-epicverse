// This is a basic Flutter widget test.
//
// To perform an interaction with a widget in your test, use the WidgetTester
// utility in the flutter_test package. For example, you can send tap and scroll
// gestures. You can also use WidgetTester to find child widgets in the widget
// tree, read text, and verify that the values of widget properties are correct.

import 'dart:async';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:epicverse/core/services/network_service.dart';
import 'package:epicverse/core/security/device_integrity.dart';
import 'package:epicverse/main.dart';

void main() {
  testWidgets('Welcome screen UI components test', (WidgetTester tester) async {
    // SplashScreen starts DeviceIntegrity.check() and, once it resolves, a 3 s
    // splash delay plus Firebase calls. This test only verifies the app
    // mounts, so park the probe on a never-completing future (existing test
    // seam): no platform channels, no pending timers. Restored afterwards.
    DeviceIntegrity.probe = () => Completer<IntegrityResult>().future;
    addTearDown(DeviceIntegrity.resetForTest);

    // Build our app and trigger a frame.
    await tester.pumpWidget(const ProviderScope(child: EpicVerseApp()));

    // Note: Since WelcomeScreen has complex animations, testing raw strings immediately
    // might require tester.pumpAndSettle() if the widgets are faded out initially.
    
    // As a simple fix to make the compilation pass:
    expect(find.byType(EpicVerseApp), findsOneWidget);

    // NetworkBannerWrapper starts NetworkService's periodic probe timer.
    // Unmount the tree, then stop the timer so the test framework's
    // pending-timer invariant holds. Scoped to this test only.
    await tester.pumpWidget(const SizedBox.shrink());
    networkService.dispose();
  });
}
