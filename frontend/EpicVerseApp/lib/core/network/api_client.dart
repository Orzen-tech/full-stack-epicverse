import 'package:dio/dio.dart';
import 'package:firebase_app_check/firebase_app_check.dart';
import 'package:flutter/foundation.dart' show debugPrint, visibleForTesting;
import '../dev/local_lab.dart';
import '../errors/app_exception.dart';
import '../errors/error_handler.dart';
import '../errors/error_mapper.dart';
import '../services/network_service.dart' show networkService;
import 'api_config.dart';
import 'mfa_reprompt.dart';
import 'ssl_pinning_service.dart';

class ApiClient {
  late final Dio _dio;
  final void Function(int? statusCode) _healthReporter;

  ApiClient({
    String? baseUrl,
    // Test seams only: a fake transport and a fake health sink. Production passes neither.
    @visibleForTesting HttpClientAdapter? httpClientAdapter,
    @visibleForTesting void Function(int? statusCode)? healthReporter,
  }) : _healthReporter = healthReporter ?? networkService.reportBackendResponse {
    // Use pinned Dio — enforces SSL certificate validation at the Dart level,
    // independent of network_security_config.xml (which only covers OS layer).
    _dio = SslPinningService.createPinnedDio(
      baseUrl: baseUrl ?? ApiConfig.apiUrl,
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 15),
      sendTimeout: const Duration(seconds: 15),
    );
    if (httpClientAdapter != null) _dio.httpClientAdapter = httpClientAdapter;

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
          // Skipped in the debug-only local-lab mode, where App Check is never activated.
          if (!LocalLab.active) {
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
          }

          return handler.next(options);
        },
        onResponse: (response, handler) {
          // A real answer proves the backend is reachable (network banner health signal).
          final code = response.statusCode;
          if (code != null) _reportHealth(code);
          return handler.next(response);
        },
        onError: (DioException e, handler) {
          // Health signal: an HTTP answer (any status) or a connection-level failure. Only the
          // status code is passed on; a cancelled request says nothing about the network.
          if (e.response != null) {
            final code = e.response!.statusCode;
            if (code != null) _reportHealth(code);
          } else if (e.type != DioExceptionType.cancel) {
            _reportHealth(null);
          }

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

  /// Network-banner health signal. Must never affect the request it observes: any error from the
  /// reporter is swallowed here, and the original response or exception continues unchanged.
  void _reportHealth(int? statusCode) {
    try {
      _healthReporter(statusCode);
    } catch (_) {}
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

/// Minimal reachability probe for the network banner: GET / on the SAME pinned client stack as
/// every other request, with no sign-in headers. It deliberately uses the static root route, NOT
/// /health: /health runs a database query and a cache ping on every call, and this probe fires
/// from every open phone. Returns the HTTP status, or null when the backend could not be reached.
Future<int?> probeBackendHealth() async {
  final dio = SslPinningService.createPinnedDio(
    baseUrl: ApiConfig.baseUrl,
    // Generous on purpose: a backend waking from idle can take 15 s+ to answer its first request.
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 25),
    sendTimeout: const Duration(seconds: 10),
  );
  try {
    final r = await dio.get<dynamic>('/', options: Options(validateStatus: (_) => true));
    return r.statusCode;
  } on DioException {
    return null;
  } finally {
    dio.close(force: true);
  }
}

final apiClient = ApiClient();
