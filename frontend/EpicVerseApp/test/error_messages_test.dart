import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/errors/error_handler.dart';
import 'package:epicverse/core/errors/error_mapper.dart';
import 'package:epicverse/core/network/voice_status_labels.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

DioException _dio(DioExceptionType type, {int? status, Object? error, Map<String, List<String>>? headers}) {
  final req = RequestOptions(path: '/x');
  return DioException(
    requestOptions: req,
    type: type,
    error: error,
    response: status == null
        ? null
        : Response(requestOptions: req, statusCode: status, headers: Headers.fromMap(headers ?? {})),
  );
}

void main() {
  group('ErrorMapper messages', () {
    test('timeouts say it took too long and offer retry', () {
      final e = ErrorMapper.fromException(_dio(DioExceptionType.receiveTimeout));
      expect(e.userMessage, ErrorMessages.timeout);
      expect(e.retryable, isTrue);
      expect(e.type, AppExceptionType.timeout);
    });

    test('plain TimeoutException maps the same way', () {
      final e = ErrorMapper.fromException(TimeoutException('x'));
      expect(e.userMessage, ErrorMessages.timeout);
      expect(e.retryable, isTrue);
    });

    test('a failed host lookup means the phone is offline', () {
      final e = ErrorMapper.fromException(
          _dio(DioExceptionType.connectionError, error: const SocketException('Failed host lookup: x')));
      expect(e.userMessage, ErrorMessages.offline);
      expect(e.retryable, isTrue);
    });

    test('a refused connection with internet available says EpicVerse is unreachable', () {
      final e = ErrorMapper.fromException(
          _dio(DioExceptionType.connectionError, error: const SocketException('Connection refused')));
      expect(e.userMessage, ErrorMessages.unreachable);
      expect(e.retryable, isTrue);
    });

    test('certificate and TLS failures give secure-connection advice', () {
      expect(ErrorMapper.fromException(_dio(DioExceptionType.badCertificate)).userMessage,
          ErrorMessages.secureConnection);
      expect(
          ErrorMapper.fromException(
                  _dio(DioExceptionType.connectionError, error: const HandshakeException('bad cert')))
              .userMessage,
          ErrorMessages.secureConnection);
    });

    test('server errors are our side and retryable', () {
      for (final code in [500, 502, 503, 504]) {
        final e = ErrorMapper.fromException(_dio(DioExceptionType.badResponse, status: code));
        expect(e.userMessage, ErrorMessages.serverError, reason: '$code');
        expect(e.retryable, isTrue, reason: '$code');
      }
    });

    test('429 uses the server wait time when it sends one, and is not retryable', () {
      final withHeader = ErrorMapper.fromException(
          _dio(DioExceptionType.badResponse, status: 429, headers: {'retry-after': ['120']}));
      expect(withHeader.userMessage, 'Too many tries. Please wait 2 minutes.');
      expect(withHeader.retryAfterSeconds, 120);
      expect(withHeader.retryable, isFalse);
      final without = ErrorMapper.fromException(_dio(DioExceptionType.badResponse, status: 429));
      expect(without.userMessage, ErrorMessages.rateLimited);
      expect(ErrorMessages.rateLimitedFor(45), 'Too many tries. Please wait 45 seconds.');
    });

    test('client errors are not retryable and read plainly', () {
      expect(ErrorMapper.fromException(_dio(DioExceptionType.badResponse, status: 401)).userMessage,
          ErrorMessages.sessionEnded);
      expect(ErrorMapper.fromException(_dio(DioExceptionType.badResponse, status: 403)).userMessage,
          ErrorMessages.noPermission);
      expect(ErrorMapper.fromException(_dio(DioExceptionType.badResponse, status: 404)).userMessage,
          ErrorMessages.notFound);
      expect(ErrorMapper.fromException(_dio(DioExceptionType.badResponse, status: 422)).retryable, isFalse);
    });

    test('unknown errors get the generic message with a support hint', () {
      final e = ErrorMapper.fromException(StateError('boom'));
      expect(e.userMessage, ErrorMessages.generic);
      expect(e.userMessage, contains('contact support'));
    });

    test('an exception message never leaks into what the user sees', () {
      const secret = 'java.lang.NullPointerException at com.foo.Bar(Bar.java:42)';
      final shown = [
        ErrorMapper.fromException(StateError(secret)),
        ErrorMapper.fromException(const FormatException(secret)),
        ErrorMapper.fromException(_dio(DioExceptionType.unknown, error: StateError(secret))),
      ].map((e) => e.userMessage);
      for (final m in shown) {
        expect(m.contains('Null'), isFalse);
        expect(m.contains('java'), isFalse);
      }
    });
  });

  group('message quality rules', () {
    const all = {
      'offline': ErrorMessages.offline,
      'timeout': ErrorMessages.timeout,
      'unreachable': ErrorMessages.unreachable,
      'secureConnection': ErrorMessages.secureConnection,
      'serverError': ErrorMessages.serverError,
      'generic': ErrorMessages.generic,
      'sessionEnded': ErrorMessages.sessionEnded,
      'noPermission': ErrorMessages.noPermission,
      'notFound': ErrorMessages.notFound,
      'alreadyExists': ErrorMessages.alreadyExists,
      'checkInput': ErrorMessages.checkInput,
      'rateLimited': ErrorMessages.rateLimited,
      'voiceStartFailed': ErrorMessages.voiceStartFailed,
    };
    final technical = RegExp(r'exception|socket|dio|http|null|stack|\b[45]\d\d\b|certificate|handshake|tls|ssl',
        caseSensitive: false);

    all.forEach((name, text) {
      test('$name is short, ends with a full stop and has no technical words', () {
        expect(text.length, lessThanOrEqualTo(90));
        expect(text.endsWith('.'), isTrue);
        expect(technical.hasMatch(text), isFalse, reason: text);
      });
    });
  });

  group('voice status labels', () {
    test('every connection status becomes a plain sentence', () {
      expect(voiceStatusLabel('Disconnected'), 'Tap the mic to start');
      expect(voiceStatusLabel('Reconnecting...'), 'Connection lost. Reconnecting...');
      expect(voiceStatusLabel('Reconnect failed'), contains('Tap the mic to try again'));
      expect(voiceStatusLabel('Email verification required'), 'Please verify your email to use voice.');
      expect(voiceStatusLabel('Connected'), 'Ready');
    });

    test('unknown status words are not shown', () {
      expect(voiceStatusLabel('Something internal'), isNull);
    });
  });

  group('error snackbar', () {
    Future<void> show(WidgetTester tester, Object error, {VoidCallback? onRetry}) async {
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () => ErrorHandler.handleError(error, context: context, onRetry: onRetry),
              child: const Text('go'),
            ),
          ),
        ),
      ));
      await tester.tap(find.text('go'));
      await tester.pump();
    }

    testWidgets('a retryable error with a callback shows a Retry button that runs it', (tester) async {
      var retried = 0;
      await show(tester, _dio(DioExceptionType.receiveTimeout), onRetry: () => retried++);
      expect(find.text(ErrorMessages.timeout), findsOneWidget);
      expect(find.text('Retry'), findsOneWidget);
      await tester.pump(const Duration(milliseconds: 600)); // let the snackbar finish sliding in
      await tester.tap(find.text('Retry'));
      expect(retried, 1);
    });

    testWidgets('a non-retryable error shows no Retry button even if a callback is given', (tester) async {
      await show(tester, _dio(DioExceptionType.badResponse, status: 403), onRetry: () {});
      expect(find.text(ErrorMessages.noPermission), findsOneWidget);
      expect(find.text('Retry'), findsNothing);
    });

    testWidgets('no callback means no Retry button', (tester) async {
      await show(tester, _dio(DioExceptionType.receiveTimeout));
      expect(find.text('Retry'), findsNothing);
    });
  });
}
