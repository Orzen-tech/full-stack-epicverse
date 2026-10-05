import 'dart:async';

import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../errors/app_exception.dart';
import '../network/api_client.dart';
import '../network/mfa_session_manager.dart';
import '../../presentation/screens/otp_verification_screen.dart';

/// Server-authoritative security state for the signed-in user.
class SessionStatus {
  final bool emailVerified;
  final bool mfaEnabled;
  final bool mfaSessionValid;

  const SessionStatus({
    required this.emailVerified,
    required this.mfaEnabled,
    required this.mfaSessionValid,
  });
}

/// F-09 client flows. The backend decides everything; this only asks it,
/// shows the OTP UI, and carries the resulting session token.
class MfaFlow {
  MfaFlow._();

  static const loginChallengePath = '/auth/mfa/challenge';
  static const loginVerifyPath = '/auth/mfa/verify';
  static const enableRequestPath = '/user/mfa/enable-request';
  static const enableConfirmPath = '/user/mfa/enable-confirm';

  /// Returns null only when the backend has no such route at all (rolled
  /// back to a pre-F-09 revision); every other failure is rethrown.
  static Future<SessionStatus?> fetchSessionStatus() async {
    try {
      final res = await apiClient.get('/auth/session-status');
      final d = Map<String, dynamic>.from(res.data as Map);
      return SessionStatus(
        emailVerified: d['email_verified'] == true,
        mfaEnabled: d['mfa_enabled'] == true,
        mfaSessionValid: d['mfa_session_valid'] == true,
      );
    } on AppException catch (e) {
      if (e.type == AppExceptionType.notFound && _isMissingRoute(e)) return null;
      rethrow;
    }
  }

  static bool _isMissingRoute(AppException e) {
    final err = e.originalException;
    final data = err is DioException ? err.response?.data : null;
    return data is Map && data['detail'] == 'Not Found';
  }

  /// Runs an OTP challenge and returns true only if the backend issued an
  /// MFA session for the current Firebase user.
  static Future<bool> runChallenge(
    NavigatorState nav, {
    required String challengePath,
    required String verifyPath,
  }) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) return false;
    String? challengeId;
    final startError = await _requestChallenge(challengePath, (id) => challengeId = id);
    if (startError != null) {
      _snack(nav, startError);
      return false;
    }
    if (challengeId == null) return false;

    final ok = await nav.push<bool>(MaterialPageRoute(
      builder: (_) => OtpVerificationScreen(
        email: user.email ?? '',
        isMfaVerification: true,
        onSubmitOtp: (otp) => _verify(verifyPath, challengeId!, otp, user.uid),
        onResend: () => _requestChallenge(challengePath, (id) => challengeId = id),
        onVerified: () => nav.pop(true),
        onBack: () => nav.pop(false),
      ),
    ));
    return ok == true && MfaSessionManager.getSessionForUid(user.uid) != null;
  }

  static Future<bool> verifyLogin(NavigatorState nav) =>
      runChallenge(nav, challengePath: loginChallengePath, verifyPath: loginVerifyPath);

  static Future<bool>? _reprompt;

  /// One shared prompt even if several requests hit MFA_SESSION_REQUIRED at once.
  static Future<bool> reprompt(NavigatorState nav) =>
      _reprompt ??= verifyLogin(nav).whenComplete(() => _reprompt = null);

  /// Best-effort server revocation, then local clear and Firebase sign-out.
  /// Signing out always succeeds locally even if the backend is unreachable.
  static Future<void> signOut() async {
    if (MfaSessionManager.currentSession() != null) {
      try {
        await apiClient.post('/auth/mfa/logout').timeout(const Duration(seconds: 5));
      } catch (_) {}
    }
    MfaSessionManager.clear();
    await FirebaseAuth.instance.signOut();
  }

  static Future<String?> _requestChallenge(String path, void Function(String) onId) async {
    try {
      final res = await apiClient.post(path);
      final id = res.data is Map ? res.data['challenge_id'] : null;
      if (id is String && id.isNotEmpty) onId(id);
      return null;
    } on AppException catch (e) {
      return _message(e, sending: true);
    }
  }

  static Future<String?> _verify(String path, String challengeId, String otp, String uid) async {
    try {
      final res = await apiClient.post(path,
          data: FormData.fromMap({'challenge_id': challengeId, 'otp': otp}));
      final token = res.data is Map ? res.data['mfa_session_token'] : null;
      if (token is! String || token.isEmpty) return 'Verification failed. Please try again.';
      MfaSessionManager.setSession(uid, token);
      return null;
    } on AppException catch (e) {
      return _message(e, sending: false);
    }
  }

  static String _message(AppException e, {required bool sending}) {
    switch (e.type) {
      case AppExceptionType.rateLimit:
        return sending
            ? 'Too many code requests. Please wait a few minutes and try again.'
            : 'Too many attempts. Please request a new code.';
      case AppExceptionType.validation:
        return 'Invalid or expired code. Please try again or request a new code.';
      case AppExceptionType.network:
      case AppExceptionType.timeout:
        return 'No internet connection. Please try again.';
      case AppExceptionType.authorization:
        return 'Please verify your email address first.';
      default:
        return sending
            ? 'Could not send a verification code. Please try again.'
            : 'Verification failed. Please try again.';
    }
  }

  static void _snack(NavigatorState nav, String message) {
    final messenger = ScaffoldMessenger.maybeOf(nav.context);
    messenger?.showSnackBar(SnackBar(content: Text(message), backgroundColor: Colors.redAccent));
  }
}
