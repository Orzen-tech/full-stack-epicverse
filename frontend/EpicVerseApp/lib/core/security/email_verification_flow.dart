import 'dart:async';

import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../errors/app_exception.dart';
import '../network/api_client.dart';
import '../../presentation/screens/otp_verification_screen.dart';

/// F-09 H1: email-ownership verification client.
///
/// The server decides everything. `email_verified` is only ever set by an
/// authenticated request whose UID and email come from the Firebase token:
///   * signup: the one-time proof returned by /auth/verify-otp is presented to
///     /auth/mark-verified (see [EmailOtp.verify]);
///   * an existing, signed-in user: [EmailVerificationFlow.run] asks the server
///     to send a code to the account's own email and confirms it.
///
/// Every request here goes through the pinned [apiClient]. The one-time proof,
/// OTPs and challenge ids are kept in memory only: never written to
/// SharedPreferences or disk, never put in a URL, never logged.
typedef EmailVerificationPost = Future<Response<dynamic>> Function(String path, {dynamic data});

EmailVerificationPost? _transportOverride;

/// Tests replace the network with a stub; production always uses [apiClient].
@visibleForTesting
void setEmailVerificationTransportForTest(EmailVerificationPost? transport) =>
    _transportOverride = transport;

Future<Response<dynamic>> _post(String path, {dynamic data}) {
  final override = _transportOverride;
  if (override != null) return override(path, data: data);
  return apiClient.post(path, data: data);
}

/// Result of verifying an emailed code before an account exists.
class EmailOtpResult {
  /// User-facing error, or null on success.
  final String? error;

  /// Memory-only one-time proof ("evp1_..."). Null when the server did not
  /// return one (a pre-H1 backend).
  final String? proof;

  const EmailOtpResult({this.error, this.proof});
}

class EmailOtp {
  EmailOtp._();

  static const proofPrefix = 'evp1_';

  /// Returns the proof only if it is shaped like one; anything else is
  /// ignored rather than trusted.
  @visibleForTesting
  static String? parseProof(dynamic body) {
    final value = body is Map ? body['email_verification_proof'] : null;
    return value is String && value.startsWith(proofPrefix) ? value : null;
  }

  /// POST /auth/verify-otp. On success returns the proof (memory only).
  static Future<EmailOtpResult> verify(String email, String otp) async {
    try {
      final res = await _post('/auth/verify-otp',
          data: FormData.fromMap({'identifier': email, 'otp': otp}));
      return EmailOtpResult(proof: parseProof(res.data));
    } on AppException catch (e) {
      return EmailOtpResult(error: verifyMessage(e));
    }
  }

  /// Authenticated resend (/auth/send-otp). Returns a user-facing error, or
  /// null when a new code was sent.
  static Future<String?> resend(String email) async {
    try {
      await _post('/auth/send-otp', data: FormData.fromMap({'identifier': email}));
      return null;
    } on AppException catch (e) {
      if (e.type == AppExceptionType.rateLimit) {
        return 'Too many code requests. Please wait a few minutes and try again.';
      }
      if (e.type == AppExceptionType.network || e.type == AppExceptionType.timeout) {
        return 'No internet connection. Please try again.';
      }
      return 'Failed to resend code.';
    }
  }

  @visibleForTesting
  static String verifyMessage(AppException e) {
    switch (e.type) {
      case AppExceptionType.rateLimit:
        return 'Too many failed attempts. Please request a new code.';
      case AppExceptionType.network:
      case AppExceptionType.timeout:
        return 'No internet connection. Please try again.';
      case AppExceptionType.server:
        return 'Verification is temporarily unavailable. Please try again.';
      default:
        return 'Invalid or expired verification code. Please try again.';
    }
  }
}

enum EmailChallengeOutcome { sent, alreadyVerified, legacyServer, failed }

class EmailChallengeStart {
  final EmailChallengeOutcome outcome;
  final String? challengeId;
  final String? error;

  const EmailChallengeStart(this.outcome, {this.challengeId, this.error});
}

class EmailVerificationFlow {
  EmailVerificationFlow._();

  static const requestPath = '/auth/email/verify-request';
  static const confirmPath = '/auth/email/verify-confirm';

  /// True only for a genuine "route does not exist" 404 (FastAPI's
  /// `{"detail":"Not Found"}`), i.e. a backend that predates H1. A 404 that
  /// means "no such user" (any other body) is NOT a missing route.
  @visibleForTesting
  static bool isMissingRoute(AppException e) {
    if (e.type != AppExceptionType.notFound) return false;
    final err = e.originalException;
    final data = err is DioException ? err.response?.data : null;
    return data is Map && data['detail'] == 'Not Found';
  }

