class ApiConfig {
  static const String baseUrl = 'https://sari-frowzy-frizzly.ngrok-free.dev';
  
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
    'ngrok-skip-browser-warning': 'true',
  };
}
