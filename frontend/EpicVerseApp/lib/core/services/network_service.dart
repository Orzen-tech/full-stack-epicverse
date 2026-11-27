import 'dart:async';
import 'dart:io';
import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart' show AppLifecycleListener, AppLifecycleState, WidgetsBinding;
import '../network/api_client.dart' show probeBackendHealth;

enum NetworkStatus {
  connected,
  noInternet,
  poorNetwork,
  // The phone has internet, but EpicVerse itself can't be reached (outage, or a Wi-Fi sign-in page in the way).
  serverUnreachable,
}

typedef BackendProbe = Future<int?> Function();
typedef InternetCheck = Future<({bool up, int? latencyMs})> Function();
typedef RetryTimerFactory = Timer Function(Duration delay, void Function() callback);

/// Network health for the banner and error messages.
///
/// No periodic probe while healthy. A check runs only when something suggests the state may have
/// changed: start-up, the app coming back to the foreground, a connectivity change, or a real API
/// request failing (network error or 5xx). While the status is degraded (offline, slow, backend
/// unreachable, or one unconfirmed miss) it re-checks with back-off: 30 s, 60 s, 120 s, then every
/// 5 minutes, and the back-off resets on recovery. Nothing is probed while the app is in the
/// background, and only one check can run at a time.
class NetworkService {
  static final NetworkService _instance = NetworkService._internal();
  factory NetworkService() => _instance;
  NetworkService._internal()
      : _probe = probeBackendHealth,
        _internetCheck = _defaultInternetCheck,
        _timerFactory = Timer.new,
        _connectivityStream = (() => Connectivity().onConnectivityChanged),
        _checkConnectivity = (() => Connectivity().checkConnectivity()),
        _now = DateTime.now,
        _watchLifecycle = true;

  /// Test instance: every outside dependency injected, no platform channels, no app lifecycle
  /// binding (drive it with [handleLifecycleChange]).
  @visibleForTesting
  NetworkService.forTest({
    required BackendProbe probe,
    required InternetCheck internetCheck,
    required RetryTimerFactory timerFactory,
    required Stream<List<ConnectivityResult>> connectivityChanges,
    Future<List<ConnectivityResult>> Function()? initialConnectivity,
    DateTime Function()? now,
    bool foreground = true,
  })  : _probe = probe,
        _internetCheck = internetCheck,
        _timerFactory = timerFactory,
        _connectivityStream = (() => connectivityChanges),
        _checkConnectivity = initialConnectivity ?? (() async => [ConnectivityResult.wifi]),
        _now = now ?? DateTime.now,
        _watchLifecycle = false,
        _foreground = foreground;

  /// Back-off while degraded; the last value repeats.
  static const List<Duration> retryDelays = [
    Duration(seconds: 30),
    Duration(seconds: 60),
    Duration(seconds: 120),
    Duration(minutes: 5),
  ];

  /// Minimum gap between checks started by a failed API request or by a quick return to the app.
  static const Duration minCheckSpacing = Duration(seconds: 30);

  final BackendProbe _probe;
  final InternetCheck _internetCheck;
  final RetryTimerFactory _timerFactory;
  final Stream<List<ConnectivityResult>> Function() _connectivityStream;
  final Future<List<ConnectivityResult>> Function() _checkConnectivity;
  final DateTime Function() _now;
  final bool _watchLifecycle;

  StreamController<NetworkStatus> _statusController = StreamController<NetworkStatus>.broadcast();
  StreamSubscription<List<ConnectivityResult>>? _connectivitySubscription;
  AppLifecycleListener? _lifecycleListener;
  Timer? _retryTimer;

  NetworkStatus _currentStatus = NetworkStatus.connected;
  NetworkStatus get currentStatus => _currentStatus;

  Stream<NetworkStatus> get statusStream => _statusController.stream;

  bool _isInitialized = false;
  bool _foreground = true;
  bool _checking = false;
  bool _recheckRequested = false;
  bool _connectivityChangedInBackground = false;
  int _backendMisses = 0;
  int _retryStep = 0;
  DateTime? _lastCheckAt;

  @visibleForTesting
  bool get isChecking => _checking;
  @visibleForTesting
  bool get hasScheduledRetry => _retryTimer?.isActive ?? false;

