import 'package:dio/dio.dart' show DioException;
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/widgets.dart';

import '../errors/app_exception.dart';
import '../errors/error_mapper.dart';
import '../network/api_client.dart';
import '../network/mfa_reprompt.dart';
import 'email_verification_flow.dart';
import 'mfa_flow.dart';

/// What the splash screen does when `GET /user/{uid}` did not give a usable profile.
/// None of these opens the Dashboard: start-up fails closed.
enum SplashFailure {
  /// 404: the profile was never created (incomplete signup). Back to Welcome, as before.
  incompleteSignup,

  /// 401 (or a sign-in too old for the server): the sign-in is no longer valid, e.g. expired or revoked
  /// tokens. Sign out locally and go to Welcome.
  signedOut,

  /// The server needs an MFA session and the re-prompt did not produce one. Blocked until verified.
  mfaRequired,

  /// A 403 that carries MFA_SESSION_REQUIRED. ApiClient re-prompts only the 401 form, so the splash asks once.
  mfaRepromptNeeded,

  /// Any other 403. Blocked (retry or sign out); not an automatic sign-out.
  blocked,

  /// 429: retry after the server's cool-down.
  rateLimited,

  /// Timeout, no network, 5xx, malformed data or an unexpected error: retry screen, sign-in kept.
  retry,
}

/// Maps a profile-fetch failure to the splash decision.
SplashFailure classifyProfileFailure(Object error) {
  if (error is! AppException) return SplashFailure.retry;
  if (error.statusCode == 404) return SplashFailure.incompleteSignup;
  switch (error.type) {
    case AppExceptionType.mfaSessionRequired:
      return SplashFailure.mfaRequired; // ApiClient already ran the re-prompt for this (401) form
    case AppExceptionType.authentication:
    case AppExceptionType.recentSignInRequired:
      return SplashFailure.signedOut;
    case AppExceptionType.authorization:
      final original = error.originalException;
      final code = original is DioException ? ErrorMapper.backendCode(original) : null;
      return code == 'MFA_SESSION_REQUIRED' ? SplashFailure.mfaRepromptNeeded : SplashFailure.blocked;
    case AppExceptionType.rateLimit:
      return SplashFailure.rateLimited;
    default:
      return SplashFailure.retry;
  }
}

/// The profile only counts as a successful sign-in check when it is an HTTP 200 JSON object that does not
/// belong to a different account. Throws [FormatException] otherwise (messages never contain user data).
Map<String, dynamic> validatedProfile(int? statusCode, Object? data, String uid) {
  if (statusCode != 200) throw FormatException('unexpected profile status $statusCode');
  if (data is! Map) throw const FormatException('profile is not a JSON object');
  final profile = Map<String, dynamic>.from(data);
  final id = profile['uid'] ?? profile['id'];
  if (id != null && id != uid) throw const FormatException('profile does not match the signed-in account');
  return profile;
}

/// Everything the splash sign-in check uses from outside. Production uses [production]; tests replace it.
class SplashAuthDeps {
  const SplashAuthDeps({
    required this.currentUid,
    required this.client,
    required this.probeSessionStatus,
    required this.verifyEmail,
    required this.verifyMfa,
    required this.signOut,
    required this.reprompt,
    this.dashboard,
    this.welcome,
  });

  final String? Function() currentUid;
  final ApiClient Function() client;
  final Future<SessionProbe> Function() probeSessionStatus;
  final Future<bool> Function(NavigatorState nav) verifyEmail;
  final Future<MfaChallengeResult> Function(NavigatorState nav) verifyMfa;

  /// Local sign-out that never revokes the account's other sessions.
  final Future<void> Function() signOut;

  /// The app's MFA re-prompt (true only when a new MFA session was issued).
  final Future<bool> Function() reprompt;

  /// Destination screens; null means the real ones.
  final Widget Function()? dashboard;
  final Widget Function()? welcome;

  static SplashAuthDeps get production => SplashAuthDeps(
        currentUid: () => FirebaseAuth.instance.currentUser?.uid,
        client: () => apiClient,
        probeSessionStatus: MfaFlow.probeSessionStatus,
        verifyEmail: EmailVerificationFlow.run,
        verifyMfa: MfaFlow.verifyLoginDetailed,
        signOut: () => MfaFlow.signOut(revokeServer: false),
        reprompt: () async {
          final handler = MfaReprompt.handler;
          return handler == null ? false : await handler();
        },
      );
}
