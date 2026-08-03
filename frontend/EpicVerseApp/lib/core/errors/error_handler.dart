import 'package:flutter/material.dart';
import '../../main.dart';
import '../services/logger_service.dart';
import 'app_exception.dart';
import 'error_mapper.dart';

class ErrorHandler {
  static final ErrorHandler _instance = ErrorHandler._internal();
  factory ErrorHandler() => _instance;
  ErrorHandler._internal();

  /// Centralized method to process an exception, trigger technical logging/Sentry,
  /// and present a user-friendly SnackBar notification.
  static void handleError(
    dynamic exception, {
    StackTrace? stackTrace,
    BuildContext? context,
    String? screenName,
    String? endpoint,
    String? method,
    dynamic requestData,
    dynamic responseData,
    bool showSnackBar = true,
  }) {
    // 1. Map raw technical exception to clean AppException
    final appException = ErrorMapper.fromException(exception);

    // 2. Log rich technical details for developers & send to Sentry
    LoggerService.logError(
      appException.originalException ?? exception,
      stackTrace: stackTrace,
      endpoint: endpoint,
      method: method,
      statusCode: appException.statusCode,
      requestData: requestData,
      responseData: responseData,
      screenName: screenName,
      customReason: appException.originalError,
    );

    // 3. Show user-friendly SnackBar if requested
    if (showSnackBar) {
      showErrorSnackBar(appException.userMessage, context: context);
    }
  }

  /// Displays an error SnackBar (Duration: 4–5 seconds)
  static void showErrorSnackBar(String message, {BuildContext? context}) {
    _showSnackBar(
      message: message,
      context: context,
      backgroundColor: const Color(0xFFD32F2F), // Dark Red
      icon: Icons.error_outline_rounded,
      duration: const Duration(seconds: 4),
    );
  }

  /// Displays a warning SnackBar (Duration: 3 seconds)
  static void showWarningSnackBar(String message, {BuildContext? context}) {
    _showSnackBar(
      message: message,
      context: context,
      backgroundColor: const Color(0xFFED6C02), // Dark Orange
      icon: Icons.warning_amber_rounded,
      duration: const Duration(seconds: 3),
    );
  }

  /// Displays a success SnackBar (Duration: 2 seconds)
  static void showSuccessSnackBar(String message, {BuildContext? context}) {
    _showSnackBar(
      message: message,
      context: context,
      backgroundColor: const Color(0xFF2E7D32), // Dark Green
      icon: Icons.check_circle_outline_rounded,
      duration: const Duration(seconds: 2),
    );
  }

  static void _showSnackBar({
    required String message,
    BuildContext? context,
    required Color backgroundColor,
    required IconData icon,
    required Duration duration,
  }) {
    final ctx = context ?? navigatorKey.currentContext;
    if (ctx == null) return;

    final messenger = ScaffoldMessenger.maybeOf(ctx);
    if (messenger == null) return;

    // Clear previous SnackBars to prevent queue stacking
    messenger.hideCurrentSnackBar();

    messenger.showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(icon, color: Colors.white, size: 20),
            const SizedBox(width: 12),
            Expanded(
              child: Text(
                message,
                style: const TextStyle(
                  color: Colors.white,
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          ],
        ),
        backgroundColor: backgroundColor,
        behavior: SnackBarBehavior.floating,
        duration: duration,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(10),
        ),
        margin: const EdgeInsets.all(12),
      ),
    );
  }
}
