import 'dart:async';
import 'dart:collection';

import 'package:connectivity_plus/connectivity_plus.dart';
import 'package:epicverse/core/services/network_service.dart';
import 'package:flutter/widgets.dart' show AppLifecycleState;
import 'package:flutter_test/flutter_test.dart';

// W3: NetworkService health checks with every dependency faked (no real timers, sockets,
// platform channels or backend).

class _FakeTimer implements Timer {
  _FakeTimer(this.delay, this.callback);
  final Duration delay;
  final void Function() callback;
  bool _active = true;
  @override
  bool get isActive => _active;
  @override
  int get tick => 0;
  @override
  void cancel() => _active = false;
  void fire() {
    if (!_active) return;
    _active = false;
    callback();
  }
}

class _Harness {
  final timers = <_FakeTimer>[];
  final connectivity = StreamController<List<ConnectivityResult>>.broadcast();
  final probeResults = Queue<Object?>(); // int? status, an Exception, or a Completer<int?> to hold the probe open
  final emitted = <NetworkStatus>[];
  int probeCalls = 0;
  int inFlight = 0;
  int maxInFlight = 0;
  ({bool up, int? latencyMs}) internet = (up: true, latencyMs: 50);
  bool internetThrows = false;
  DateTime now = DateTime(2026, 1, 1);
  late final NetworkService service;

  _Harness({
    bool foreground = true,
    Future<List<ConnectivityResult>> Function()? initial,
  }) {
    service = NetworkService.forTest(
      probe: () {
        probeCalls++;
        inFlight++;
        if (inFlight > maxInFlight) maxInFlight = inFlight;
        final next = probeResults.isEmpty ? 200 : probeResults.removeFirst();
        final Future<int?> f;
        if (next is Completer<int?>) {
          f = next.future;
        } else if (next is Exception) {
          f = Future<int?>.error(next);
        } else {
          f = Future<int?>.value(next as int?);
        }
        return f.whenComplete(() => inFlight--);
      },
      internetCheck: () async {
        if (internetThrows) throw Exception('internet check blew up');
        return internet;
      },
      timerFactory: (d, cb) {
        final t = _FakeTimer(d, cb);
        timers.add(t);
        return t;
      },
      connectivityChanges: connectivity.stream,
      initialConnectivity: initial ?? () async => [ConnectivityResult.wifi],
      now: () => now,
      foreground: foreground,
    );
  }

  List<_FakeTimer> get activeTimers => timers.where((t) => t.isActive).toList();

  Future<void> start() async {
    service.initialize();
    service.statusStream.listen(emitted.add);
    await settle();
  }

  /// Holds the next probe open; returns the completer that releases it.
  Completer<int?> holdNextProbe() {
    final c = Completer<int?>();
    probeResults.add(c);
    return c;
  }

  Future<void> fireRetry() async {
    final active = activeTimers;
    expect(active, hasLength(1), reason: 'exactly one retry should be scheduled');
    now = now.add(active.single.delay);
    active.single.fire();
    await settle();
  }

  void dispose() {
    service.dispose();
    connectivity.close();
  }
}

Future<void> settle() async {
  for (var i = 0; i < 20; i++) {
    await Future<void>.delayed(Duration.zero);
  }
}