  void initialize() {
    if (_isInitialized) return;
    _isInitialized = true;
    if (_statusController.isClosed) _statusController = StreamController<NetworkStatus>.broadcast();

    if (_watchLifecycle) {
      final state = WidgetsBinding.instance.lifecycleState;
      _foreground = state == null || !_isBackground(state);
      _lifecycleListener = AppLifecycleListener(onStateChange: handleLifecycleChange);
    }

    // Listen to device connectivity changes
    _connectivitySubscription = _connectivityStream().listen(_handleConnectivityChange);

    // Initial check
    unawaited(_checkInitialConnectivity());
  }

  static bool _isBackground(AppLifecycleState s) =>
      s == AppLifecycleState.hidden || s == AppLifecycleState.paused || s == AppLifecycleState.detached;

  /// Foreground/background tracking. "inactive" (e.g. the notification shade) is not a change.
  @visibleForTesting
  void handleLifecycleChange(AppLifecycleState state) {
    if (!_isInitialized) return;
    if (_isBackground(state)) {
      if (!_foreground) return;
      _foreground = false;
      _cancelRetry(); // no probing in the background
      return;
    }
    if (state != AppLifecycleState.resumed || _foreground) return;
    _foreground = true;
    // Back on screen: check promptly, unless a healthy check finished moments ago AND the network
    // did not change while the app was in the background.
    final recentAndHealthy = !_checkSpacingElapsed && _isHealthy;
    if (recentAndHealthy && !_connectivityChangedInBackground) return;
    if (_checking) {
      _recheckRequested = true; // the running check is stale; one follow-up runs when it ends
    } else {
      unawaited(requestCheck());
    }
  }

  Future<void> _checkInitialConnectivity() async {
    List<ConnectivityResult> results;
    try {
      results = await _checkConnectivity();
    } catch (_) {
      results = const [ConnectivityResult.other]; // unknown: let the real check decide
    }
    await _handleConnectivityChange(results);
  }

  Future<void> _handleConnectivityChange(List<ConnectivityResult> results) async {
    if (!_isInitialized) return;
    // A check already running may report a network that just changed: run ONE more afterwards.
    if (_checking) _recheckRequested = true;
    // Remembered until the next check starts, so resuming always re-checks a changed network.
    if (!_foreground) _connectivityChangedInBackground = true;
    if (results.contains(ConnectivityResult.none) || results.isEmpty) {
      _updateStatus(NetworkStatus.noInternet);
      _scheduleNext(); // keep re-checking with back-off (and the next connectivity event checks at once)
      return;
    }
    // Device network interface is active — check internet reachability and quality (foreground only;
    // in the background requestCheck does nothing and the resume check covers it).
    await requestCheck();
  }

  /// Result of a real API request, reported by ApiClient. Any answer below 500 proves the
  /// backend is reachable; a network error or a 5xx (null / >= 500) asks for a confirming check.
  void reportBackendResponse(int? statusCode) {
    if (!_isInitialized) return;
    if (statusCode != null && statusCode < 500) {
      _backendMisses = 0;
      if (_currentStatus == NetworkStatus.poorNetwork) {
        // A real answer says nothing about speed: confirm with one (spaced, guarded) check so
        // "Slow connection" clears soon after the network recovers instead of waiting for the
        // next back-off retry (up to 5 minutes).
        if (_checkSpacingElapsed) unawaited(requestCheck());
        return;
      }
      // A real answer just arrived: neither "offline" nor "can't reach EpicVerse" is true.
      _updateStatus(NetworkStatus.connected);
      _cancelRetry();
      _retryStep = 0;
      return;
    }
    // Only start a confirming check from a healthy state; degraded states already have a retry
    // scheduled. Spaced out so a burst of failing requests sends at most one probe.
    if (_isHealthy && !hasScheduledRetry && _checkSpacingElapsed) {
      unawaited(requestCheck());
    }
  }

  bool get _checkSpacingElapsed {
    final last = _lastCheckAt;
    return last == null || _now().difference(last) >= minCheckSpacing;
  }

  bool get _isHealthy => _currentStatus == NetworkStatus.connected && _backendMisses == 0;

