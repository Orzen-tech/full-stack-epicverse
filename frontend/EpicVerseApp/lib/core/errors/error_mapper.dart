import 'dart:async';
import 'dart:io';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'app_exception.dart';

class ErrorMapper {
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

    // 3. Handle Network & Socket Exceptions
    if (exception is SocketException) {
      return const AppException(
        userMessage: 'No internet connection.',
        type: AppExceptionType.network,
      );
    }

    // 4. Handle Async Timeout Exceptions
    if (exception is TimeoutException) {
      return const AppException(
        userMessage: 'Request timed out. Please try again.',
        type: AppExceptionType.timeout,
      );
    }

    // 5. Handle Parsing & Type Exceptions
    if (exception is FormatException || exception is TypeError) {
      return AppException(
        userMessage: 'Something went wrong. Please try again.',
        originalError: exception.toString(),
        type: AppExceptionType.unknown,
      );
    }

    // 6. Generic Fallback for Unknown Exceptions
    return AppException(
      userMessage: 'An unexpected error occurred.',
      originalError: exception?.toString(),
      type: AppExceptionType.unknown,
      originalException: exception,
    );
  }

  static AppException _mapDioException(DioException error) {
    final statusCode = error.response?.statusCode;

    switch (error.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.sendTimeout:
      case DioExceptionType.receiveTimeout:
        return AppException(
          userMessage: 'Request timed out. Please try again.',
          statusCode: statusCode,
          type: AppExceptionType.timeout,
          originalError: error.message,
          originalException: error,
        );

      case DioExceptionType.connectionError:
        return AppException(
          userMessage: 'No internet connection.',
          statusCode: statusCode,
          type: AppExceptionType.network,
          originalError: error.message,
          originalException: error,
        );

      case DioExceptionType.badResponse:
        return _mapHttpStatusCode(statusCode, error);

      case DioExceptionType.cancel:
        return const AppException(
          userMessage: 'Request was cancelled.',
          type: AppExceptionType.unknown,
        );

      case DioExceptionType.badCertificate:
        return const AppException(
          userMessage: 'Secure connection failed.',
          type: AppExceptionType.network,
        );

      case DioExceptionType.unknown:
      default:
        if (error.error is SocketException) {
          return const AppException(
            userMessage: 'No internet connection.',
            type: AppExceptionType.network,
          );
        }
        return AppException(
          userMessage: 'An unexpected error occurred.',
          statusCode: statusCode,
          type: AppExceptionType.unknown,
          originalError: error.message,
          originalException: error,
        );
    }
  }

  static AppException _mapHttpStatusCode(int? statusCode, DioException error) {
    switch (statusCode) {
      case 400:
        return AppException(
          userMessage: 'Please check your request and try again.',
          statusCode: 400,
          type: AppExceptionType.validation,
          originalError: error.message,
          originalException: error,
        );

      case 401:
        return AppException(
          userMessage: 'Your session has expired. Please sign in again.',
          statusCode: 401,
          type: AppExceptionType.authentication,
          originalError: error.message,
          originalException: error,
        );

      case 403:
        return AppException(
          userMessage: "You don't have permission to perform this action.",
          statusCode: 403,
          type: AppExceptionType.authorization,
          originalError: error.message,
          originalException: error,
        );

      case 404:
        return AppException(
          userMessage: 'Requested data was not found.',
          statusCode: 404,
          type: AppExceptionType.notFound,
          originalError: error.message,
          originalException: error,
        );

      case 409:
        return AppException(
          userMessage: 'This item already exists.',
          statusCode: 409,
          type: AppExceptionType.conflict,
          originalError: error.message,
          originalException: error,
        );

      case 422:
        return AppException(
          userMessage: 'Please check your input.',
          statusCode: 422,
          type: AppExceptionType.validation,
          originalError: error.message,
          originalException: error,
        );

      case 429:
        return AppException(
          userMessage: 'Too many requests. Please try again later.',
          statusCode: 429,
          type: AppExceptionType.rateLimit,
          originalError: error.message,
          originalException: error,
        );

      case 500:
      case 502:
      case 503:
      case 504:
        return AppException(
          userMessage: 'Something went wrong. Please try again.',
          statusCode: statusCode,
          type: AppExceptionType.server,
          originalError: error.message,
          originalException: error,
        );

      default:
        return AppException(
          userMessage: 'Something went wrong. Please try again.',
          statusCode: statusCode,
          type: AppExceptionType.unknown,
          originalError: error.message,
          originalException: error,
        );
    }
  }

  static AppException _mapFirebaseAuthException(FirebaseAuthException error) {
    String message;
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
        message = 'Too many attempts. Please try again later.';
        break;
      case 'network-request-failed':
        message = 'No internet connection.';
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
    );
  }
}