void main() {
  test('healthy start: one check, then NO periodic probe at all', () async {
    final h = _Harness();
    await h.start();
    expect(h.probeCalls, 1);
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers, isEmpty);
    h.dispose();
  });

  test('backend reachable while the external internet/DNS check fails: connected, not offline', () async {
    final h = _Harness()..internet = (up: false, latencyMs: null);
    await h.start();
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers, isEmpty);
    h.dispose();
  });

  test('backend network failure: one miss keeps the status and retries in 30 s; two = unreachable', () async {
    final h = _Harness();
    h.probeResults.addAll([null, null]);
    await h.start();
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers.single.delay, const Duration(seconds: 30));
    await h.fireRetry();
    expect(h.service.currentStatus, NetworkStatus.serverUnreachable);
    h.dispose();
  });

  test('backend 5xx counts as a failure', () async {
    final h = _Harness();
    h.probeResults.addAll([503, 500]);
    await h.start();
    await h.fireRetry();
    expect(h.service.currentStatus, NetworkStatus.serverUnreachable);
    h.dispose();
  });

  test('noInternet is not a dead end: retried with back-off and recovers on a later check', () async {
    final h = _Harness()..internet = (up: false, latencyMs: null);
    h.probeResults.add(null);
    await h.start();
    expect(h.service.currentStatus, NetworkStatus.noInternet);
    expect(h.activeTimers.single.delay, const Duration(seconds: 30));
    h.internet = (up: true, latencyMs: 40); // the next probe defaults to 200
    await h.fireRetry();
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers, isEmpty);
    h.dispose();
  });

  test('connectivity "none" sets offline at once and still schedules a re-check', () async {
    final h = _Harness();
    await h.start();
    h.connectivity.add([ConnectivityResult.none]);
    await settle();
    expect(h.service.currentStatus, NetworkStatus.noInternet);
    expect(h.activeTimers.single.delay, const Duration(seconds: 30));
    h.dispose();
  });

  test('back-off 30 s -> 60 s -> 120 s -> 5 min (capped), reset after recovery', () async {
    final h = _Harness();
    h.probeResults.addAll([null, null, null, null, null, null]);
    await h.start();
    final delays = <Duration>[];
    for (var i = 0; i < 5; i++) {
      delays.add(h.activeTimers.single.delay);
      await h.fireRetry();
    }
    expect(delays, const [
      Duration(seconds: 30),
      Duration(seconds: 60),
      Duration(seconds: 120),
      Duration(minutes: 5),
      Duration(minutes: 5),
    ]);
    await h.fireRetry(); // probe now succeeds (default 200)
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers, isEmpty, reason: 'healthy: no timer');

    // A new failure starts the back-off from the beginning again.
    h.probeResults.add(null);
    h.now = h.now.add(const Duration(minutes: 1));
    h.connectivity.add([ConnectivityResult.mobile]);
    await settle();
    expect(h.activeTimers.single.delay, const Duration(seconds: 30));
    h.dispose();
  });

  test('API failure and a direct request during a running check start no second probe and no follow-up', () async {
    final h = _Harness();
    await h.start(); // probe #1 (healthy)
    final hold = h.holdNextProbe();
    h.now = h.now.add(const Duration(minutes: 1));
    unawaited(h.service.requestCheck()); // probe #2, held open
    await settle();
    expect(h.service.isChecking, isTrue);

    h.service.reportBackendResponse(null); // a failing API request
    h.service.reportBackendResponse(503);
    await h.service.requestCheck(); // any other direct trigger
    await settle();
    expect(h.probeCalls, 2, reason: 'no overlapping probe');

    hold.complete(200);
    await settle();
    expect(h.service.isChecking, isFalse);
    expect(h.probeCalls, 2, reason: 'API triggers do not queue a follow-up');
    expect(h.maxInFlight, 1);
    h.dispose();
  });

  test('a scheduled retry timer firing during a running check does not run a concurrent check', () async {
    final h = _Harness();
    await h.start();
    final hold = h.holdNextProbe();
    h.now = h.now.add(const Duration(minutes: 1));
    unawaited(h.service.requestCheck()); // probe #2, held open
    await settle();
    h.connectivity.add([ConnectivityResult.none]); // goes offline mid-check: schedules a retry
    await settle();
    expect(h.activeTimers, hasLength(1));
    h.activeTimers.single.fire(); // the retry fires while probe #2 is still running
    await settle();
    expect(h.probeCalls, 2, reason: 'timer did not start a second probe');
    expect(h.maxInFlight, 1);

    h.probeResults.add(null); // the single follow-up check (queued by the connectivity change)
    h.internet = (up: false, latencyMs: null);
    hold.complete(200);
    await settle();
    expect(h.probeCalls, 3);
    expect(h.maxInFlight, 1);
    expect(h.service.currentStatus, NetworkStatus.noInternet);
    expect(h.activeTimers, hasLength(1), reason: 'the retry chain continues after the follow-up');
    h.dispose();
  });

  test('guard is released when the probe throws, and the failure counts as a miss', () async {
    final h = _Harness();
    h.probeResults.add(Exception('boom'));
    await h.start();
    expect(h.service.isChecking, isFalse);
    expect(h.activeTimers.single.delay, const Duration(seconds: 30));
    await h.fireRetry(); // later check runs normally
    expect(h.probeCalls, 2);
    h.dispose();
  });

  test('a connectivity change during a running check causes exactly one follow-up check', () async {
    final h = _Harness();
    await h.start();
    final hold = h.holdNextProbe();
    h.now = h.now.add(const Duration(minutes: 1));
    unawaited(h.service.requestCheck());
    await settle();
    h.connectivity.add([ConnectivityResult.none]);
    h.connectivity.add([ConnectivityResult.none]);
    await settle();
    expect(h.probeCalls, 2);
    h.probeResults.add(null); // follow-up probe fails
    h.internet = (up: false, latencyMs: null);
    hold.complete(200); // stale "reachable" result from before the drop
    await settle();
    expect(h.probeCalls, 3, reason: 'one follow-up, not one per event');
    expect(h.maxInFlight, 1);
    expect(h.service.currentStatus, NetworkStatus.noInternet);
    h.dispose();
  });

  test('stale result after a newer connectivity event: no false "connected" is ever emitted', () async {
    final h = _Harness();
    await h.start();
    final hold = h.holdNextProbe();
    h.now = h.now.add(const Duration(minutes: 1));
    unawaited(h.service.requestCheck());
    await settle();
    h.connectivity.add([ConnectivityResult.none]);
    await settle();
    expect(h.emitted, [NetworkStatus.noInternet]);
    h.probeResults.add(null);
    h.internet = (up: false, latencyMs: null);
    hold.complete(200); // stale reachable result
    await settle();
    expect(h.emitted, [NetworkStatus.noInternet], reason: 'no "connected" flicker from the stale result');
    h.dispose();
  });

  test('stale result does not update the miss counter', () async {
    final h = _Harness();
    await h.start();
    final hold = h.holdNextProbe();
    h.now = h.now.add(const Duration(minutes: 1));
    unawaited(h.service.requestCheck());
    await settle();
    h.connectivity.add([ConnectivityResult.mobile]); // network changed mid-check
    await settle();
    h.probeResults.add(null); // follow-up: one genuine miss
    hold.complete(null); // stale miss: must NOT count
    await settle();
    // Counted stale miss + follow-up miss would be 2 = "Can't reach EpicVerse".
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.emitted, isEmpty);
    expect(h.activeTimers.single.delay, const Duration(seconds: 30), reason: 'one unconfirmed miss: retry');
    h.dispose();
  });

  test('"Slow connection" clears on a real API success via one spaced confirming check', () async {
    final h = _Harness()..internet = (up: true, latencyMs: 3200);
    await h.start();
    expect(h.service.currentStatus, NetworkStatus.poorNetwork);
    expect(h.activeTimers.single.delay, const Duration(seconds: 30));
    h.internet = (up: true, latencyMs: 60); // the network recovers

    h.now = h.now.add(const Duration(seconds: 10));
    h.service.reportBackendResponse(200); // within 30 s of the last check: throttled
    await settle();
    expect(h.probeCalls, 1);
    expect(h.service.currentStatus, NetworkStatus.poorNetwork);

    h.now = h.now.add(const Duration(seconds: 25));
    h.service.reportBackendResponse(200);
    h.service.reportBackendResponse(204); // burst: still one check
    await settle();
    expect(h.probeCalls, 2);
    expect(h.maxInFlight, 1);
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers, isEmpty, reason: 'healthy again: back-off retry cancelled');
    h.dispose();
  });

  test('connectivity change while backgrounded does not probe; resuming checks at once', () async {
    final h = _Harness();
    await h.start();
    h.service.handleLifecycleChange(AppLifecycleState.paused);
    h.connectivity.add([ConnectivityResult.mobile]);
    h.service.reportBackendResponse(null);
    await settle();
    expect(h.probeCalls, 1);
    h.now = h.now.add(const Duration(minutes: 2));
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await settle();
    expect(h.probeCalls, 2);
    h.dispose();
  });

  test('"none" while backgrounded sets offline but creates no timer', () async {
    final h = _Harness();
    await h.start();
    h.service.handleLifecycleChange(AppLifecycleState.paused);
    h.connectivity.add([ConnectivityResult.none]);
    await settle();
    expect(h.service.currentStatus, NetworkStatus.noInternet);
    expect(h.activeTimers, isEmpty);
    expect(h.probeCalls, 1);
    h.dispose();
  });

  test('resume inside the 30 s throttle: skipped when nothing changed, forced after a background network change',
      () async {
    final h = _Harness();
    await h.start(); // healthy check at t0

    // Nothing changed while away: quick return inside 30 s is throttled.
    h.service.handleLifecycleChange(AppLifecycleState.paused);
    h.now = h.now.add(const Duration(seconds: 5));
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await settle();
    expect(h.probeCalls, 1);

    // The network changed while away (still "up", e.g. a new Wi-Fi): resume must re-check.
    h.service.handleLifecycleChange(AppLifecycleState.paused);
    h.connectivity.add([ConnectivityResult.wifi]);
    await settle();
    h.now = h.now.add(const Duration(seconds: 5)); // still within 30 s of the last check
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await settle();
    expect(h.probeCalls, 2);

    // The flag was cleared by that check: the next quick resume is throttled again.
    h.service.handleLifecycleChange(AppLifecycleState.paused);
    h.now = h.now.add(const Duration(seconds: 5));
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await settle();
    expect(h.probeCalls, 2);
    h.dispose();
  });

  test('going to the background cancels a pending retry; resume checks immediately', () async {
    final h = _Harness();
    h.probeResults.addAll([null, 200]);
    await h.start();
    expect(h.activeTimers, hasLength(1));
    h.service.handleLifecycleChange(AppLifecycleState.hidden);
    expect(h.activeTimers, isEmpty);
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await settle();
    expect(h.probeCalls, 2);
    expect(h.service.currentStatus, NetworkStatus.connected);
    h.dispose();
  });

  test('"inactive" (notification shade) is not a background/resume cycle', () async {
    final h = _Harness();
    await h.start();
    h.service.handleLifecycleChange(AppLifecycleState.inactive);
    h.now = h.now.add(const Duration(minutes: 2));
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await settle();
    expect(h.probeCalls, 1);
    h.dispose();
  });

  test('real API answer clears "offline"/"unreachable"; failures from a healthy state probe at most once per 30 s',
      () async {
    final h = _Harness();
    h.probeResults.addAll([null, null]);
    await h.start();
    await h.fireRetry();
    expect(h.service.currentStatus, NetworkStatus.serverUnreachable);
    h.service.reportBackendResponse(401); // any real answer below 500
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.activeTimers, isEmpty);

    final calls = h.probeCalls;
    h.now = h.now.add(const Duration(seconds: 10)); // last check was < 30 s ago
    h.service.reportBackendResponse(null);
    await settle();
    expect(h.probeCalls, calls, reason: 'throttled');
    h.now = h.now.add(const Duration(seconds: 30));
    h.service.reportBackendResponse(502);
    h.service.reportBackendResponse(null);
    await settle();
    expect(h.probeCalls, calls + 1);
    h.dispose();
  });

  test('initial connectivity lookup throwing falls back to a real check', () async {
    final h = _Harness(initial: () async => throw Exception('no plugin'));
    await h.start();
    expect(h.probeCalls, 1);
    expect(h.service.currentStatus, NetworkStatus.connected);
    h.dispose();
  });

  test('internet check throwing is treated as "internet down" (only matters when the backend failed)', () async {
    final h = _Harness()..internetThrows = true;
    h.probeResults.add(null);
    await h.start();
    expect(h.service.currentStatus, NetworkStatus.noInternet);
    expect(h.service.isChecking, isFalse);
    await h.fireRetry(); // backend answers (200): reachable wins even though the internet check throws
    expect(h.service.currentStatus, NetworkStatus.connected);
    h.dispose();
  });

  test('dispose cancels a pending retry and ignores later timer, API, lifecycle and direct triggers', () async {
    final h = _Harness();
    h.probeResults.add(null);
    await h.start();
    final pending = h.activeTimers.single;
    h.service.dispose();
    expect(pending.isActive, isFalse);
    pending.fire(); // a cancelled timer does nothing
    h.service.reportBackendResponse(null);
    h.service.handleLifecycleChange(AppLifecycleState.resumed);
    await h.service.requestCheck();
    await settle();
    expect(h.probeCalls, 1);
    expect(h.activeTimers, isEmpty);
    h.connectivity.close();
  });

  test('dispose during a held probe: the late result changes nothing and schedules nothing', () async {
    final h = _Harness();
    await h.start(); // healthy
    final hold = h.holdNextProbe();
    h.now = h.now.add(const Duration(minutes: 1));
    unawaited(h.service.requestCheck());
    await settle();
    expect(h.service.isChecking, isTrue);

    h.service.dispose(); // while the probe is genuinely in flight
    h.internet = (up: false, latencyMs: null);
    hold.complete(null); // would mean "offline" if it were applied
    await settle();
    expect(h.service.currentStatus, NetworkStatus.connected);
    expect(h.emitted, isEmpty);
    expect(h.activeTimers, isEmpty);
    expect(h.service.isChecking, isFalse, reason: 'guard released even after dispose');
    expect(h.probeCalls, 2);
    h.connectivity.close();
  });
}
