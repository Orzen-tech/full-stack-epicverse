import 'dart:async';
import 'dart:io';

import 'package:epicverse/core/dev/local_lab.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';

/// Stage-1 guards for the debug-only local-lab mode. Everything here is offline: the Firebase plugin is never called (the redirect is
/// injected) and nothing touches the network. What these tests cannot prove is the compiled profile/release code; that needs a build and is
/// checked separately.
const bool _requested = bool.fromEnvironment('EPICVERSE_LOCAL_LAB');

/// Source with whole-line `//` comments removed (same convention as the other guard tests).
String _code(String path) => File(path).readAsLinesSync().where((l) => !l.trim().startsWith('//')).join('\n');

String _stripXmlComments(String xml) => xml.replaceAll(RegExp(r'<!--.*?-->', dotAll: true), '');

String _norm(String s) => s.replaceAll(RegExp(r'\s+'), ' ').trim();

const _nsc = 'android/app/src/main/res/xml/network_security_config.xml';
const _debugNsc = 'android/app/src/debug/res/xml/network_security_config.xml';

/// One `<domain-config>`: its opening attributes and the (comment-free) body.
class _DomainConfig {
  _DomainConfig(this.attributes, this.body);
  final String attributes;
  final String body;

  bool get cleartext => attributes.contains('cleartextTrafficPermitted="true"');
  List<String> get domains => RegExp(r'<domain[^>]*>([^<]+)</domain>').allMatches(body).map((m) => m.group(1)!.trim()).toList();
  String get normalized => _norm('$attributes|$body');
}

List<_DomainConfig> _domainConfigs(String path) {
  final xml = _stripXmlComments(File(path).readAsStringSync());
  return RegExp(r'<domain-config([^>]*)>(.*?)</domain-config>', dotAll: true).allMatches(xml).map((m) => _DomainConfig(m.group(1)!, m.group(2)!)).toList();
}

