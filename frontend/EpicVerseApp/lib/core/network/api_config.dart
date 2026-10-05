import 'package:firebase_auth/firebase_auth.dart';
import 'mfa_session_manager.dart';

class ApiConfig {

  // Test builds can point at an isolated backend with
  // --dart-define=API_BASE_URL=...; normal builds use production.
  static const String baseUrl = String.fromEnvironment(
    'API_BASE_URL',
    defaultValue: 'https://epicverse-backend-721191424605.asia-south1.run.app',
  );

  

  static String get wsUrl {

    String cleanUrl = baseUrl;

    if (cleanUrl.startsWith('https://')) {

      cleanUrl = cleanUrl.replaceFirst('https://', 'wss://');

    } else if (cleanUrl.startsWith('http://')) {

      cleanUrl = cleanUrl.replaceFirst('http://', 'ws://');

    }

    return '$cleanUrl/api/v1/ws/realtime';

  }



  static String get apiUrl => '$baseUrl/api/v1';



  static Map<String, String> get headers => {

    'Content-Type': 'application/json',

  };

  static Future<Map<String, String>> authHeaders() async {
    final user = FirebaseAuth.instance.currentUser;
    final token = await user?.getIdToken();
    final mfaSession = MfaSessionManager.getSessionForUid(user?.uid);
    return {
      'Content-Type': 'application/json',
      if (token != null) 'Authorization': 'Bearer $token',
      'X-MFA-Session': ?mfaSession,
    };
  }

}

