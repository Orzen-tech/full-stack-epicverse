import 'package:dio/dio.dart';
import '../errors/error_handler.dart';
import '../errors/error_mapper.dart';
import 'api_config.dart';

class ApiClient {
  late final Dio _dio;

  ApiClient({String? baseUrl}) {
    _dio = Dio(
      BaseOptions(
        baseUrl: baseUrl ?? ApiConfig.apiUrl,
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 15),
        sendTimeout: const Duration(seconds: 15),
        contentType: 'application/json',
      ),
    );

    _dio.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          // Dynamically attach authorization header if user is authenticated
          final authHeaders = await ApiConfig.authHeaders();
          options.headers.addAll(authHeaders);
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
