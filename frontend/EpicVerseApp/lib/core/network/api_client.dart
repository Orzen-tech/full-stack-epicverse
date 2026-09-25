import 'package:dio/dio.dart';
import 'package:firebase_app_check/firebase_app_check.dart';
import 'package:flutter/foundation.dart' show debugPrint;
import '../errors/error_handler.dart';
import '../errors/error_mapper.dart';
import 'api_config.dart';
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

  // GET Request
  Future<Response<T>> get<T>(
    String path, {
    Map<String, dynamic>? queryParameters,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      return await _dio.get<T>(
        path,
        queryParameters: queryParameters,
        options: options,
        cancelToken: cancelToken,
      );
    } catch (e) {
      throw ErrorMapper.fromException(e);
    }
  }

  // POST Request
  Future<Response<T>> post<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Options? options,
    CancelToken? cancelToken,
  }) async {
    try {
      return await _dio.post<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: options,
        cancelToken: cancelToken,
      );
    } catch (e) {
      throw ErrorMapper.fromException(e);
    }
  }

  // PUT Request
  Future<Response<T>> put<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Options? options,
  }) async {
    try {
      return await _dio.put<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: options,
      );
    } catch (e) {
      throw ErrorMapper.fromException(e);
    }
  }

  // DELETE Request
  Future<Response<T>> delete<T>(
    String path, {
    dynamic data,
    Map<String, dynamic>? queryParameters,
    Options? options,
  }) async {
    try {
      return await _dio.delete<T>(
        path,
        data: data,
        queryParameters: queryParameters,
        options: options,
      );
    } catch (e) {
      throw ErrorMapper.fromException(e);
    }
  }
}

final apiClient = ApiClient();
