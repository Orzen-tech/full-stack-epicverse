import 'package:shared_preferences/shared_preferences.dart';
import 'package:uuid/uuid.dart';

class SessionManager {
  static const String _sessionKey = 'epic_device_session_id';

  /// Gets the existing session ID or creates a new one for this device.
  static Future<String> getSessionId() async {
    final prefs = await SharedPreferences.getInstance();
    String? sessionId = prefs.getString(_sessionKey);
    
    if (sessionId == null) {
      sessionId = const Uuid().v4();
      await prefs.setString(_sessionKey, sessionId);
    }
    
    return sessionId;
  }
}
