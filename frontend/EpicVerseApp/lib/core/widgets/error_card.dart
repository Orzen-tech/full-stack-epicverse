import 'package:flutter/material.dart';
import '../constants/app_colors.dart';
import '../errors/app_exception.dart';

/// How serious a message is. Decides the icon and accent colour.
enum ErrorSeverity { connection, failure, info }

/// Short heading shown above the message, chosen from the kind of error.
String errorTitleFor(AppExceptionType type) {
  switch (type) {
    case AppExceptionType.network:
      return 'Connection problem';
    case AppExceptionType.timeout:
      return 'Taking too long';
    case AppExceptionType.server:
      return 'Our side is having trouble';
    case AppExceptionType.authentication:
    case AppExceptionType.mfaSessionRequired:
    case AppExceptionType.recentSignInRequired:
      return 'Sign-in needed';
    case AppExceptionType.authorization:
      return 'Not allowed';
    case AppExceptionType.rateLimit:
      return 'Please slow down';
    case AppExceptionType.validation:
      return 'Check your details';
    case AppExceptionType.notFound:
    case AppExceptionType.conflict:
    case AppExceptionType.unknown:
      return 'Something went wrong';
  }
}

ErrorSeverity errorSeverityFor(AppExceptionType type) {
  switch (type) {
    case AppExceptionType.network:
    case AppExceptionType.timeout:
      return ErrorSeverity.connection;
    case AppExceptionType.rateLimit:
      return ErrorSeverity.info;
    default:
      return ErrorSeverity.failure;
  }
}

/// A floating message card: icon, title, one line of explanation and an optional Retry
/// button. Plain text only (never HTML), so server text can never inject anything.
class ErrorCard extends StatelessWidget {
  final String title;
  final String message;
  final ErrorSeverity severity;
  final VoidCallback? onRetry;
  final VoidCallback onDismiss;

  const ErrorCard({
    super.key,
    required this.title,
    required this.message,
    required this.severity,
    required this.onDismiss,
    this.onRetry,
  });

  Color get _accent {
    switch (severity) {
      case ErrorSeverity.connection:
        return const Color(0xFFFFA726); // orange
      case ErrorSeverity.failure:
        return AppColors.error; // red
      case ErrorSeverity.info:
        return const Color(0xFF64B5F6); // blue
    }
  }

  IconData get _icon {
    switch (severity) {
      case ErrorSeverity.connection:
        return Icons.cloud_off_rounded;
      case ErrorSeverity.failure:
        return Icons.error_outline_rounded;
      case ErrorSeverity.info:
        return Icons.info_outline_rounded;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      liveRegion: true,
      child: Container(
          padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
          decoration: BoxDecoration(
            color: AppColors.surface,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: _accent.withValues(alpha: 0.6)),
            boxShadow: const [
              BoxShadow(color: AppColors.glassShadow, blurRadius: 16, offset: Offset(0, 6)),
            ],
          ),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Icon(_icon, color: _accent, size: 24),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                          color: AppColors.textPrimary, fontSize: 15, fontWeight: FontWeight.w700),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      message,
                      style: const TextStyle(color: AppColors.textSecondary, fontSize: 13.5, height: 1.3),
                    ),
                    if (onRetry != null) ...[
                      const SizedBox(height: 10),
                      SizedBox(
                        height: 40,
                        child: ElevatedButton(
                          onPressed: onRetry,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: _accent,
                            foregroundColor: Colors.black,
                            elevation: 0,
                            padding: const EdgeInsets.symmetric(horizontal: 22),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
                          ),
                          child: const Text('Retry', style: TextStyle(fontWeight: FontWeight.w700)),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              IconButton(
                tooltip: 'Dismiss',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.close_rounded, color: AppColors.textMuted, size: 20),
                onPressed: onDismiss,
              ),
            ],
          ),
      ),
    );
  }
}