void main() {
  late DebugPrintCallback originalDebugPrint;
  final printedLines = <String>[];

  setUp(() {
    printedLines.clear();
    originalDebugPrint = debugPrint;
    debugPrint = (String? message, {int? wrapWidth}) => printedLines.add(message ?? '');
  });

  tearDown(() {
    debugPrint = originalDebugPrint;
  });

  group('the lab switch', () {
    test('is on only for a debug build started with the define', () {
      expect(LocalLab.active, kDebugMode && _requested);
      if (!_requested) expect(LocalLab.active, isFalse);
    });

    test('profile and release builds can never turn it on, even with the define supplied', () {
      if (kProfileMode || kReleaseMode) expect(LocalLab.active, isFalse);
    });

    test('with the define supplied to a debug run it is on (positive control)', () {
      if (_requested && kDebugMode) expect(LocalLab.active, isTrue);
    });

    test('when it is off, bootstrap does nothing and lets startup continue', () async {
      var redirects = 0, fatals = 0;
      final go = await LocalLab.bootstrap(
        redirect: (h, p, {required automaticHostMapping}) async {
          redirects++;
        },
        fatal: (_) => fatals++,
      );
      expect(go, isTrue);
      expect(<int>[redirects, fatals], <int>[0, 0]);
    }, skip: LocalLab.active ? 'lab mode was requested for this run' : false);
  });

  group('the redirect', () {
    test('goes to 127.0.0.1:9099 with host mapping OFF (a physical phone must not be mapped to 10.0.2.2)', () async {
      final calls = <String>[];
      var fatals = 0;
      final go = await LocalLab.redirectAuthOrFail(
        redirect: (h, p, {required automaticHostMapping}) async {
          calls.add('$h|$p|$automaticHostMapping');
        },
        fatal: (_) => fatals++,
      );
      expect(go, isTrue);
      expect(calls, ['127.0.0.1|9099|false']);
      expect(fatals, 0);
    });

    test('FAIL-CLOSED: when it throws, startup is stopped, the caller is told to return, and nothing after it can run', () async {
      final codes = <int>[];
      var continuedStartup = false;
      // The shape main.dart uses: `if (LocalLab.active && !await LocalLab.bootstrap()) return;` followed by the rest of startup.
      Future<void> startup() async {
        final go = await LocalLab.redirectAuthOrFail(
          redirect: (h, p, {required automaticHostMapping}) async => throw StateError('plugin refused'),
          fatal: codes.add,
        );
        if (!go) return;
        continuedStartup = true; // stands for MfaSessionManager.restore / webSocketService.connect / runApp
      }

      await startup();
      expect(codes, [70], reason: 'the process is told to exit exactly once, with EX_SOFTWARE');
      expect(continuedStartup, isFalse);
    });

    test('FAIL-CLOSED on TIMEOUT: a redirect that never answers stops the process once, tries nothing else, and startup does not continue', () async {
      final codes = <int>[];
      var attempts = 0, continuedStartup = false;
      final neverAnswers = Completer<void>();
      final go = await LocalLab.redirectAuthOrFail(
        redirect: (h, p, {required automaticHostMapping}) {
          attempts++;
          return neverAnswers.future;
        },
        fatal: codes.add,
        timeout: const Duration(milliseconds: 50),
      );
      if (go) continuedStartup = true;
      expect(go, isFalse);
      expect(codes, [70]);
      expect(attempts, 1, reason: 'one attempt only: no retry and no second route (certainly not the real Firebase Auth)');
      expect(continuedStartup, isFalse);
      expect(printedLines.join('\n'), contains('TimeoutException'));

      // a late answer after the timeout changes nothing: no second fatal call, no unhandled error
      neverAnswers.complete();
      await Future<void>.delayed(const Duration(milliseconds: 20));
      expect(codes, [70]);
    });

    test('a redirect that fails after a delay but inside the timeout is a plain failure, not a timeout', () async {
      final codes = <int>[];
      final go = await LocalLab.redirectAuthOrFail(
        redirect: (h, p, {required automaticHostMapping}) async {
          await Future<void>.delayed(const Duration(milliseconds: 10));
          throw StateError('late refusal');
        },
        fatal: codes.add,
        timeout: const Duration(seconds: 5),
      );
      expect(go, isFalse);
      expect(codes, [70]);
      expect(printedLines.join('\n'), contains('StateError'));
      expect(printedLines.join('\n'), isNot(contains('TimeoutException')));
    });

    test('a redirect that answers inside the timeout succeeds', () async {
      final codes = <int>[];
      final go = await LocalLab.redirectAuthOrFail(
        redirect: (h, p, {required automaticHostMapping}) async {
          await Future<void>.delayed(const Duration(milliseconds: 10));
        },
        fatal: codes.add,
        timeout: const Duration(seconds: 5),
      );
      expect(go, isTrue);
      expect(codes, isEmpty);
    });

    test('a redirect that throws synchronously, before it returns a future, is still a failure', () async {
      final codes = <int>[];
      final go = await LocalLab.redirectAuthOrFail(
        redirect: (h, p, {required automaticHostMapping}) => throw StateError('no Firebase app'),
        fatal: codes.add,
      );
      expect(go, isFalse);
      expect(codes, [70]);
    });

    test('the default timeout is bounded (10 s)', () {
      expect(LocalLab.redirectTimeout, const Duration(seconds: 10));
      expect(LocalLab.redirectTimeout, greaterThan(Duration.zero));
    });

    test('the failure message carries the exception type only, never its text', () async {
      await LocalLab.redirectAuthOrFail(
        redirect: (h, p, {required automaticHostMapping}) async => throw Exception('SECRET-MARKER-123'),
        fatal: (_) {},
      );
      final all = printedLines.join('\n');
      expect(all, contains('could not be redirected'));
      expect(all, contains('_Exception'));
      expect(all, isNot(contains('SECRET-MARKER-123')));
    });
  });

  group('source guards: gating', () {
    test('`active` is `kDebugMode && <the define>` and nothing else', () {
      final src = _code('lib/core/dev/local_lab.dart');
      expect(src, contains("static const bool _requested = bool.fromEnvironment('EPICVERSE_LOCAL_LAB');"));
      expect(src, contains('static const bool active = kDebugMode && _requested;'));
    });

    test('the define is read in exactly one place in lib/', () {
      final hits = <String>[];
      for (final f in Directory('lib').listSync(recursive: true).whereType<File>().where((f) => f.path.endsWith('.dart'))) {
        final n = 'EPICVERSE_LOCAL_LAB'.allMatches(_code(f.path)).length;
        if (n > 0) hits.add('${f.path.replaceAll(r'\', '/')}:$n');
      }
      expect(hits, ['lib/core/dev/local_lab.dart:1']);
    });

    test('lab-only code is reached only through `LocalLab.active`/`bootstrap`, which main.dart and api_client.dart use', () {
      final main = _code('lib/main.dart');
      final client = _code('lib/core/network/api_client.dart');
      expect(main, contains("options.dsn = LocalLab.active ? '' : 'https://"));
      expect(main, contains('if (!LocalLab.active) {\n        try {'));
      expect(main.indexOf('if (!LocalLab.active) {'), lessThan(main.indexOf('FirebaseAppCheck.instance.activate')));
      expect(client, contains('if (!LocalLab.active) {\n            try {'));
      expect(client.indexOf('if (!LocalLab.active) {'), lessThan(client.indexOf('FirebaseAppCheck.instance')));
      // existing guard-test anchors are intact
      expect(client, contains('X-Firebase-AppCheck'));
      expect(RegExp(r'sendDefaultPii\s*=\s*false').hasMatch(main), isTrue);
      expect(main, contains('tracesSampleRate = 1.0'));
    });
  });

  group('source guards: the first Firebase Auth touch', () {
    test('local_lab.dart touches Auth once, as ONE expression with nothing awaited between the instance and the redirect', () {
      final src = _code('lib/core/dev/local_lab.dart');
      expect('FirebaseAuth.instance'.allMatches(src).length, 1);
      expect(
        RegExp(r'FirebaseAuth\.instance\s*\.useAuthEmulator\(\s*host,\s*port,\s*automaticHostMapping:\s*automaticHostMapping\s*\)').hasMatch(src),
        isTrue,
      );
      expect(RegExp(r'FirebaseAuth\.instance[^;]*\bawait\b').hasMatch(src), isFalse);
      expect(src, contains('await redirect(authHost, authPort, automaticHostMapping: false).timeout(timeout);'));
    });

    test('main.dart never names FirebaseAuth, so nothing there can touch Auth before the redirect', () {
      expect(_code('lib/main.dart').contains('FirebaseAuth'), isFalse);
    });
  });

  group('source guards: startup order in main.dart', () {
    test('initializeApp -> bootstrap -> MFA restore -> App Check -> socket -> runApp', () {
      final code = _code('lib/main.dart');
      final at = <String, int>{
        'initializeApp': code.indexOf('Firebase.initializeApp()'),
        'bootstrap': code.indexOf('LocalLab.bootstrap()'),
        'mfa restore': code.indexOf('MfaSessionManager.restore()'),
        'app check': code.indexOf('FirebaseAppCheck.instance.activate'),
        'socket': code.indexOf('webSocketService.connect()'),
        'runApp': code.indexOf('runApp('),
      };
      expect(at.values.every((i) => i >= 0), isTrue, reason: '$at');
      final order = at.values.toList();
      for (var i = 1; i < order.length; i++) {
        expect(order[i], greaterThan(order[i - 1]), reason: '${at.keys.elementAt(i)} must come after ${at.keys.elementAt(i - 1)}');
      }
      // Gated at the call site, so a build where `active` is a constant false drops the call and its await entirely.
      expect(code, contains('if (LocalLab.active && !await LocalLab.bootstrap()) return;'));
      expect(code, isNot(contains('if (!await LocalLab.bootstrap())')));
      expect('LocalLab.bootstrap()'.allMatches(code).length, 1);
    });

    test('bootstrap sits OUTSIDE the try/catch that swallows a failed initializeApp', () {
      final code = _code('lib/main.dart');
      final tryStart = code.lastIndexOf('try {', code.indexOf('Firebase.initializeApp()'));
      final upToBootstrap = code.substring(tryStart, code.indexOf('LocalLab.bootstrap()'));
      expect('{'.allMatches(upToBootstrap).length, '}'.allMatches(upToBootstrap).length, reason: 'the try/catch must be closed before bootstrap');
    });
  });

  group('source guards: Android network security', () {
    test('production file allows no cleartext at all', () {
      expect(_domainConfigs(_nsc).where((d) => d.cleartext), isEmpty);
      expect(_stripXmlComments(File(_nsc).readAsStringSync()), isNot(contains('cleartextTrafficPermitted="true"')));
    });

    test('debug overlay allows cleartext for exactly 127.0.0.1 and localhost, once', () {
      final cleartext = _domainConfigs(_debugNsc).where((d) => d.cleartext).toList();
      expect(cleartext, hasLength(1));
      expect(cleartext.single.domains, unorderedEquals(['127.0.0.1', 'localhost']));
      expect(cleartext.single.body, isNot(contains('includeSubdomains="true"')));
    });

    test('everything else in the debug overlay is the production configuration, pins included', () {
      final production = _domainConfigs(_nsc).map((d) => d.normalized).toList();
      final overlay = _domainConfigs(_debugNsc).where((d) => !d.cleartext).map((d) => d.normalized).toList();
      expect(overlay, production);
      expect(production, isNotEmpty);
    });

    test('the overlay exists only for debug builds; no manifest enables cleartext globally', () {
      expect(File('android/app/src/profile/res/xml/network_security_config.xml').existsSync(), isFalse);
      expect(File('android/app/src/release/res/xml/network_security_config.xml').existsSync(), isFalse);
      for (final m in ['main', 'debug', 'profile']) {
        expect(File('android/app/src/$m/AndroidManifest.xml').readAsStringSync(), isNot(contains('usesCleartextTraffic')), reason: m);
      }
      expect(File('android/app/src/main/AndroidManifest.xml').readAsStringSync(), contains('android:networkSecurityConfig="@xml/network_security_config"'));
    });
  });
}