  /// Verifies the signed-in user's own email. Returns true only when the
  /// server confirms (or reports it was already verified).
  static Future<bool> run(NavigatorState nav) async {
    final user = FirebaseAuth.instance.currentUser;
    final email = user?.email ?? '';
    if (user == null || email.isEmpty) return false;

    final start = await requestChallenge();
    switch (start.outcome) {
      case EmailChallengeOutcome.alreadyVerified:
        return true;
      case EmailChallengeOutcome.legacyServer:
        return _runLegacy(nav, email);
      case EmailChallengeOutcome.failed:
        _snack(nav, start.error ?? 'Could not send a verification code. Please try again.');
        return false;
      case EmailChallengeOutcome.sent:
        break;
    }

    String challengeId = start.challengeId!;
    final ok = await nav.push<bool>(MaterialPageRoute(
      builder: (_) => OtpVerificationScreen(
        email: email,
        onSubmitOtp: (otp) => confirm(challengeId, otp),
        onResend: () async {
          final again = await requestChallenge();
          if (again.outcome == EmailChallengeOutcome.sent) {
            challengeId = again.challengeId!;
            return null;
          }
          if (again.outcome == EmailChallengeOutcome.alreadyVerified) return null;
          return again.error ?? 'Could not send a verification code. Please try again.';
        },
        onVerified: () => nav.pop(true),
        onBack: () => nav.pop(false),
      ),
    ));
    return ok == true;
  }

  /// Pre-H1 backend (the new routes do not exist): the old authenticated
  /// send-otp + verify-otp pair, which marks the account server-side. Reached
  /// only on a genuine missing-route 404, never on any other failure.
  static Future<bool> _runLegacy(NavigatorState nav, String email) async {
    final sendError = await EmailOtp.resend(email);
    if (sendError != null) {
      _snack(nav, sendError);
      return false;
    }
    final ok = await nav.push<bool>(MaterialPageRoute(
      builder: (_) => OtpVerificationScreen(
        email: email,
        onSubmitOtp: (otp) async => (await EmailOtp.verify(email, otp)).error,
        onResend: () => EmailOtp.resend(email),
        onVerified: () => nav.pop(true),
        onBack: () => nav.pop(false),
      ),
    ));
    return ok == true;
  }

  @visibleForTesting
  static Future<EmailChallengeStart> requestChallenge() async {
    try {
      final res = await _post(requestPath);
      final body = res.data;
      if (body is Map && body['status'] == 'already_verified') {
        return const EmailChallengeStart(EmailChallengeOutcome.alreadyVerified);
      }
      final id = body is Map ? body['challenge_id'] : null;
      if (id is String && id.isNotEmpty) {
        return EmailChallengeStart(EmailChallengeOutcome.sent, challengeId: id);
      }
      return const EmailChallengeStart(EmailChallengeOutcome.failed,
          error: 'Could not send a verification code. Please try again.');
    } on AppException catch (e) {
      if (isMissingRoute(e)) return const EmailChallengeStart(EmailChallengeOutcome.legacyServer);
      return EmailChallengeStart(EmailChallengeOutcome.failed, error: startMessage(e));
    }
  }

  /// Returns a user-facing error, or null when the server verified the email.
  @visibleForTesting
  static Future<String?> confirm(String challengeId, String otp) async {
    try {
      await _post(confirmPath,
          data: FormData.fromMap({'challenge_id': challengeId, 'otp': otp}));
      return null;
    } on AppException catch (e) {
      return confirmMessage(e);
    }
  }

  @visibleForTesting
  static String startMessage(AppException e) {
    switch (e.type) {
      case AppExceptionType.rateLimit:
        return 'Too many code requests. Please wait a few minutes and try again.';
      case AppExceptionType.network:
      case AppExceptionType.timeout:
        return 'No internet connection. Please try again.';
      case AppExceptionType.conflict:
        return "This account's email could not be confirmed. Please contact support.";
      case AppExceptionType.notFound:
        return 'Your account could not be found. Please sign in again.';
      default:
        return 'Could not send a verification code. Please try again.';
    }
  }

  @visibleForTesting
  static String confirmMessage(AppException e) {
    switch (e.type) {
      case AppExceptionType.rateLimit:
        return 'Too many failed attempts. Please request a new code.';
      case AppExceptionType.network:
      case AppExceptionType.timeout:
        return 'No internet connection. Please try again.';
      case AppExceptionType.validation:
        return 'Invalid or expired code. Please try again or request a new code.';
      default:
        return 'Verification failed. Please try again.';
    }
  }

  static void _snack(NavigatorState nav, String message) {
    final messenger = ScaffoldMessenger.maybeOf(nav.context);
    messenger?.showSnackBar(SnackBar(content: Text(message), backgroundColor: Colors.redAccent));
  }
}
