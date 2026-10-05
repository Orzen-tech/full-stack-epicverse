import 'package:dio/dio.dart';
import 'package:firebase_app_check/firebase_app_check.dart';
import 'package:flutter/foundation.dart' show debugPrint;
import '../errors/app_exception.dart';
import '../errors/error_handler.dart';
import '../errors/error_mapper.dart';
import 'api_config.dart';
import 'mfa_reprompt.dart';
import 'ssl_pinning_service.dart';

class ApiClient {
  late final Dio _dio;

  ApiClient({String? baseUrl}) {
    // Use pinned Dio — enforces SSL certificate validation at the Dart level,
    // independent of network_security_config.xml (which only covers OS layer).
    _dio = SslPinningService.createPinnedDio(
      baseUrl: baseUrl ?? ApiConfig.apiUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 15),
      sendTimeout: const Duration(seconds: 15),
    );

    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          // Dynamically attach authorization header if user is authenticated
          final authHeaders = await ApiConfig.authHeaders();
          options.headers.addAll(authHeaders);

          // App Check token — monitor-only signal for the backend (see
          // Finding #4 remediation). Uses the SDK's own token caching
          // (no forceRefresh), so this is a network call only on first
          // use / near expiry, not on every request. A short timeout and
          // broad catch ensure a slow or failed fetch never blocks or
          // fails the actual API call — the header is simply omitted.
          try {
            final token = await FirebaseAppCheck.instance
                .getToken()
                .timeout(const Duration(seconds: 3));
            if (token != null) {
              options.headers['X-Firebase-AppCheck'] = token;
            }
          } catch (e) {
            debugPrint('[ApiClient] App Check token unavailable (non-blocking): $e');
          }

          return handler.next(options);
        },
        onError: (DioException e, handler) {
          // Map and log technical error globally
          final mappedError = ErrorMapper.fromException(e);

          // Log to Console & Sentry via ErrorHandler
          ErrorHandler.handleError(
            mappedError,
            stackTrace: e.stackTrace,
            endpoint: e.requestOptions.path,
            method: e.requestOptions.method,
            requestData: e.requestOptions.data,
            responseData: e.response?.data,
            showSnackBar: false, // UI screen decides whether to show SnackBar
          );

          return handler.next(e);
        },
      ),
    );
  }

  /// Sends the request; if the backend answers MFA_SESSION_REQUIRED, asks the
  /// user to re-verify once and retries once. MFA endpoints are never retried.
  Future<Response<T>> _send<T>(String path, Future<Response<T>> Function() call) async {
    try {
      return await call();
    } catch (e) {
      final mapped = ErrorMapper.fromException(e);
      final handler = MfaReprompt.handler;
      if (mapped.type != AppExceptionType.mfaSessionRequired ||
          handler == null ||
          path.startsWith('/auth/mfa/')) {
        throw mapped;
      }
      if (!await handler()) throw mapped;
      try {
        return await call();
      } catch (e2) {
        throw ErrorMapper.fromException(e2);
      }
    }
  }

  // GET Request
  Future<Response<T>> get<T>(
    String path, {
    Map<String, dynamic>? queryParameters,
    Options? options,
    CancelToken? cancelToken,
  }) =>
      _send(path, () => _dio.get<T>(
            path,
            queryParameters: queryParameters,
            options: options,
            cancelToken: cancelToken,
          ));

  // POST Request
  Future<Response<T>> post<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Options? options,
    CancelToken? cancelToken,
  }) =>
      _send(path, () => _dio.post<T>(
            path,
            data: data is FormData ? data.clone() : data,
            queryParameters: queryParameters,
            options: options,
            cancelToken: cancelToken,
          ));

  // PUT Request
  Future<Response<T>> put<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Options? options,
  }) =>
      _send(path, () => _dio.put<T>(
            path,
            data: data is FormData ? data.clone() : data,
            queryParameters: queryParameters,
            options: options,
          ));

  // DELETE Request
  Future<Response<T>> delete<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Options? options,
  }) =>
      _send(path, () => _dio.delete<T>(
            path,
            data: data is FormData ? data.clone() : data,
            queryParameters: queryParameters,
            options: options,
          ));
}

final apiClient = ApiClient();
