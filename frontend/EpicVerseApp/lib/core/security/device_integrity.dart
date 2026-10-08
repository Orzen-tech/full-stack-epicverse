import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show MethodChannel;
import 'package:flutter_jailbreak_detection/flutter_jailbreak_detection.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

/// Finding #4: one place that decides whether this device looks rooted,
/// jailbroken or instrumented.
///
///  * [compromised] a confirmed signal; callers block with the Security Alert.
///  * [safe]        every available source answered "no".
///  * [unknown]     a source failed or timed out and nothing was positive.
///                  Callers keep normal access (an OS/plugin glitch must not
///                  lock a legitimate user out), but it is never reported as
///                  safe and is simply checked again at the next boundary.
enum IntegrityState { safe, compromised, unknown }

@immutable
class IntegrityResult {
  const IntegrityResult(this.state, {this.reasons = const [], this.weakSignals = const []});

  final IntegrityState state;

  /// Fixed machine codes (e.g. `jailbreak`, `dyld`) behind a non-safe result.
  final List<String> reasons;

  /// Indicators that are too weak to block on (diagnostics only).
  final List<String> weakSignals;
}

enum IntegrityPlatform { android, ios, other }

/// Existing detection is kept: `flutter_jailbreak_detection` on both platforms
/// (RootBeer on Android, IOSSecuritySuite.amIJailbroken() on iOS) and the
/// Android TracerPid check. iOS adds native instrumentation signals from
/// `AppDelegate.swift` (`integritySignals`).
class DeviceIntegrity {
  DeviceIntegrity._();

  static const _channel = MethodChannel('epicverse/integrity');
  static final _code = RegExp(r'^[a-z0-9_]{1,32}$');

  static IntegrityResult? _compromised; // sticky for this process
  static Future<IntegrityResult>? _inFlight; // only simultaneous callers share a probe
  static final Set<String> _reported = {};

  /// Checks the device. Simultaneous callers share one probe; a finished
  /// probe is never reused, so login and signup each get a fresh check. A
  /// confirmed compromise is remembered for the life of the process.
  static Future<IntegrityResult> check() {
    final sticky = _compromised;
    if (sticky != null) return Future.value(sticky);
    return _inFlight ??= _run().whenComplete(() => _inFlight = null);
  }

  static Future<IntegrityResult> _run() async {
    IntegrityResult result;
    try {
      result = await probe();
    } catch (_) {
      result = const IntegrityResult(IntegrityState.unknown, reasons: ['probe_error']);
    }
    if (result.state == IntegrityState.compromised) _compromised = result;
    _report(result);
    return result;
  }

  // ---- diagnostics (privacy-safe, at most once per process per outcome) ----

  static void _report(IntegrityResult r) {
    if (isDebugMode()) return;
    final noisy = r.state != IntegrityState.safe || r.weakSignals.isNotEmpty;
    if (!noisy) return;
    final key = '${r.state.name}|${r.reasons.join(",")}|${r.weakSignals.join(",")}';
    if (!_reported.add(key)) return;
    try {
      reporter(r);
    } catch (_) {}
  }

  static void _sentryReporter(IntegrityResult r) {
    Sentry.captureMessage(
      'device_integrity',
      level: SentryLevel.info,
      withScope: (scope) {
        scope.setTag('integrity_state', r.state.name);
        if (r.reasons.isNotEmpty) scope.setTag('integrity_reasons', r.reasons.join(','));
        if (r.weakSignals.isNotEmpty) scope.setTag('integrity_weak', r.weakSignals.join(','));
      },
    );
  }

  // ---- platform probe ----

  static Future<IntegrityResult> _platformProbe() async {
    switch (platform()) {
      case IntegrityPlatform.android:
        return _androidProbe();
      case IntegrityPlatform.ios:
        return _iosProbe();
      case IntegrityPlatform.other:
        return const IntegrityResult(IntegrityState.safe);
    }
  }

  /// Same algorithm as before: plugin jailbreak/root check, plus TracerPid
  /// outside debug builds.
  static Future<IntegrityResult> _androidProbe() async {
    final jb = await _source<bool>(pluginJailbroken);
    final tracer = isDebugMode() ? const _Source<int?>.skipped() : await _source<int?>(androidTracerPid);

    final reasons = <String>[];
    if (jb.ok && jb.value == true) reasons.add('jailbreak');
    if (tracer.ok && (tracer.value ?? -1) > 0) reasons.add('tracer_pid');
    if (reasons.isNotEmpty) return IntegrityResult(IntegrityState.compromised, reasons: reasons);

    final failed = <String>[
      if (jb.failed) 'jailbreak_check_failed',
      if (tracer.failed || (tracer.ok && tracer.value == null)) 'tracer_check_failed',
    ];
    if (failed.isNotEmpty) return IntegrityResult(IntegrityState.unknown, reasons: failed);
    return const IntegrityResult(IntegrityState.safe);
  }

