import 'dart:async';
import 'dart:io';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/services.dart';
import 'app_exception.dart';
import '../network/mfa_session_manager.dart';
import '../services/network_service.dart';

class ErrorMapper {
  /// The backend's structured error code (`{"detail": {"code": ...}}`), if any.
  static String? backendCode(DioException error) {
    final data = error.response?.data;
    final detail = data is Map ? data['detail'] : null;
    return detail is Map && detail['code'] is String ? detail['code'] as String : null;
  }

  /// True when the failure means the phone itself has no usable internet (as opposed to
  /// "internet works but EpicVerse can't be reached").
  static bool _looksOffline(Object? cause) {
    if (networkService.currentStatus == NetworkStatus.noInternet) return true;
    if (cause is SocketException) {
      final text = '${cause.message} ${cause.osError?.message ?? ''}'.toLowerCase();
      return text.contains('failed host lookup') ||
          text.contains('network is unreachable') ||
          text.contains('no address associated');
    }
    return false;
  }

  static AppException _offlineOrUnreachable(Object? cause, {String? originalError, DioException? original}) {
    final offline = _looksOffline(cause);
    return AppException(
      userMessage: offline ? ErrorMessages.offline : ErrorMessages.unreachable,
      type: AppExceptionType.network,
      originalError: originalError,
      originalException: original ?? cause,
      retryable: true,
    );
  }

  /// Converts any raw technical exception into a clean, user-friendly [AppException].
  static AppException fromException(dynamic exception) {
    if (exception is AppException) {
      return exception;
    }

    // 1. Handle Dio HTTP Client Exceptions
    if (exception is DioException) {
      return _mapDioException(exception);
    }

    // 2. Handle Firebase Auth Exceptions
    if (exception is FirebaseAuthException) {
      return _mapFirebaseAuthException(exception);
    }

    // 3. Handle Platform Exceptions
    if (exception is PlatformException) {
      return _mapPlatformException(exception);
    }

    // 4. Handle Network & Socket Exceptions
    if (exception is SocketException) {
      return _offlineOrUnreachable(exception, originalError: exception.message);
    }

    // TLS failures (e.g. a sign-in Wi-Fi page or proxy in the way)
    if (exception is HandshakeException || exception is TlsException) {
      return AppException(
        userMessage: ErrorMessages.secureConnection,
        type: AppExceptionType.network,
        originalError: exception.toString(),
        originalException: exception,
        retryable: true,
      );
    }

    // 5. Handle Async Timeout Exceptions
    if (exception is TimeoutException) {
      return const AppException(
        userMessage: ErrorMessages.timeout,
        type: AppExceptionType.timeout,
        retryable: true,
      );
    }

    // 6. Handle Parsing & Type Exceptions
    if (exception is FormatException || exception is TypeError) {
      return AppException(
        userMessage: ErrorMessages.generic,
        originalError: exception.toString(),
        type: AppExceptionType.unknown,
        retryable: true,
      );
    }

    // 7. Generic Fallback for Unknown Exceptions
    return AppException(
      userMessage: ErrorMessages.generic,
      originalError: exception?.toString(),
      type: AppExceptionType.unknown,
      originalException: exception,
      retryable: true,
    );
  }

  static AppException _mapPlatformException(PlatformException exception) {
    switch (exception.code) {
      case 'camera_access_denied':
        return AppException(
          userMessage: 'Camera access was denied. Please allow camera access to take a photo.',
          originalError: exception.message,
          type: AppExceptionType.authorization,
          originalException: exception,
        );
      default:
        // Never show a platform plugin's raw message to the user.
        return AppException(
          userMessage: ErrorMessages.generic,
          originalError: exception.message,
          type: AppExceptionType.unknown,
          originalException: exception,
          retryable: true,
        );
    }
  }

