import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../models/user_model.dart';

class ApiService {
  final Dio _dio = Dio(
    BaseOptions(
      baseUrl: 'https://api.mltiverse.app/v1',
      connectTimeout: const Duration(seconds: 15),
      receiveTimeout: const Duration(seconds: 15),
    ),
  );

  ApiService() {
    _dio.interceptors.add(InterceptorsWrapper(
      onError: (e, handler) {
        // Global Error Handling (Step 14)
        debugPrint('API Error: ${e.message}');
        return handler.next(e);
      },
    ));
  }

  // Mock Login (Step 13)
  Future<UserModel> login(String email, String password) async {
    // Simulate API delay
    await Future.delayed(const Duration(seconds: 2));
    
    // Mock response
    return UserModel(
      id: '123',
      displayName: 'Gayathiri',
      email: email,
      primaryLanguage: 'English',
      preferredLanguages: ['English', 'Tamil'],
    );
  }

  // Future: Real API calls
  Future<Response> get(String path) => _dio.get(path);
}

final apiServiceProvider = Provider((ref) => ApiService());
