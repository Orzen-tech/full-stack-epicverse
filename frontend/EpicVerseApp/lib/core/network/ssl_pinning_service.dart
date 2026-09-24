import 'dart:io';
import 'package:dio/dio.dart';
import 'package:dio/io.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show rootBundle;

/// Dart-level SSL pinning service.
///
/// Why this exists alongside network_security_config.xml:
/// - network_security_config.xml only protects Android OS-level connections.
/// - Dart's own TLS stack (used by Dio + WebSocket) is independent and
///   bypassed by Frida/SSL-bypass scripts on rooted devices.
/// - This class enforces certificate validation inside the Dart process itself.
///
/// How pinning is enforced: rather than trusting the OS's full CA trust
/// store (which happily accepts a MITM proxy's certificate once its CA is
/// installed as "trusted" on the device — e.g. Burp Suite on a rooted test
/// device), we build a [SecurityContext] that trusts ONLY the specific root
/// CA certificates below. The TLS handshake itself then fails for any
/// connection that doesn't chain up to one of these roots, regardless of
/// what the OS trust store says.
///
/// Root CAs are pinned instead of leaf certs because Cloud Run rotates its
/// leaf certificate every ~90 days. Pinning roots prevents app breakage.
class SslPinningService {
  SslPinningService._();

  static const List<String> _certAssets = [
    'assets/certs/gts_root_r1.pem', // Google Trust Services Root R1 (legacy RSA hierarchy)
    'assets/certs/gts_root_r2.pem', // Google Trust Services Root R2 (legacy RSA backup)
    'assets/certs/gts_root_r4.pem', // Google Trust Services Root R4 — actual root behind
                                     // Cloud Run's current "WE2" ECDSA intermediate, confirmed
                                     // via openssl s_client against the live backend.
    'assets/certs/isrg_root_x1.pem', // Let's Encrypt ISRG Root X1 (possible future CA)
  ];

  static SecurityContext? _pinnedContext;

  /// Loads the pinned root certificates into a restricted [SecurityContext].
  /// Must be awaited once at app startup, before any pinned Dio/WebSocket
  /// client is created (release/profile builds only — see [_isPinningActive]).
  static Future<void> preload() async {
    if (!_isPinningActive) {
      debugPrint('[SslPinning] DEBUG mode — pinning relaxed, skipping preload');
      return;
    }
    final ctx = SecurityContext(withTrustedRoots: false);
    for (final assetPath in _certAssets) {
      final bytes = (await rootBundle.load(assetPath)).buffer.asUint8List();
      ctx.setTrustedCertificatesBytes(bytes);
    }
    _pinnedContext = ctx;
    debugPrint('[SslPinning] Preloaded ${_certAssets.length} pinned root CAs');
  }

  /// Debug builds relax pinning so developers can use a local proxy
  /// (Charles/Burp) against a debug build. Release/profile builds enforce it.
  static bool get _isPinningActive => !kDebugMode;

  /// Returns a [Dio] instance pre-configured with the pinned [HttpClient].
  /// Use this as the HTTP client for all API calls.
  static Dio createPinnedDio({
    required String baseUrl,
    Duration connectTimeout = const Duration(seconds: 15),
    Duration receiveTimeout = const Duration(seconds: 15),
    Duration sendTimeout = const Duration(seconds: 15),
  }) {
    final dio = Dio(
      BaseOptions(
        baseUrl: baseUrl,
        connectTimeout: connectTimeout,
        receiveTimeout: receiveTimeout,
        sendTimeout: sendTimeout,
        contentType: 'application/json',
      ),
    );

    if (_isPinningActive) {
      (dio.httpClientAdapter as IOHttpClientAdapter).createHttpClient = () {
        return _pinnedHttpClient();
      };
      debugPrint('[SslPinning] Dio: SSL pinning ACTIVE');
    } else {
      debugPrint('[SslPinning] Dio: SSL pinning RELAXED (debug)');
    }

    return dio;
  }

  /// Returns an [HttpClient] configured for use with [IOWebSocketChannel].
  /// In release mode the client's trust store is restricted to the pinned
  /// root CAs, so any connection that doesn't chain to one of them fails
  /// the handshake outright.
  static HttpClient createPinnedWebSocketHttpClient() {
    if (!_isPinningActive) {
      debugPrint('[SslPinning] WebSocket: pinning RELAXED (debug)');
      return HttpClient();
    }
    debugPrint('[SslPinning] WebSocket: pinning ACTIVE');
    return _pinnedHttpClient();
  }

  static HttpClient _pinnedHttpClient() {
    final ctx = _pinnedContext;
    if (ctx == null) {
      // preload() wasn't awaited before this client was needed — fail
      // closed rather than silently falling back to the OS trust store.
      throw StateError(
        'SslPinningService.preload() must complete before creating a '
        'pinned HttpClient in release/profile builds.',
      );
    }
    final client = HttpClient(context: ctx);
    // Belt-and-braces: the restricted trust store above is what actually
    // enforces pinning. This callback is just a safety net and should
    // never legitimately be reached.
    client.badCertificateCallback = (cert, host, port) {
      debugPrint('[SslPinning] REJECTED cert for $host:$port (not a pinned root)');
      return false;
    };
    return client;
  }
}
