import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math' as math;
import 'dart:typed_data';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart' show WidgetsBinding, AppLifecycleState;
import 'package:web_socket_channel/web_socket_channel.dart';
import 'package:web_socket_channel/io.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'api_config.dart';
import 'session_manager.dart';
import 'ssl_pinning_service.dart';

/// Delay before the next automatic reconnect attempt: exponential backoff
/// (1s, 2s, 4s ... capped at 30s) plus up to 0.5s of jitter; a server-requested
/// renewal reconnects almost immediately.
int reconnectDelayMs(int attempt, {required bool immediate, double jitter01 = 0.0}) {
  final baseSeconds = immediate ? 0.3 : math.min(30.0, math.pow(2, attempt).toDouble());
  return ((baseSeconds + jitter01 * 0.5) * 1000).round();
}

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
  final _sessionKickedController = StreamController<void>.broadcast();
  
  String _currentMode = 'Mode 1';
  bool _isConnecting = false;
  
  Completer<void>? _connectionCompleter;
  Timer? _reconnectTimer;

  // ── Automatic reconnect (enabled by the Companion screen while it is open) ──
  // Lets the app survive the 60-minute session/connection limit and brief network
  // drops without the user having to tap again. Off by default so no other screen
  // ever opens a voice session on its own.
  bool autoReconnect = false;
  bool _manualClose = false;       // disconnect() / mode change / kick: never auto-reconnect
  bool _renewPending = false;      // server sent session_renew: reconnect right away
  int _reconnectAttempts = 0;
  static const int _maxReconnectAttempts = 6;
  String? _lastLanguage;

  bool get isConnected => _isConnected;
  String get statusText => _statusText;
  Stream<dynamic> get messages => _messageController.stream;
  Stream<String> get statusTextStream => _statusController.stream;
  Stream<bool> get connectionState => _connectionStateController.stream;
  Stream<void> get sessionKicked => _sessionKickedController.stream;

  Future<void> connect({String? hostOrUrl, bool? isListening, String? game_mode, String? language}) async {
    debugPrint('[EpicVerse][WS] connect() called mode=${game_mode ?? _currentMode}');
    if (_isConnected) return;
    if (_isConnecting) {
       await (_connectionCompleter?.future ?? Future.value());
       return;
    }

    if (game_mode != null) {
      _currentMode = game_mode;
    }
    if (language != null) _lastLanguage = language;
    _manualClose = false;

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
    String queryParams = 'uid=$uid&mode=${Uri.encodeComponent(_currentMode)}&session_id=$sessionId&listening=$listenFlag&language=${Uri.encodeComponent(language ?? 'English')}';

    Uri wsUri = Uri.parse('${ApiConfig.wsUrl}?$queryParams');
    if (hostOrUrl != null && hostOrUrl.isNotEmpty) {
      wsUri = Uri.parse('${hostOrUrl.startsWith('ws') ? hostOrUrl : 'ws://$hostOrUrl'}/api/v1/ws/realtime?$queryParams');
    }

    try {
      debugPrint('[EpicVerse][WS] Opening channel uri=$wsUri');
      // Use a pinned HttpClient so WebSocket TLS is validated at the Dart level.
      // network_security_config.xml does NOT cover WebSocket connections.
      final HttpClient pinnedHttpClient =
          SslPinningService.createPinnedWebSocketHttpClient();
      _channel = IOWebSocketChannel.connect(
        wsUri,
        headers: {
          ...ApiConfig.headers,
          'Authorization': 'Bearer $idToken',
        },
        customClient: pinnedHttpClient,
      );
      
      _channel!.stream.listen(
        (message) {
          if (!_isConnected) {
            _isConnected = true;
            _isConnecting = false;
            _reconnectAttempts = 0;
            _renewPending = false;
            debugPrint('[EpicVerse][WS] CONNECTED');
            _statusController.add('Connected');
            _connectionStateController.add(true);
            if (!(_connectionCompleter?.isCompleted ?? true)) {
              _connectionCompleter?.complete();
            }
          }

          if (message is List<int>) {
             if (kDebugMode) debugPrint('[EpicVerse][WS] AUDIO chunk in bytes=${message.length}');
             _messageController.add(Uint8List.fromList(message));
             return;
          }

          try {
            final data = jsonDecode(message);
            if (kDebugMode) debugPrint('[EpicVerse][WS] MSG in type=${data['type']} status=${data['status']}');
            
            // Check for Session Kick — backend sends {"type": "SESSION_KICKED"}
            if (data['type'] == 'SESSION_KICKED') {
               debugPrint('[EpicVerse][WS] SESSION_KICKED — another device took over');
               _handleDisconnect(reconnect: false);
               _statusController.add('Logged out: Active elsewhere');
               _sessionKickedController.add(null);
               return;
            }

            // Server asks us to reconnect before the 60-minute session limit.
            // It closes the socket right after; _handleDisconnect reconnects.
            if (data['type'] == 'session_renew') {
               debugPrint('[EpicVerse][WS] session_renew — will reconnect when the socket closes');
               _renewPending = true;
            }

            if (data['type'] == 'connection_success') {
               debugPrint('[EpicVerse][WS] Unified Handshake Confirmed');
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
    _manualClose = true;
    _reconnectTimer?.cancel();
    _channel?.sink.close();
    _handleDisconnect(reconnect: false);
  }

  void _scheduleReconnect({bool immediate = false}) {
    if (!autoReconnect || _manualClose || _isConnected || _isConnecting) return;
    if (_reconnectTimer?.isActive ?? false) return;
    // Don't open voice sessions for an app that isn't on screen; the next mic tap
    // reconnects lazily as before.
    final lifecycle = WidgetsBinding.instance.lifecycleState;
    if (lifecycle != null && lifecycle != AppLifecycleState.resumed) return;
    if (!immediate && _reconnectAttempts >= _maxReconnectAttempts) {
      debugPrint('[EpicVerse][WS] auto-reconnect gave up after $_reconnectAttempts attempts');
      return;
    }
    final delayMs = reconnectDelayMs(_reconnectAttempts,
        immediate: immediate, jitter01: math.Random().nextDouble());
    if (!immediate) _reconnectAttempts++;
    debugPrint('[EpicVerse][WS] reconnecting in ${delayMs}ms (attempt $_reconnectAttempts, renew=$immediate)');
    _reconnectTimer = Timer(Duration(milliseconds: delayMs), () async {
      if (!autoReconnect || _manualClose || _isConnected || _isConnecting) return;
      try {
        await connect(language: _lastLanguage).timeout(const Duration(seconds: 20));
      } catch (e) {
        debugPrint('[EpicVerse][WS] auto-reconnect attempt failed: $e');
        _scheduleReconnect();
      }
    });
  }

  void _handleDisconnect({bool reconnect = true}) {
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

    if (reconnect) _scheduleReconnect(immediate: _renewPending);
  }

  void sendMessage(dynamic message) {
    if (_isConnected && _channel != null) {
      if (message is String) {
        debugPrint('[EpicVerse][WS] SEND text=${message.length > 80 ? '${message.substring(0, 80)}...' : message}');
      } else if (message is List<int>) {
        // Audio frames are spammy; only log occasionally
      }
      _channel!.sink.add(message);
    } else {
      debugPrint('[EpicVerse][WS] Cannot send: not connected');
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
    _sessionKickedController.close();
  }
}

final webSocketService = WebSocketService();
