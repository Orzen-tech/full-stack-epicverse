import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/io.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'api_config.dart';
import 'session_manager.dart';

class WebSocketService {
  static final WebSocketService _instance = WebSocketService._internal();
  factory WebSocketService() => _instance;
  WebSocketService._internal();

  WebSocketChannel? _channel;
  bool _isConnected = false;
  String _statusText = 'Disconnected';
  final _messageController = StreamController<dynamic>.broadcast();
  final _statusController = StreamController<String>.broadcast();
  final _connectionStateController = StreamController<bool>.broadcast();
  
  String _currentMode = 'Mode 1';
  bool _isConnecting = false;
  
  Completer<void>? _connectionCompleter;
  Timer? _reconnectTimer;

  bool get isConnected => _isConnected;
  String get statusText => _statusText;
  Stream<dynamic> get messages => _messageController.stream;
  Stream<String> get statusTextStream => _statusController.stream;
  Stream<bool> get connectionState => _connectionStateController.stream;

  Future<void> connect({String? hostOrUrl, bool? isListening, String? game_mode}) async {
    if (_isConnected) return;
    if (_isConnecting) {
       await (_connectionCompleter?.future ?? Future.value());
       return;
    }

    if (game_mode != null) {
      _currentMode = game_mode;
    }

    _isConnecting = true;
    _connectionCompleter = Completer<void>();
    _statusController.add('Connecting...');

    String? idToken;
    String? uid;
    String sessionId = await SessionManager.getSessionId();

    try {
      final user = FirebaseAuth.instance.currentUser;
      if (user == null) {
         _isConnecting = false;
         _connectionCompleter?.completeError('User not logged in');
         return;
      }
      uid = user.uid;
      idToken = await user.getIdToken().timeout(const Duration(seconds: 5));
    } catch (e) {
      debugPrint('Auth/Token error: $e');
      _isConnecting = false;
      _connectionCompleter?.completeError(e);
      return;
    }

    _reconnectTimer?.cancel();

    final listenFlag = isListening ?? false;
    // Token is sent via the Authorization handshake header (NOT in the URL),
    // so it does not leak into Cloud Run / load balancer / proxy access logs.
    String queryParams = 'uid=$uid&mode=${Uri.encodeComponent(_currentMode)}&session_id=$sessionId&listening=$listenFlag';

    Uri wsUri = Uri.parse('${ApiConfig.wsUrl}?$queryParams');
    if (hostOrUrl != null && hostOrUrl.isNotEmpty) {
      wsUri = Uri.parse('${hostOrUrl.startsWith('ws') ? hostOrUrl : 'ws://$hostOrUrl'}/api/v1/ws/realtime?$queryParams');
    }

    try {
      _channel = IOWebSocketChannel.connect(
        wsUri,
        headers: {
          ...ApiConfig.headers,
          'Authorization': 'Bearer $idToken',
        },
      );
      
      _channel!.stream.listen(
        (message) {
          if (!_isConnected) {
            _isConnected = true;
            _isConnecting = false;
            _statusController.add('Connected');
            _connectionStateController.add(true);
            if (!(_connectionCompleter?.isCompleted ?? true)) {
              _connectionCompleter?.complete();
            }
          }

          if (message is List<int>) {
             _messageController.add(Uint8List.fromList(message));
             return;
          }

          try {
            final data = jsonDecode(message);
            
            // Check for Session Kick
            if (data['type'] == 'error' && (data['code'] == 'SESSION_KICKED' || data['code'] == 'SESSION_INVALID')) {
               _handleDisconnect();
               _statusController.add('Logged out: Active elsewhere');
               _messageController.add(data); // Propagate to UI for dialog
               return;
            }

            if (data['type'] == 'connection_success') {
               debugPrint("WS: Unified Handshake Confirmed");
               if (!(_connectionCompleter?.isCompleted ?? true)) {
                 _connectionCompleter?.complete();
               }
            }
            _messageController.add(data);
          } catch (e) {
            debugPrint('Error decoding message: $e');
          }
        },
        onError: (error) {
          debugPrint('WebSocket error: $error');
          _handleDisconnect();
        },
        onDone: () {
          debugPrint('WebSocket closed');
          _handleDisconnect();
        },
      );
    } catch (e) {
      debugPrint('Connection error: $e');
      _handleDisconnect();
    }
    
    await (_connectionCompleter?.future ?? Future.value());
  }

  void disconnect() {
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _handleDisconnect();
  }

  void _handleDisconnect() {
    if (!(_connectionCompleter?.isCompleted ?? true)) {
       _connectionCompleter?.completeError('Handshake failed or was interrupted');
    }

    if (_isConnected) {
      _isConnected = false;
      _connectionStateController.add(false);
    }
    _statusController.add('Disconnected');
    _channel = null;
    _isConnecting = false;
  }

  void sendMessage(dynamic message) {
    if (_isConnected && _channel != null) {
      _channel!.sink.add(message);
    } else {
      debugPrint('Cannot send message: WebSocket not connected');
    }
  }

  void updateMode(String mode) {
    if (_currentMode != mode) {
       debugPrint("WS: Switching Journey from $_currentMode to $mode");
       _currentMode = mode;
       if (_isConnected) {
         disconnect(); // Close existing stale session
       }
    }
  }

  void dispose() {
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _connectionStateController.close();
    _statusController.close();
    _messageController.close();
  }
}

final webSocketService = WebSocketService();