  /// Existing plugin check plus the native instrumentation signals. Only
  /// specific indicators (injected Frida/Cycript libraries, frida-server file,
  /// Frida's own port, an attached debugger on a release build) are positive.
  /// Generic local-port and experimental indicators are diagnostics only.
  static Future<IntegrityResult> _iosProbe() async {
    final jb = await _source<bool>(pluginJailbroken);
    final signals = await _source<Map<Object?, Object?>?>(() => iosSignals(isReleaseMode()));

    final reasons = <String>[];
    var weak = const <String>[];
    var signalsOk = false;

    if (jb.ok && jb.value == true) reasons.add('jailbreak');

    final map = signals.ok ? signals.value : null;
    if (map != null) {
      final strong = _codes(map['strong']);
      final weakCodes = _codes(map['weak']);
      final debugged = map['debugged'];
      if (strong != null && weakCodes != null && debugged is bool) {
        signalsOk = true;
        reasons.addAll(strong);
        if (debugged) reasons.add('debugger');
        weak = weakCodes;
      }
    }

    if (reasons.isNotEmpty) {
      return IntegrityResult(IntegrityState.compromised, reasons: reasons, weakSignals: weak);
    }
    final failed = <String>[
      if (jb.failed) 'jailbreak_check_failed',
      if (!signalsOk) 'native_signals_failed',
    ];
    if (failed.isNotEmpty) {
      return IntegrityResult(IntegrityState.unknown, reasons: failed, weakSignals: weak);
    }
    return IntegrityResult(IntegrityState.safe, weakSignals: weak);
  }

  /// Accepts only a list of short machine codes; anything else is rejected so
  /// arbitrary native text can never reach logs or telemetry.
  static List<String>? _codes(Object? raw) {
    if (raw is! List || raw.length > 8) return null;
    final out = <String>[];
    for (final item in raw) {
      if (item is! String || !_code.hasMatch(item)) return null;
      out.add(item);
    }
    return out;
  }

  static Future<_Source<T>> _source<T>(Future<T> Function() read) async {
    try {
      return _Source<T>.ok(await read().timeout(sourceTimeout));
    } catch (_) {
      return _Source<T>.failed();
    }
  }

  // ---- injectable seams (defaults are the real implementations) ----

  @visibleForTesting
  static Future<IntegrityResult> Function() probe = _platformProbe;
  @visibleForTesting
  static IntegrityPlatform Function() platform = _currentPlatform;
  @visibleForTesting
  static bool Function() isDebugMode = () => kDebugMode;
  @visibleForTesting
  static bool Function() isReleaseMode = () => kReleaseMode;
  @visibleForTesting
  static Duration sourceTimeout = const Duration(seconds: 2);
  @visibleForTesting
  static Future<bool> Function() pluginJailbroken = () => FlutterJailbreakDetection.jailbroken;
  @visibleForTesting
  static Future<int?> Function() androidTracerPid = () => _channel.invokeMethod<int>('tracerPid');
  @visibleForTesting
  static Future<Map<Object?, Object?>?> Function(bool evaluateDebugger) iosSignals =
      (evaluateDebugger) => _channel
          .invokeMethod<Map<Object?, Object?>>('integritySignals', {'evaluateDebugger': evaluateDebugger});
  @visibleForTesting
  static void Function(IntegrityResult) reporter = _sentryReporter;

  static IntegrityPlatform _currentPlatform() {
    switch (defaultTargetPlatform) {
      case TargetPlatform.android:
        return IntegrityPlatform.android;
      case TargetPlatform.iOS:
        return IntegrityPlatform.ios;
      default:
        return IntegrityPlatform.other;
    }
  }

  /// Restores real implementations and clears process state (tests only).
  @visibleForTesting
  static void resetForTest() {
    _compromised = null;
    _inFlight = null;
    _reported.clear();
    probe = _platformProbe;
    platform = _currentPlatform;
    isDebugMode = () => kDebugMode;
    isReleaseMode = () => kReleaseMode;
    sourceTimeout = const Duration(seconds: 2);
    pluginJailbroken = () => FlutterJailbreakDetection.jailbroken;
    androidTracerPid = () => _channel.invokeMethod<int>('tracerPid');
    iosSignals = (evaluateDebugger) => _channel
        .invokeMethod<Map<Object?, Object?>>('integritySignals', {'evaluateDebugger': evaluateDebugger});
    reporter = _sentryReporter;
  }
}

class _Source<T> {
  const _Source.ok(this.value) : ok = true, failed = false;
  const _Source.failed() : value = null, ok = false, failed = true;
  const _Source.skipped() : value = null, ok = false, failed = false;

  final T? value;
  final bool ok;
  final bool failed;
}
