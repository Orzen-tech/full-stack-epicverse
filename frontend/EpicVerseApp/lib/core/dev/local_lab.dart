import 'dart:io' show exit;

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/foundation.dart' show debugPrint, kDebugMode;

/// Sets the host, port and host-mapping flag of one Firebase Auth emulator redirect. Injectable so tests never touch the plugin.
typedef AuthEmulatorRedirect = Future<void> Function(String host, int port, {required bool automaticHostMapping});

/// LOCAL-LAB MODE: a debug-only switch for testing the encrypted-auth flows on a phone against the local Docker lab and the Firebase Auth
/// Emulator (see scripts/enc_auth_poc). It exists nowhere else and does nothing in a normal build.
///
/// `active` is true only in a DEBUG build that was started with `--dart-define=EPICVERSE_LOCAL_LAB=true`. `kDebugMode` is a compile-time
/// constant that is false in profile and release builds, so there `active` is a constant `false`, the define is ignored, and every
/// lab-only branch is never executed (the AOT compiler is also expected to drop them; to be confirmed with a profile/release build).
///
/// While active: Firebase Auth is pointed at the emulator before anything else touches it (see [bootstrap]); Sentry is not initialised with
/// a DSN (main.dart); and App Check is neither activated nor queried (main.dart, api_client.dart). The phone reaches the lab over
/// `adb reverse`, so the emulator is `127.0.0.1:9099` from the phone's point of view.
class LocalLab {
  LocalLab._();

  static const bool _requested = bool.fromEnvironment('EPICVERSE_LOCAL_LAB');

  /// The only definition of the lab switch. Never read the define anywhere else.
  static const bool active = kDebugMode && _requested;

  static const String authHost = '127.0.0.1';
  static const int authPort = 9099;

  /// Process exit code used when the redirect cannot be applied (EX_SOFTWARE).
  static const int failureExitCode = 70;

  /// How long the redirect may take before it counts as failed. A timeout is a failure like any other: the process is stopped, and there is
  /// no retry and no fallback to the real Firebase Auth.
  static const Duration redirectTimeout = Duration(seconds: 10);

  /// Call once, right after `Firebase.initializeApp()` and before anything else uses Firebase Auth, and only when [active]
  /// (main.dart: `if (LocalLab.active && !await LocalLab.bootstrap()) return;`, so where `active` is a constant false the call and its await are never executed). Returns true
  /// when startup may go on: lab mode is off (nothing is done), or the redirect was applied. Returns false when the redirect failed or timed
  /// out: the process has been told to stop and the caller must return immediately, without touching Auth, starting a socket or showing a
  /// screen.
  static Future<bool> bootstrap({AuthEmulatorRedirect? redirect, void Function(int code)? fatal}) async {
    if (active) {
      return redirectAuthOrFail(redirect: redirect ?? _redirectFirebaseAuth, fatal: fatal ?? _exitProcess);
    }
    return true;
  }

  /// The redirect and its fail-closed handling, separate from [active] so tests can drive it with fakes. Any failure, including a
  /// `TimeoutException` after [timeout], stops the process via [fatal]; the redirect is attempted exactly once. The message never includes
  /// the exception text.
  static Future<bool> redirectAuthOrFail({
    required AuthEmulatorRedirect redirect,
    required void Function(int code) fatal,
    Duration timeout = redirectTimeout,
  }) async {
    try {
      // false: a physical phone must keep 127.0.0.1 as given. The plugin's default would rewrite it to 10.0.2.2, the Android EMULATOR's alias.
      await redirect(authHost, authPort, automaticHostMapping: false).timeout(timeout);
    } catch (e) {
      debugPrint('[LocalLab] Firebase Auth could not be redirected to the local emulator (${e.runtimeType}); refusing to start');
      fatal(failureExitCode);
      return false;
    }
    debugPrint('[LocalLab] Firebase Auth redirected to the local emulator at $authHost:$authPort');
    return true;
  }

  // The first and only Auth touch in lab mode: the instance is obtained and useAuthEmulator is called in ONE expression, so no Dart code can
  // run in between. ASSUMPTION (not proven by any test, only by reading the pinned plugin sources): the plugin creates its platform object
  // lazily on first use and sends its listener-registration messages in the same synchronous run as the useEmulator message, and the native
  // side handles those messages in order, so the native ID-token listener, which starts token refreshes, is attached only after the
  // redirect. If that assumption is wrong, the only exposure is a token refresh sent before the redirect applies.
  static Future<void> _redirectFirebaseAuth(String host, int port, {required bool automaticHostMapping}) =>
      FirebaseAuth.instance.useAuthEmulator(host, port, automaticHostMapping: automaticHostMapping);

  static void _exitProcess(int code) => exit(code);
}
