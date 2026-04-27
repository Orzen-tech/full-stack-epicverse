class ApiConfig {
  static const String baseUrl = 'https://epicverse-backend-721191424605.us-central1.run.app';
  
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
}
