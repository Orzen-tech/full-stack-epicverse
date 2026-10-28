// Shared helpers for the encrypted-auth tests: a scripted gateway, a recording session port, and wire-leak checks.
import 'dart:convert';

import 'package:epicverse/core/security/enc_auth/enc_auth_client.dart';
import 'package:epicverse/core/security/enc_auth/encrypted_password_auth.dart';
import 'package:epicverse/core/security/enc_auth/g4_envelope_sealer.dart';
import 'package:epicverse/core/security/enc_auth/sealer/sealer.dart' as g4;
import 'package:firebase_auth/firebase_auth.dart';

// A PUBLIC RSA-3072 key from the G4 test vectors (no private half exists in this repository). The key id is derived from it.
const String testSpki =
    'MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAqMNfZRLNg8iJkgmgi6JvF7xHH6VJMt/J9q/gHvTqmeIX7OqLKSnTh+OWc6O+HJlTewqRuymyKP7U42uLalAQuRpTB2Ye/bkkRSnjXi3a9tD+m/ZT5UVcrRTfw1AQDsiYuWrWRk1tKenIgiYAqcEhKrsWOPd7uourxg1IogvIs8d8quwpT62x+fMNGJzB0loQ17rliE66toogezcKOom7wg8xYJV1PxuybXWfcaLxnkPqDPBK/AaUDGx1GStMxV1Ay9mxlA4P6Tn3BL13pE67KfsHK1d6usBGrlHNDPPExkyVk/ywMPPUxVpi1OTPjLoyeq238oE8H/Muz0HK8sGPp0rPbrO4gu6w7N7RCvqqUfnQjaz6+shsy9M4WIAczvMHg8T1lmM7gxvVQhxrRBQ2qvsGa3ntAgX+7zDZta0k7X13WAMeE2UDchji3/Yg9dSf799hO+CU8xiHADqFVZSVEYJZ4kE4K2pACmFb5T69BQlIhT7jKpCdhC5v0kVnYtp1AgMBAAE=';

/// A real G4 sealer for the public test key (the same built-in sealer a required-mode build uses).
G4EnvelopeSealer testSealer() => G4EnvelopeSealer(keyId: testKeyId, spkiBase64: testSpki);

final String testKeyId = g4.keyIdForSpki(base64.decode(testSpki));

class RecordedPost {
  RecordedPost(this.path, this.body, this.headers);

  final String path;
  final Object body;
  final Map<String, String> headers;

  String get bodyText => body is String ? body as String : jsonEncode(body);
}

/// A scripted gateway. [nonceStatus]/[nonceBody] answer /auth/enc/nonce; [opStatus]/[opBody] answer the operation.
class FakeGateway implements EncAuthTransport {
  FakeGateway({
    this.nonceStatus = 200,
    Object? nonceBody = _unset,
    this.opStatus = 200,
    Object? opBody = _unset,
    this.failTransport = false,
  })  : nonceBody = identical(nonceBody, _unset) ? {'nonce': 'A' * 43, 'ttl': 120, 'server_time': 1800000000} : nonceBody as Map<String, dynamic>?,
        opBody = identical(opBody, _unset) ? {'custom_token': 'synthetic-custom-token', 'created': true} : opBody as Map<String, dynamic>?;

  static const Object _unset = Object();

  int nonceStatus;
  Map<String, dynamic>? nonceBody;
  int opStatus;
  Map<String, dynamic>? opBody;
  bool failTransport;
  final List<RecordedPost> posts = [];

  @override
  Future<EncAuthHttpResponse> post(String path, {required Object body, Map<String, String>? headers}) async {
    posts.add(RecordedPost(path, body, headers ?? const {}));
    if (failTransport) throw const EncAuthTransportFailure();
    if (path == '/auth/enc/nonce') return EncAuthHttpResponse(nonceStatus, nonceBody);
    return EncAuthHttpResponse(opStatus, opBody);
  }
}

class FakeCredential implements UserCredential {
  @override
  dynamic noSuchMethod(Invocation invocation) => null;
}

class RecordingSession implements FirebaseSessionPort {
  RecordingSession({this.email = 'user@example.invalid'});

  String? email;
  final List<String> calls = [];
  final List<String> tokens = [];

  @override
  String? get currentEmail => email;

  @override
  Future<UserCredential> signInWithCustomToken(String token) async {
    calls.add('signInWithCustomToken');
    tokens.add(token);
    return FakeCredential();
  }

  @override
  Future<void> refreshIdToken() async => calls.add('refreshIdToken');

  @override
  Future<void> signOut() async => calls.add('signOut');
}

/// Every textual form a secret could take on the wire: clear, URL-encoded, base64, base64url (with and without padding), hex.
List<String> forms(String secret) {
  final bytes = utf8.encode(secret);
  final b64 = base64.encode(bytes);
  final b64u = base64Url.encode(bytes);
  return {
    secret,
    Uri.encodeComponent(secret),
    Uri.encodeQueryComponent(secret),
    b64,
    b64.replaceAll('=', ''),
    b64u,
    b64u.replaceAll('=', ''),
    bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join(),
  }.toList();
}

bool leaks(Iterable<String> wire, String secret) => wire.any((text) => forms(secret).any(text.contains));
