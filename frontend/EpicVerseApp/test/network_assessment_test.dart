import 'package:epicverse/core/services/network_service.dart';
import 'package:flutter_test/flutter_test.dart';

NetworkStatus? _a({
  bool reachable = true,
  int misses = 0,
  bool internet = true,
  int? latency = 80,
}) =>
    assessNetwork(
      backendReachable: reachable,
      backendMisses: misses,
      internetUp: internet,
      internetLatencyMs: latency,
    );

void main() {
  test('everything answers quickly: connected', () {
    expect(_a(), NetworkStatus.connected);
  });

  test('everything answers but the connection is slow: slow-connection banner', () {
    expect(_a(latency: 3200), NetworkStatus.poorNetwork);
  });

  test('a blocked quick-socket check with a working backend is not "slow"', () {
    expect(_a(latency: null), NetworkStatus.connected);
  });

  test('ONE missed backend probe (e.g. a cold start) does NOT raise the banner', () {
    expect(_a(reachable: false, misses: 1), isNull);
  });

  test('two missed probes in a row while the internet works: "Can\'t reach EpicVerse"', () {
    expect(_a(reachable: false, misses: 2), NetworkStatus.serverUnreachable);
    expect(_a(reachable: false, misses: 5), NetworkStatus.serverUnreachable);
  });

  test('no internet is reported at once, even on the first miss', () {
    expect(_a(reachable: false, misses: 1, internet: false), NetworkStatus.noInternet);
    expect(_a(internet: false), NetworkStatus.noInternet);
  });
}
