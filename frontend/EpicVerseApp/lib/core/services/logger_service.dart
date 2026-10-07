import 'package:dio/dio.dart' show FormData;
import 'package:flutter/foundation.dart';
import 'package:sentry_flutter/sentry_flutter.dart';

class LoggerService {
  static final LoggerService _instance = LoggerService._internal();
  factory LoggerService() => _instance;
  LoggerService._internal();

  // F-09 H1: request/response payloads may carry OTPs, one-time email proofs,
  // challenge ids, MFA session tokens, emails or passwords. They are redacted
  // before anything is printed or attached to a Sentry event.
  static const String redactedMarker = '[REDACTED]';
  static const Set<String> _sensitiveKeys = {
    'otp',
    'proof',
    'email_verification_proof',
    'challenge_id',
    'mfa_session_token',
    'identifier',
    'email',
    'password',
    'authorization',
    'x-mfa-session',
    'x-firebase-appcheck',
    'id_token',
    'token',
  };
  static final RegExp _proofPattern = RegExp(r'evp1_[A-Za-z0-9_\-]+');

  /// Redacts values stored under sensitive keys. Maps are copied with those
  /// values replaced; [FormData] is reduced to its field NAMES only.
  @visibleForTesting
  static dynamic redact(dynamic value) {
    if (value == null) return null;
    if (value is FormData) {
      final names = [
        ...value.fields.map((f) => f.key),
        ...value.files.map((f) => f.key),
      ];
      return 'FormData(fields: ${names.join(', ')})';
    }
    if (value is Map) {
      return value.map((k, v) => MapEntry(
          k, _sensitiveKeys.contains(k.toString().toLowerCase()) ? redactedMarker : redact(v)));
    }
    if (value is Iterable) return value.map(redact).toList();
    if (value is String) return scrub(value);
    return value;
  }

  /// Removes anything shaped like a one-time email proof from free text.
  @visibleForTesting
  static String scrub(String text) => text.replaceAll(_proofPattern, redactedMarker);

  /// Sentry tag form of an endpoint: query/fragment dropped and per-user
  /// identifiers (Firebase UID, invite code) replaced by placeholders so they
  /// never reach event tags.
  @visibleForTesting
  static String normalizeEndpoint(String endpoint) {
    var path = endpoint.split(RegExp(r'[?#]')).first;
    path = path.replaceFirstMapped(
      RegExp(r'^(/user)/([^/]+)'),
      (m) => m[2] == 'update-mfa' || m[2] == 'mfa' ? m[0]! : '${m[1]}/{uid}',
    );
    return path.replaceFirstMapped(
      RegExp(r'^(/validate-invite)/[^/]+'),
      (m) => '${m[1]}/{code}',
    );
  }

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
      ..writeln('⚠️ Exception   : ${scrub('$exception')}');

    // Free text that is attached to logs and Sentry events: scrubbed once.
    final safeReason = customReason == null ? null : scrub(customReason);

    if (endpoint != null) {
      logBuffer.writeln('🌐 Endpoint   : [${method ?? 'GET'}] $endpoint');
    }
    if (statusCode != null) {
      logBuffer.writeln('🔢 Status Code : $statusCode');
    }
    if (requestData != null) {
      logBuffer.writeln('📤 Request     : ${redact(requestData)}');
    }
    if (responseData != null) {
      logBuffer.writeln('📥 Response    : ${redact(responseData)}');
    }
    if (safeReason != null) {
      logBuffer.writeln('💡 Context     : $safeReason');
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
          if (endpoint != null) scope.setTag('endpoint', normalizeEndpoint(endpoint));
          if (statusCode != null) scope.setTag('status_code', statusCode.toString());
          if (screenName != null) scope.setTag('screen', screenName);
          if (method != null) scope.setTag('http_method', method);
          if (safeReason != null) scope.setContexts('context', {'reason': safeReason});
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
