import 'dart:io';

import 'package:epicverse/presentation/screens/create_profile_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// The server allows 20 FAILED invite checks per IP per 10 minutes (shared by /validate-invite and the invite path of
/// /auth/send-otp). Checking the code on every keystroke spent that budget on partial input, whose results were never
/// shown. The code is checked once, when the form is submitted; typing must never reach /validate-invite.
/// Fake values only: they contain 0 and 1, which real invite codes never do.

/// Records every request the app tries to open and refuses it, so nothing leaves the test.
class _RecordingHttpClient implements HttpClient {
  _RecordingHttpClient(this.seen);
  final List<Uri> seen;

  @override
  Future<HttpClientRequest> openUrl(String method, Uri url) async {
    seen.add(url);
    throw const SocketException('network disabled in this test');
  }

  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class _RecordingOverrides extends HttpOverrides {
  final List<Uri> seen = [];

  @override
  HttpClient createHttpClient(SecurityContext? context) => _RecordingHttpClient(seen);
}

String _code(String path) => File(path).readAsLinesSync().where((l) => !l.trim().startsWith('//')).join('\n');

void main() {
  testWidgets('typing an invite code, character by character, never calls /validate-invite', (tester) async {
    final overrides = _RecordingOverrides();
    final previousOverrides = HttpOverrides.current;
    final previousDebugPrint = debugPrint;
    final logs = <String>[];
    HttpOverrides.global = overrides;
    debugPrint = (String? message, {int? wrapWidth}) => logs.add(message ?? '');
    try {
      await tester.binding.setSurfaceSize(const Size(800, 2400));
      await tester.pumpWidget(const ProviderScope(child: MaterialApp(home: CreateProfileScreen())));
      await tester.pump(const Duration(milliseconds: 100));

      final invite = find.byWidgetPredicate((w) => w is TextField && w.decoration?.hintText == 'XXXXXX');
      expect(invite, findsOneWidget);
      await tester.ensureVisible(invite);

      const typed = 'FAKE01FAK1';
      for (var i = 1; i <= typed.length; i++) {
        await tester.enterText(invite, typed.substring(0, i));
        await tester.pump(const Duration(milliseconds: 50));
      }
      await tester.enterText(invite, 'EPIC-FAKE01'); // pasted with the prefix
      await tester.pump(const Duration(milliseconds: 300));
    } finally {
      // Restored inside the test body: the framework checks foundation debug variables before tear-downs run.
      debugPrint = previousDebugPrint;
      HttpOverrides.global = previousOverrides;
      await tester.binding.setSurfaceSize(null);
    }

    expect(overrides.seen.where((u) => u.path.contains('validate-invite')), isEmpty);
    expect(logs.where((l) => l.contains('validate-invite')), isEmpty);
  });

  test('the only /validate-invite call is the check made on submission, before any account is created', () {
    final src = _code('lib/presentation/screens/create_profile_screen.dart');
    expect('/validate-invite/'.allMatches(src).length, 1);
    expect(src, contains("Future<bool> _checkInviteCodeValid(String inviteCode) async {\n"
        "    try {\n"
        "      final response = await apiClient.get('/validate-invite/\$inviteCode');"));
    expect(src, isNot(contains('_inviteController.addListener')));
    final check = src.indexOf('final isValid = await _checkInviteCodeValid(inviteCode);');
    final firebase = src.indexOf('createUserWithEmailAndPassword');
    expect(check, greaterThan(0));
    if (firebase >= 0) expect(check, lessThan(firebase));
  });
}
