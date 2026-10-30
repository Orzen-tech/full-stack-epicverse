/// Standalone, offline proof of concept: seals email/password for the EpicVerse encrypted-credential envelope (version 1).
/// Not used by the app. No network, file, Firebase or plaintext-fallback code lives in this library.
library;

export 'src/encoding.dart' show b64uEncode, buildAad, buildPlaintext;
export 'src/envelope.dart' show sealCredentials;
export 'src/errors.dart' show EnvelopeError;
export 'src/spki_key.dart' show keyIdForSpki, parseRsaPublicKeySpki;
export 'src/validation.dart' show operations;
