import 'package:flutter_test/flutter_test.dart';
import 'package:epicverse/core/network/websocket_service.dart';

void main() {
  group('reconnectDelayMs', () {
    test('backs off exponentially: 1s, 2s, 4s, 8s, 16s', () {
      expect([0, 1, 2, 3, 4].map((a) => reconnectDelayMs(a, immediate: false)).toList(),
          [1000, 2000, 4000, 8000, 16000]);
    });

    test('is capped at 30 seconds', () {
      expect(reconnectDelayMs(5, immediate: false), 30000);
      expect(reconnectDelayMs(12, immediate: false), 30000);
    });

    test('adds up to 0.5s of jitter', () {
      expect(reconnectDelayMs(0, immediate: false, jitter01: 1.0), 1500);
      expect(reconnectDelayMs(2, immediate: false, jitter01: 0.5), 4250);
    });

    test('a server-requested renewal reconnects almost immediately', () {
      expect(reconnectDelayMs(0, immediate: true), 300);
      expect(reconnectDelayMs(5, immediate: true), 300);
    });
  });
}
