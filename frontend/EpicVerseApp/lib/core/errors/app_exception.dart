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
