import 'package:flutter/foundation.dart';
import 'package:record/record.dart';
import '../core/network/websocket_service.dart';
import 'dart:async';
import 'dart:convert';

enum BuiltInKeyword {
  EPIC,
  PICOVOICE,
  BUMBLEBEE,
  COMPUTER
}

class WakeWordService {
  static final WakeWordService _instance = WakeWordService._internal();
  factory WakeWordService() => _instance;
  WakeWordService._internal();

  final _audioRecorder = AudioRecorder();
  StreamSubscription? _micSubscription;
  StreamSubscription? _msgSubscription;
  bool _isListening = false;
  BuiltInKeyword _currentKeyword = BuiltInKeyword.EPIC;
  Function()? _onDetected;

  bool get isListening => _isListening;
  BuiltInKeyword get currentKeyword => _currentKeyword;

  StreamSubscription? _connSub;

  Future<void> init({
    required Function() onWakeWordDetected,
    BuiltInKeyword keyword = BuiltInKeyword.EPIC,
  }) async {
    _onDetected = onWakeWordDetected;
    _currentKeyword = keyword;
    
    // Listen for wake word detection from WebSocket
    await _msgSubscription?.cancel();
    _msgSubscription = webSocketService.messages.listen((data) {
      try {
        if (data['type'] == 'wakeword_detected') {
          _onWakeWordDetected();
        }
      } catch (_) {}
    });

    // Auto-restart on reconnection
    await _connSub?.cancel();
    _connSub = webSocketService.connectionState.listen((connected) async {
      if (connected && _isListening) {
        debugPrint("WakeWordService: Connection restored, re-syncing...");
        // Wait a tiny bit for the sink to be ready
        await Future.delayed(const Duration(milliseconds: 300));
        // Re-call start to be absolutely sure the stream is sending to the NEW sink
        await startListening();
      }
    });
  }

  void _onWakeWordDetected() {
    if (_isListening) {
      stopListening();
      _onDetected?.call();
    }
  }

  Future<void> startListening() async {
    // Safety: Wait up to 2 seconds for WebSocket to settle if needed
    int retries = 0;
    while (!webSocketService.isConnected && retries < 4) {
      debugPrint("WakeWordService: Waiting for connection... ($retries)");
      await Future.delayed(const Duration(milliseconds: 500));
      retries++;
    }
    
    if (_isListening && webSocketService.isConnected) {
       // Already active, but re-send command to be sure backend knows
       webSocketService.sendMessage(jsonEncode({"type": "start_wakeword"}));
       return;
    }

    debugPrint("WakeWordService: Checking mic permission...");
    final hasPermission = await _audioRecorder.hasPermission();
    debugPrint("WakeWordService: Mic permission: $hasPermission");
    
    if (hasPermission) {
      _isListening = true;
      
      debugPrint("WakeWordService: Sending start_wakeword command...");
      // Tell backend to start looking for wake word
      webSocketService.sendMessage(jsonEncode({"type": "start_wakeword"}));
      
      final stream = await _audioRecorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        numChannels: 1,
        sampleRate: 16000,
      ));

      _micSubscription = stream.listen((data) {
        webSocketService.sendMessage(data);
      });
      
      debugPrint("WakeWordService: Started listening for 'Hey Epic'");
    }
  }

  Future<void> stopListening() async {
    if (!_isListening) return;
    
    _isListening = false;
    await _micSubscription?.cancel();
    _micSubscription = null;
    await _audioRecorder.stop();
    
    webSocketService.sendMessage(jsonEncode({"type": "stop_wakeword"}));
    debugPrint("WakeWordService: Stopped listening");
  }

  Future<void> changeKeyword(BuiltInKeyword newKeyword) async {
    _currentKeyword = newKeyword;
  }

  Future<void> dispose() async {
    await stopListening();
    await _connSub?.cancel();
    // _audioRecorder.dispose(); // Do not dispose here; singleton needs it alive for mode changes
  }
}

final wakeWordService = WakeWordService();
