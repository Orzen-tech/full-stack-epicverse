/// Build-time switch for encrypted e-mail/password authentication.
///
/// `--dart-define=ENC_AUTH_MODE=required` makes every credential-bearing request (login, signup, re-authentication,
/// password change) go through the encrypted gateway, and removes the plaintext Firebase password path from that build.
/// The default is `off`: a normal build behaves exactly as before. Any value other than exactly `off` counts as `required`,
/// so a typo can never silently turn encryption off.
///
/// The sealer is built into the app (the vendored sealer in lib/core/security/enc_auth/sealer/, which uses the pinned pointycastle
/// dependency); there is no path dependency. In a `required` build EncAuthRuntime creates it once from the ENC_AUTH_KID and
/// ENC_AUTH_PUBLIC_KEY_B64 defines below. If either is missing, malformed or does not match, there is no sealer and every credential
/// operation fails closed with `enc-auth-unavailable` before any request is sent. This applies to release builds as well, and there is
/// no plaintext fallback. The encrypted flows also need the backend's /auth/enc/* gateway.
class EncAuthConfig {
  EncAuthConfig._();

  static const String _mode = String.fromEnvironment('ENC_AUTH_MODE', defaultValue: 'off');

  /// True only for exactly `off`.
  static const bool isOff = _mode == 'off';

  /// True for every other value.
  static const bool isRequired = !isOff;

  /// Key id and DER SubjectPublicKeyInfo (base64) of the gateway's public key. Public values, not secrets. Local builds pass
  /// them with --dart-define; a production build would embed the real key.
  static const String keyId = String.fromEnvironment('ENC_AUTH_KID');
  static const String publicKeySpkiBase64 = String.fromEnvironment('ENC_AUTH_PUBLIC_KEY_B64');
}
