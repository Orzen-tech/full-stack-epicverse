/// The EpicVerse G1 encrypted-credential envelope (version 1) sealer: RSA-OAEP-SHA256 key wrap + AES-256-GCM, pure Dart on pointycastle 4.0.0.
/// The five implementation files in this folder are byte-identical copies of the tested G4 proof-of-concept sealer (a test pins their SHA-256).
/// No network, file, Firebase or plaintext-fallback code lives here.
library;

export 'encoding.dart' show b64uEncode, buildAad, buildPlaintext;
export 'envelope.dart' show sealCredentials;
export 'errors.dart' show EnvelopeError;
export 'spki_key.dart' show keyIdForSpki, parseRsaPublicKeySpki;
export 'validation.dart' show operations;