  static AppException _mapDioException(DioException error) {
    final statusCode = error.response?.statusCode;

    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        return AppException(
          userMessage: ErrorMessages.timeout,
          statusCode: statusCode,
          type: AppExceptionType.timeout,
          originalError: error.message,
          originalException: error,
          retryable: true,
        );

      case DioExceptionType.connectionError:
        if (error.error is HandshakeException || error.error is TlsException) {
          return AppException(
            userMessage: ErrorMessages.secureConnection,
            type: AppExceptionType.network,
            originalError: error.message,
            originalException: error,
            retryable: true,
          );
        }
        return _offlineOrUnreachable(error.error, originalError: error.message, original: error);

      case DioExceptionType.badResponse:
        return _mapHttpStatusCode(statusCode, error);

      case DioExceptionType.cancel:
        return const AppException(
          userMessage: ErrorMessages.cancelled,
          type: AppExceptionType.unknown,
        );

      case DioExceptionType.badCertificate:
        return AppException(
          userMessage: ErrorMessages.secureConnection,
          type: AppExceptionType.network,
          originalError: error.message,
          originalException: error,
          retryable: true,
        );

      case DioExceptionType.unknown:
      default:
        if (error.error is SocketException) {
          return _offlineOrUnreachable(error.error, originalError: error.message, original: error);
        }
        if (error.error is HandshakeException || error.error is TlsException) {
          return AppException(
            userMessage: ErrorMessages.secureConnection,
            type: AppExceptionType.network,
            originalError: error.message,
            originalException: error,
            retryable: true,
          );
        }
        return AppException(
          userMessage: ErrorMessages.generic,
          statusCode: statusCode,
          type: AppExceptionType.unknown,
          originalError: error.message,
          originalException: error,
          retryable: true,
        );
    }
  }

  /// Seconds from the HTTP Retry-After header, when it is a plain number.
  static int? _retryAfterSeconds(DioException error) {
    final raw = error.response?.headers.value('retry-after');
    final n = raw == null ? null : int.tryParse(raw.trim());
    return (n != null && n > 0 && n <= 86400) ? n : null;
  }

  static AppException _mapHttpStatusCode(int? statusCode, DioException error) {
    switch (statusCode) {
      case 400:
        return AppException(
          userMessage: ErrorMessages.checkInput,
          statusCode: 400,
          type: AppExceptionType.validation,
          originalError: error.message,
          originalException: error,
        );

      case 401:
        final code = backendCode(error);
        if (code == 'MFA_SESSION_REQUIRED') {
          // A stale session is useless; drop it so the app re-verifies.
          MfaSessionManager.clear();
          return AppException(
            userMessage: 'Please verify your identity to continue.',
            statusCode: 401,
            type: AppExceptionType.mfaSessionRequired,
            originalError: error.message,
            originalException: error,
          );
        }
        if (code == 'RECENT_SIGN_IN_REQUIRED') {
          return AppException(
            userMessage: 'Please sign in again to continue.',
            statusCode: 401,
            type: AppExceptionType.recentSignInRequired,
            originalError: error.message,
            originalException: error,
          );
        }
        return AppException(
          userMessage: ErrorMessages.sessionEnded,
          statusCode: 401,
          type: AppExceptionType.authentication,
          originalError: error.message,
          originalException: error,
        );

      case 403:
        if (backendCode(error) == 'EMAIL_VERIFICATION_REQUIRED') {
          return AppException(
            userMessage: 'Please verify your email to continue.',
            statusCode: 403,
            type: AppExceptionType.authorization,
            originalError: error.message,
            originalException: error,
          );
        }
        return AppException(
          userMessage: ErrorMessages.noPermission,
          statusCode: 403,
          type: AppExceptionType.authorization,
          originalError: error.message,
          originalException: error,
        );

      case 404:
        return AppException(
          userMessage: ErrorMessages.notFound,
          statusCode: 404,
          type: AppExceptionType.notFound,
          originalError: error.message,
          originalException: error,
        );

      case 409:
        return AppException(
          userMessage: ErrorMessages.alreadyExists,
          statusCode: 409,
          type: AppExceptionType.conflict,
          originalError: error.message,
          originalException: error,
        );

      case 422:
        return AppException(
          userMessage: ErrorMessages.checkInput,
          statusCode: 422,
          type: AppExceptionType.validation,
          originalError: error.message,
          originalException: error,
        );

      case 429:
        final wait = _retryAfterSeconds(error);
        return AppException(
          userMessage: wait == null ? ErrorMessages.rateLimited : ErrorMessages.rateLimitedFor(wait),
          statusCode: 429,
          type: AppExceptionType.rateLimit,
          originalError: error.message,
          originalException: error,
          retryAfterSeconds: wait,
        );

      case 500:
      case 502:
      case 503:
      case 504:
        return AppException(
          userMessage: ErrorMessages.serverError,
          statusCode: statusCode,
          type: AppExceptionType.server,
          originalError: error.message,
          originalException: error,
          retryable: true,
        );

      default:
        return AppException(
          userMessage: ErrorMessages.generic,
          statusCode: statusCode,
          type: AppExceptionType.unknown,
          originalError: error.message,
          originalException: error,
          retryable: true,
        );
    }
  }

  static AppException _mapFirebaseAuthException(FirebaseAuthException error) {
    String message;
    var retryable = false;
    switch (error.code) {
      case 'user-not-found':
      case 'wrong-password':
      case 'invalid-credential':
        message = 'Invalid email or password.';
        break;
      case 'email-already-in-use':
        message = 'An account with this email already exists.';
        break;
      case 'invalid-email':
        message = 'Please enter a valid email address.';
        break;
      case 'user-disabled':
        message = 'This user account has been disabled.';
        break;
      case 'requires-recent-login':
        message = 'Please sign in again to complete this operation.';
        break;
      case 'too-many-requests':
        message = ErrorMessages.rateLimited;
        break;
      case 'network-request-failed':
        message = _looksOffline(null) ? ErrorMessages.offline : ErrorMessages.unreachable;
        retryable = true;
        break;
      default:
        message = 'Authentication failed. Please try again.';
        break;
    }

    return AppException(
      userMessage: message,
      originalError: '[${error.code}] ${error.message}',
      type: AppExceptionType.authentication,
      originalException: error,
      retryable: retryable,
    );
  }
}
