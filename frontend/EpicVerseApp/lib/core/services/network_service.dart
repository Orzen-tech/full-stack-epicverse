import 'dart:async';
import 'dart:io';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';

enum NetworkStatus {
  connected,
  noInternet,
  poorNetwork,
}

class NetworkService {
  static final NetworkService _instance = NetworkService._internal();
  factory NetworkService() => _instance;
  NetworkService._internal();

  final Connectivity _connectivity = Connectivity();
  final StreamController<NetworkStatus> _statusController =
      StreamController<NetworkStatus>.broadcast();

  StreamSubscription<List<ConnectivityResult>>? _connectivitySubscription;
  Timer? _latencyTimer;

  NetworkStatus _currentStatus = NetworkStatus.connected;
  NetworkStatus get currentStatus => _currentStatus;

  Stream<NetworkStatus> get statusStream => _statusController.stream;

  bool _isInitialized = false;

  void initialize() {
    if (_isInitialized) return;
    _isInitialized = true;

    // Listen to device connectivity changes
    _connectivitySubscription = _connectivity.onConnectivityChanged.listen(
      _handleConnectivityChange,
    );

    // Initial check
    _checkInitialConnectivity();

    // Periodic ping to check latency / poor network
    _latencyTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (_currentStatus != NetworkStatus.noInternet) {
        _checkNetworkQuality();
      }
    });
  }

  Future<void> _checkInitialConnectivity() async {
    final results = await _connectivity.checkConnectivity();
    await _handleConnectivityChange(results);
  }

  Future<void> _handleConnectivityChange(List<ConnectivityResult> results) async {
    if (results.contains(ConnectivityResult.none) || results.isEmpty) {
      _updateStatus(NetworkStatus.noInternet);
      return;
    }

    // Device network interface is active — check internet reachability and quality
    await _checkNetworkQuality();
  }

  Future<void> _checkNetworkQuality() async {
    try {
      final stopwatch = Stopwatch()..start();

      // Ping a reliable DNS server socket to measure latency
      final socket = await Socket.connect(
        '8.8.8.8',
        53,
        timeout: const Duration(seconds: 4),
      );
      stopwatch.stop();
      socket.destroy();

      final latencyMs = stopwatch.elapsedMilliseconds;

      if (latencyMs > 2500) {
        _updateStatus(NetworkStatus.poorNetwork);
      } else {
        _updateStatus(NetworkStatus.connected);
      }
    } catch (_) {
      // If socket ping fails, attempt HTTP backup check
      try {
        final result = await InternetAddress.lookup('google.com')
            .timeout(const Duration(seconds: 3));
        if (result.isNotEmpty && result[0].rawAddress.isNotEmpty) {
          _updateStatus(NetworkStatus.connected);
        } else {
          _updateStatus(NetworkStatus.noInternet);
        }
      } catch (_) {
        _updateStatus(NetworkStatus.noInternet);
      }
    }
  }

  void _updateStatus(NetworkStatus newStatus) {
    if (_currentStatus != newStatus) {
      debugPrint('🌐 [NetworkService] Status changed: $_currentStatus -> $newStatus');
      _currentStatus = newStatus;
      _statusController.add(newStatus);
    }
  }

  void dispose() {
    _connectivitySubscription?.cancel();
    _latencyTimer?.cancel();
    _statusController.close();
    _isInitialized = false;
  }
}

final networkService = NetworkService();
