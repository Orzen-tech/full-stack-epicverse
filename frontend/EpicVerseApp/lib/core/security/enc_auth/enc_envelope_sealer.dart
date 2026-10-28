import 'package:flutter/foundation.dart' show kReleaseMode, visibleForTesting;

import 'enc_auth_config.dart';
import 'g4_envelope_sealer.dart';

/// Raised by a sealer when the envelope cannot be built. Carries a fixed code only, never a value the user typed.
class EnvelopeSealFailure implements Exception {
  const EnvelopeSealFailure(this.code);

  final String code;

  @override
  String toString() => 'EnvelopeSealFailure($code)';
}

/// Builds the G1 version-1 envelope for one operation. The real implementation is [G4EnvelopeSealer] (the vendored G4 Dart sealer),
/// which `EncAuthRuntime` creates by itself in an `ENC_AUTH_MODE=required` build.
abstract class EnvelopeSealer {
  /// [operation] is one of login, signup, reauth, change_password. [nonce] is the fresh server nonce for that operation and
  /// [nowSeconds] the server's time. Throws [EnvelopeSealFailure] on any problem; there is no other output form.
  String seal({
    required String operation,
    required String nonce,
    required int nowSeconds,
    required Map<String, String> fields,
  });
}

/// Provides the sealer. In an `ENC_AUTH_MODE=required` build it is the built-in [G4EnvelopeSealer], created once from the compile-time
/// ENC_AUTH_KID / ENC_AUTH_PUBLIC_KEY_B64 values. If those are missing, malformed or do not match, there is NO sealer and every
/// credential operation fails closed with `enc-auth-unavailable` before any request is made. In an `off` build there is none at all.
class EncAuthRuntime {
  EncAuthRuntime._();

  static EnvelopeSealer? _registered;
  static EnvelopeSealer? _builtIn;
  static bool _builtInTried = false;

  static EnvelopeSealer? get sealer {
    final registered = _registered;
    if (registered != null) return registered;
    if (!EncAuthConfig.isRequired) return null;
    if (!_builtInTried) {
      _builtInTried = true;
      _builtIn = buildSealer(EncAuthConfig.keyId, EncAuthConfig.publicKeySpkiBase64);
    }
    return _builtIn;
  }

  /// The one place that turns a key id and a public key into a sealer: null for any problem (never a partial or fallback sealer).
  @visibleForTesting
  static EnvelopeSealer? buildSealer(String keyId, String spkiBase64) {
    try {
      return G4EnvelopeSealer(keyId: keyId, spkiBase64: spkiBase64);
    } catch (_) {
      return null; // any problem at all (a fixed EnvelopeSealFailure or anything unexpected): no sealer, so every operation fails closed
    }
  }

  /// Tests and the local development entry point only: injects a different sealer (for example a fake). Refused in release builds, so a
  /// release build can only ever use the built-in sealer.
  static void registerSealer(EnvelopeSealer sealer) {
    if (kReleaseMode) {
      throw StateError('A sealer cannot be registered in a release build');
    }
    _registered = sealer;
  }

  @visibleForTesting
  static void clearForTest() {
    _registered = null;
    _builtIn = null;
    _builtInTried = false;
  }
}
