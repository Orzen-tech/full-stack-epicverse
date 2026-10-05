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

  const AppException({
    required this.userMessage,
    this.originalError,
    this.statusCode,
    this.type = AppExceptionType.unknown,
    this.originalException,
  });

  @override
  String toString() => userMessage;
}

typedef DynamicData = dynamic;
