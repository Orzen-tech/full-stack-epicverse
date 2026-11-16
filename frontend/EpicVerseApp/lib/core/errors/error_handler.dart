import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../main.dart';
import '../widgets/error_card.dart';
import '../services/logger_service.dart';
import '../services/network_service.dart';
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
    // When given and the error is retryable, the snackbar shows a Retry button.
    VoidCallback? onRetry,
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

    // 3. Show user-friendly SnackBar if requested. While the app-wide banner already says
    // "You're offline", a second red message for the same thing is just noise.
    final bannerAlreadyShowsOffline = appException.type == AppExceptionType.network &&
        networkService.currentStatus == NetworkStatus.noInternet;
    if (showSnackBar && !bannerAlreadyShowsOffline) {
      showErrorSnackBar(
        appException.userMessage,
        context: context,
        onRetry: appException.retryable ? onRetry : null,
        type: appException.type,
      );
    }
  }

  /// Shows an error as a floating card (icon, title, message, optional Retry button).
  /// A card with a Retry button stays until tapped or dismissed (up to 30 s); others 6 s.
  static void showErrorSnackBar(
    String message, {
    BuildContext? context,
    VoidCallback? onRetry,
    AppExceptionType type = AppExceptionType.validation, // plain messages are usually "check your input"
  }) {
    final ctx = context ?? navigatorKey.currentContext;
    if (ctx == null) return;
    final messenger = ScaffoldMessenger.maybeOf(ctx);
    if (messenger == null) return;

    // One at a time: a new error replaces the old one.
    messenger.hideCurrentSnackBar();
    messenger.showSnackBar(
      SnackBar(
        backgroundColor: Colors.transparent,
        elevation: 0,
        padding: EdgeInsets.zero,
        behavior: SnackBarBehavior.floating,
        margin: const EdgeInsets.all(12),
        duration: Duration(seconds: onRetry == null ? 6 : 30),
        content: ErrorCard(
          title: errorTitleFor(type),
          message: message,
          severity: errorSeverityFor(type),
          onRetry: onRetry == null
              ? null
              : () {
                  messenger.hideCurrentSnackBar();
                  onRetry();
                },
          onDismiss: messenger.hideCurrentSnackBar,
        ),
      ),
    );
    HapticFeedback.lightImpact();
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
    VoidCallback? onRetry,
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
        action: onRetry == null
            ? null
            : SnackBarAction(
                label: 'Retry',
                textColor: Colors.white,
                onPressed: onRetry,
              ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(10),
        ),
        margin: const EdgeInsets.all(12),
      ),
    );
  }
}
