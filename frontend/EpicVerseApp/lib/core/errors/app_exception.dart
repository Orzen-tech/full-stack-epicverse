enum AppExceptionType {
  network,
  timeout,
  authentication,
  authorization,
  notFound,
  conflict,
  validation,
  rateLimit,
  server,
  unknown,
  // F-09: backend says this request needs a (fresh) MFA verification.
  mfaSessionRequired,
  // F-09: backend says the Firebase sign-in is too old for this change.
  recentSignInRequired,
}

class AppException implements Exception {
  final String userMessage;
  final String? originalError;
  final int? statusCode;
  final AppExceptionType type;
  final DynamicData? originalException;

  /// True when trying the same action again can reasonably succeed (offline, timeout,
  /// server hiccup). The UI uses it to offer a Retry button.
  final bool retryable;

  /// Seconds the server asked the client to wait (HTTP Retry-After), if it sent one.
  final int? retryAfterSeconds;

  const AppException({
    required this.userMessage,
    this.originalError,
    this.statusCode,
    this.type = AppExceptionType.unknown,
    this.originalException,
    this.retryable = false,
    this.retryAfterSeconds,
  });

  @override
  String toString() => userMessage;
}

typedef DynamicData = dynamic;

/// Every user-facing error sentence in one place. Rules: say what happened, then what to
/// do next; short; no codes, no jargon, no technical text.
class ErrorMessages {
  static const offline = "You're offline. Check your internet and try again.";
  static const timeout = 'That took too long. Check your connection and try again.';
  static const unreachable = "Can't reach EpicVerse right now. Please try again in a moment.";
  static const secureConnection =
      "Couldn't make a secure connection. Try mobile data or another Wi-Fi network.";
  static const serverError = 'Something went wrong on our side. Please try again shortly.';
  static const generic =
      'Something went wrong. Please try again. If it keeps happening, contact support.';
  static const sessionEnded = 'Your session ended. Please sign in again.';
  static const noPermission = "You don't have permission to do that.";
  static const notFound = "We couldn't find that.";
  static const alreadyExists = 'That already exists.';
  static const checkInput = 'Please check what you entered and try again.';
  static const cancelled = 'Cancelled.';
  static const rateLimited = 'Too many tries. Please wait a few minutes.';
  static const voiceStartFailed = "Couldn't start the voice session. Tap the mic to try again.";

  /// "Too many tries. Please wait 2 minutes." when the server told us how long.
  static String rateLimitedFor(int seconds) {
    if (seconds <= 0) return rateLimited;
    if (seconds < 90) return 'Too many tries. Please wait $seconds seconds.';
    final minutes = (seconds / 60).ceil();
    return 'Too many tries. Please wait $minutes minutes.';
  }
}