  /// Runs one check now unless the app is in the background or a check is already running.
  @visibleForTesting
  Future<void> requestCheck() async {
    if (!_isInitialized || !_foreground || _checking) return;
    _checking = true;
    _connectivityChangedInBackground = false; // this check covers any change seen in the background
    _cancelRetry();
    try {
      await _checkNetworkQuality();
    } catch (e) {
      debugPrint('🌐 [NetworkService] check failed: ${e.runtimeType}');
    } finally {
      _checking = false;
      _lastCheckAt = _now();
    }
    if (_recheckRequested && _isInitialized && _foreground) {
      _recheckRequested = false;
      return requestCheck();
    }
    _recheckRequested = false;
    _scheduleNext();
  }

  /// Asks the backend whether it answers (a reply of any kind below 500 means yes), then checks
  /// the internet itself. A backend that is waking from idle can take 15 s+, so one missed probe
  /// is never reported as an outage: it takes two in a row.
  Future<void> _checkNetworkQuality() async {
    int? status;
    try {
      status = await _probe();
    } catch (_) {
      status = null; // treated like a network error
    }
    // Disposed while the probe was running, or the network changed meanwhile (a follow-up check is
    // queued): this result is stale, so it must not touch the miss count or the status.
    if (_resultIsStale) return;
    final backendReachable = status != null && status < 500;

    ({bool up, int? latencyMs}) internet;
    try {
      internet = await _internetCheck();
    } catch (_) {
      internet = (up: false, latencyMs: null);
    }
    if (_resultIsStale) return;
    _backendMisses = backendReachable ? 0 : _backendMisses + 1;
    final result = assessNetwork(
      backendReachable: backendReachable,
      backendMisses: _backendMisses,
      internetUp: internet.up,
      internetLatencyMs: internet.latencyMs,
    );
    if (result != null) _updateStatus(result);
  }

  bool get _resultIsStale => !_isInitialized || _recheckRequested;

  /// Healthy: no timer at all. Degraded: one retry after the current back-off step.
  void _scheduleNext() {
    if (!_isInitialized) return;
    if (_isHealthy) {
      _retryStep = 0;
      _cancelRetry();
      return;
    }
    if (!_foreground || _retryTimer != null) return;
    final delay = retryDelays[_retryStep < retryDelays.length ? _retryStep : retryDelays.length - 1];
    if (_retryStep < retryDelays.length - 1) _retryStep++;
    _retryTimer = _timerFactory(delay, () {
      _retryTimer = null;
      unawaited(requestCheck());
    });
  }

  void _cancelRetry() {
    _retryTimer?.cancel();
    _retryTimer = null;
  }

  /// Is the internet itself usable, and how quick is a plain connection? Null latency means the
  /// quick socket check was blocked but a DNS lookup still worked.
  static Future<({bool up, int? latencyMs})> _defaultInternetCheck() async {
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
      if (!_statusController.isClosed) _statusController.add(newStatus);
    }
  }

  void dispose() {
    _isInitialized = false;
    _connectivitySubscription?.cancel();
    _connectivitySubscription = null;
    _cancelRetry();
    _lifecycleListener?.dispose();
    _lifecycleListener = null;
    _statusController.close();
  }
}

final networkService = NetworkService();

/// Decides what the network banner should say. Returns null for "keep the current status for now".
///
///  * backend answered, connection quick (or speed unknown) -> [NetworkStatus.connected]
///  * backend answered, connection slow                     -> [NetworkStatus.poorNetwork]
///    (a backend that answered means we are online, even if the external internet check failed)
///  * backend did not answer, no internet                   -> [NetworkStatus.noInternet]
///  * backend did not answer, internet fine, < 2 misses     -> null (a single miss is usually the
///                                                             backend waking from idle)
///  * backend did not answer, internet fine, 2+ misses      -> [NetworkStatus.serverUnreachable]
@visibleForTesting
NetworkStatus? assessNetwork({
  required bool backendReachable,
  required int backendMisses,
  required bool internetUp,
  required int? internetLatencyMs,
}) {
  if (backendReachable) {
    return (internetLatencyMs ?? 0) > 2500 ? NetworkStatus.poorNetwork : NetworkStatus.connected;
  }
  if (!internetUp) return NetworkStatus.noInternet;
  return backendMisses >= 2 ? NetworkStatus.serverUnreachable : null;
}
