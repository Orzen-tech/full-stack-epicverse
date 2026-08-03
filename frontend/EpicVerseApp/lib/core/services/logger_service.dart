import 'package:flutter/foundation.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

class LoggerService {
  static final LoggerService _instance = LoggerService._internal();
  factory LoggerService() => _instance;
  LoggerService._internal();

  /// Logs developer-facing technical details and reports exceptions to Sentry.
  static void logError(
    dynamic exception, {
    StackTrace? stackTrace,
    String? endpoint,
    String? method,
    int? statusCode,
    dynamic requestData,
    dynamic responseData,
    String? screenName,
    String? customReason,
  }) {
    final timestamp = DateTime.now().toIso8601String();

    // 1. Format rich technical output for Console (Developers)
    final logBuffer = StringBuffer()
      ..writeln('-------------------- 🚨 ERROR LOG 🚨 --------------------')
      ..writeln('⏰ Timestamp   : $timestamp')
      ..writeln('📍 Screen      : ${screenName ?? 'Unknown / Background'}')
      ..writeln('⚠️ Exception   : $exception');

    if (endpoint != null) {
      logBuffer.writeln('🌐 Endpoint   : [${method ?? 'GET'}] $endpoint');
    }
    if (statusCode != null) {
      logBuffer.writeln('🔢 Status Code : $statusCode');
    }
    if (requestData != null) {
      logBuffer.writeln('📤 Request     : $requestData');
    }
    if (responseData != null) {
      logBuffer.writeln('📥 Response    : $responseData');
    }
    if (customReason != null) {
      logBuffer.writeln('💡 Context     : $customReason');
    }
    if (stackTrace != null && kDebugMode) {
      logBuffer.writeln('📜 StackTrace  :\n$stackTrace');
    }
    logBuffer.writeln('---------------------------------------------------------');

    debugPrint(logBuffer.toString());

    // 2. Report to Sentry (Production Monitoring)
    try {
      Sentry.captureException(
        exception,
        stackTrace: stackTrace,
        withScope: (scope) {
          if (endpoint != null) scope.setTag('endpoint', endpoint);
          if (statusCode != null) scope.setTag('status_code', statusCode.toString());
          if (screenName != null) scope.setTag('screen', screenName);
          if (method != null) scope.setTag('http_method', method);
          if (customReason != null) scope.setContexts('context', {'reason': customReason});
        },
      );
    } catch (sentryError) {
      debugPrint('⚠️ [LoggerService] Sentry report failed: $sentryError');
    }
  }

  /// Information level log
  static void logInfo(String message, {String? tag}) {
    if (kDebugMode) {
      debugPrint('ℹ️ [${tag ?? 'APP_INFO'}] $message');
    }
  }

  /// Warning level log
  static void logWarning(String message, {String? tag}) {
    if (kDebugMode) {
      debugPrint('⚠️ [${tag ?? 'APP_WARNING'}] $message');
    }
  }
}

final loggerService = LoggerService();
