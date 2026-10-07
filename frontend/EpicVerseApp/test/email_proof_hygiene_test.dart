import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Static guards for F-09 H1. The one-time email proof is a bearer secret: it
/// must stay in memory, off disk, out of logs and URLs, and travel only over
/// the pinned client. These tests read the source so a careless edit fails CI.
List<File> _dartFiles() => Directory('lib')
    .listSync(recursive: true)
    .whereType<File>()
    .where((f) => f.path.endsWith('.dart'))
    .toList();

Iterable<MapEntry<int, String>> _codeLines(File f) sync* {
  final lines = f.readAsLinesSync();
  for (var i = 0; i < lines.length; i++) {
    final t = lines[i].trim();
    if (t.startsWith('//') || t.startsWith('///')) continue;
    yield MapEntry(i + 1, lines[i]);
  }
}

void main() {
  test('the proof is never persisted, logged, or put in a URL', () {
    final proofish = RegExp(r'proof', caseSensitive: false);
    final sinks = RegExp(
        r'SharedPreferences|prefs\.|writeAsString|writeAsBytes|FlutterSecureStorage|'
        r'debugPrint\(|[^a-zA-Z]print\(|Sentry\.|queryParameters|Uri\.|\.log\(');
    final offenders = <String>[];
    for (final f in _dartFiles()) {
      // The logger legitimately mentions "proof": it is the code that REDACTS it.
      if (f.path.endsWith('core/services/logger_service.dart')) continue;
      for (final line in _codeLines(f)) {
        if (proofish.hasMatch(line.value) && sinks.hasMatch(line.value)) {
          offenders.add('${f.path}:${line.key}: ${line.value.trim()}');
        }
      }
    }
    expect(offenders, isEmpty, reason: offenders.join('\n'));
  });

  test('verification endpoints are never called with a query string', () {
    final offenders = <String>[];
    final paths = RegExp(r"'/auth/(verify-otp|mark-verified|send-otp|email/[a-z\-]+)[^']*'");
    for (final f in _dartFiles()) {
      for (final line in _codeLines(f)) {
        for (final m in paths.allMatches(line.value)) {
          if (m.group(0)!.contains('?')) offenders.add('${f.path}:${line.key}');
        }
      }
    }
    expect(offenders, isEmpty);
  });

  test('H1 screens and services use the pinned apiClient, never a plain Dio()', () {
    const h1Files = [
      'lib/presentation/screens/create_profile_screen.dart',
      'lib/presentation/screens/otp_verification_screen.dart',
      'lib/presentation/screens/login_screen.dart',
      'lib/core/security/email_verification_flow.dart',
    ];
    for (final path in h1Files) {
      final src = File(path).readAsStringSync();
      expect(RegExp(r'\bDio\(').hasMatch(src), isFalse, reason: '$path creates an unpinned Dio()');
    }
  });

  test('every email OTP screen supplies its own pinned verification callback', () {
    final file = File('lib/presentation/screens/otp_verification_screen.dart');
    // Code only: an explanatory comment may still mention the endpoint names.
    final code = _codeLines(file).map((l) => l.value).join('\n');
    expect(code.contains('/auth/verify-otp'), isFalse, reason: 'default email branch must stay removed');
    expect(code.contains('/auth/send-otp'), isFalse, reason: 'default email resend must stay removed');
    expect(code.contains('assert(phone != null || onSubmitOtp != null'), isTrue);
  });

  test('the proof is only ever a widget/state field or a local, never a static or global', () {
    final offenders = <String>[];
    final global = RegExp(r'^\s*(static\s+)?(final\s+|var\s+)?String\??\s+_?\w*[pP]roof\w*\s*(=|;)');
    for (final f in _dartFiles()) {
      for (final line in _codeLines(f)) {
        if (line.value.contains('static') && global.hasMatch(line.value)) {
          offenders.add('${f.path}:${line.key}');
        }
      }
    }
    expect(offenders, isEmpty, reason: offenders.join('\n'));
  });
}
