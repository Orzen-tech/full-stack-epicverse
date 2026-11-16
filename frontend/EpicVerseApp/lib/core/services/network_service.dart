import 'dart:async';
import 'dart:io';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart' show WidgetsBinding, AppLifecycleState;
import '../network/api_client.dart' show probeBackendHealth;

enum NetworkStatus {
  connected,
  noInternet,
  poorNetwork,
  // The phone has internet, but EpicVerse itself can't be reached (outage, or a Wi-Fi sign-in page in the way).
  serverUnreachable,
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

    // Periodic check of latency / reachability. Only while the app is on screen: it costs
    // battery and a tiny backend request, and nothing is shown to a user who isn't looking.
    _latencyTimer = Timer.periodic(const Duration(seconds: 30), (_) {
      final lifecycle = WidgetsBinding.instance.lifecycleState;
      if (lifecycle != null && lifecycle != AppLifecycleState.resumed) return;
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

  int _backendMisses = 0;

  /// Asks the backend whether it answers (a reply of any kind below 500 means yes), then checks
  /// the internet itself. A backend that is waking from idle can take 15 s+, so one missed probe
  /// is never reported as an outage: it takes two in a row.
  Future<void> _checkNetworkQuality() async {
    final status = await probeBackendHealth();
    final backendReachable = status != null && status < 500;
    _backendMisses = backendReachable ? 0 : _backendMisses + 1;

    final internet = await _checkInternet();
    final result = assessNetwork(
      backendReachable: backendReachable,
      backendMisses: _backendMisses,
      internetUp: internet.up,
      internetLatencyMs: internet.latencyMs,
    );
    if (result != null) _updateStatus(result);
  }

  /// Is the internet itself usable, and how quick is a plain connection? Null latency means the
  /// quick socket check was blocked but a DNS lookup still worked.
  Future<({bool up, int? latencyMs})> _checkInternet() async {
    try {
      final stopwatch = Stopwatch()..start();
      final socket = await Socket.connect('8.8.8.8', 53, timeout: const Duration(seconds: 4));
      stopwatch.stop();
      socket.destroy();
      return (up: true, latencyMs: stopwatch.elapsedMilliseconds);
    } catch (_) {
      try {
        final result = await InternetAddress.lookup('google.com').timeout(const Duration(seconds: 3));
        final ok = result.isNotEmpty && result[0].rawAddress.isNotEmpty;
        return (up: ok, latencyMs: null);
      } catch (_) {
        return (up: false, latencyMs: null);
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

/// Decides what the network banner should say. Returns null for "keep the current status for now".
///
///  * no internet                                   -> [NetworkStatus.noInternet]
///  * internet fine, backend missed fewer than 2    -> null (a single miss is usually the backend
///    probes in a row                                  waking from idle, not an outage)
///  * internet fine, backend missed 2+ in a row     -> [NetworkStatus.serverUnreachable]
///  * everything answers, but the connection is slow-> [NetworkStatus.poorNetwork]
///  * everything answers                            -> [NetworkStatus.connected]
@visibleForTesting
NetworkStatus? assessNetwork({
  required bool backendReachable,
  required int backendMisses,
  required bool internetUp,
  required int? internetLatencyMs,
}) {
  if (!internetUp) return NetworkStatus.noInternet;
  if (!backendReachable) {
    return backendMisses >= 2 ? NetworkStatus.serverUnreachable : null;
  }
  return (internetLatencyMs ?? 0) > 2500 ? NetworkStatus.poorNetwork : NetworkStatus.connected;
}
