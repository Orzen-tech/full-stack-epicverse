import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart' show FirebaseAuthException;
import '../../errors/app_exception.dart';
import '../../network/api_client.dart';
import 'enc_envelope_sealer.dart';

/// Minimal HTTP answer used by the gateway client.
class EncAuthHttpResponse {
  const EncAuthHttpResponse(this.status, this.json);

  final int status;
  final Map<String, dynamic>? json;
}

/// The request never reached the gateway (no connection, timeout). No detail is kept.
class EncAuthTransportFailure implements Exception {
  const EncAuthTransportFailure();

  @override
  String toString() => 'EncAuthTransportFailure';
}

abstract class EncAuthTransport {
  /// POST. [body] is a Map (sent as JSON) or an already-built JSON string (sent byte for byte). Never retries.
  Future<EncAuthHttpResponse> post(String path, {required Object body, Map<String, String>? headers});
}

/// The production transport: the app's existing pinned, App-Check-aware [ApiClient]. HTTP error statuses are returned, not thrown,
/// so the gateway's fixed error codes can be read; only connection-level failures throw.
class ApiClientEncAuthTransport implements EncAuthTransport {
  ApiClientEncAuthTransport(this._client);

  final ApiClient _client;

  @override
  Future<EncAuthHttpResponse> post(String path, {required Object body, Map<String, String>? headers}) async {
    try {
      final response = await _client.post<dynamic>(
        path,
        data: body,
        options: Options(
          contentType: Headers.jsonContentType,
          headers: headers,
          validateStatus: (_) => true,
          responseType: ResponseType.json,
        ),
      );
      final data = response.data;
      return EncAuthHttpResponse(response.statusCode ?? 0, data is Map ? Map<String, dynamic>.from(data) : null);
    } on AppException {
      throw const EncAuthTransportFailure();
    } on DioException {
      throw const EncAuthTransportFailure();
    }
  }
}

/// Maps a gateway answer to a [FirebaseAuthException] with the code the existing screens already handle. Only fixed text is used.
FirebaseAuthException mapGatewayFailure(int status, Map<String, dynamic>? json) {
  final error = json?['error'];
  final name = error is String ? error : '';
  final String code;
  switch (name) {
    case 'invalid-credential':
    case 'user-disabled':
    case 'email-already-in-use':
    case 'weak-password':
    case 'password-unchanged':
    case 'email-proof-invalid':
    case 'too-many-requests':
      code = name;
    case 'AUTH_UNAVAILABLE':
      code = json?['password_changed'] == 'unknown' ? 'password-change-unknown' : 'network-request-failed';
    case 'AUTH_FAILED':
      code = status == 401 ? 'user-token-expired' : 'enc-auth-failed';
    default:
      code = status == 429
          ? 'too-many-requests'
          : (status >= 500 ? 'network-request-failed' : 'enc-auth-failed');
  }
  return FirebaseAuthException(code: code, message: _messages[code] ?? _messages['enc-auth-failed']);
}

const Map<String, String> _messages = {
  'invalid-credential': 'Incorrect email or password.',
  'user-disabled': 'This account has been disabled.',
  'email-already-in-use': 'This email is already registered.',
  'weak-password': 'The password does not meet the password policy.',
  'password-unchanged': 'New password must be different from your current password.',
  'email-proof-invalid': 'Your email verification has expired. Please verify your email again.',
  'too-many-requests': 'Too many attempts. Please wait a few minutes and try again.',
  'network-request-failed': 'Unable to connect. Please check your connection and try again.',
  'user-token-expired': 'Please sign in again and retry.',
  'password-change-unknown': 'We could not confirm the password change. Try signing in with your new password, or try again.',
  'enc-auth-unavailable': 'Secure sign-in is not available in this build.',
  'enc-auth-failed': 'Secure sign-in failed. Please update the app and try again.',
};

FirebaseAuthException encAuthUnavailable() =>
    FirebaseAuthException(code: 'enc-auth-unavailable', message: _messages['enc-auth-unavailable']);

/// Client for the encrypted-credential gateway (`/auth/enc/*`): asks for a single-use nonce, seals the credentials with the
/// registered [EnvelopeSealer], sends the envelope. It never sends credentials in any other form, never retries, and logs nothing.
class EncAuthClient {
  EncAuthClient({required EncAuthTransport transport, required EnvelopeSealer sealer})
      : _transport = transport,
        _sealer = sealer;

  final EncAuthTransport _transport;
  final EnvelopeSealer _sealer;

  /// Returns the gateway's success body. Throws [FirebaseAuthException] for every failure.
  Future<Map<String, dynamic>> call(String operation, Map<String, String> fields, {String? emailProof}) async {
    final nonceAnswer = await _send('/auth/enc/nonce', {'op': operation});
    final nonce = nonceAnswer.json?['nonce'];
    final serverTime = nonceAnswer.json?['server_time'];
    if (nonceAnswer.status != 200) throw mapGatewayFailure(nonceAnswer.status, nonceAnswer.json);
    if (nonce is! String || serverTime is! int) throw mapGatewayFailure(502, null);

    final String envelope;
    try {
      envelope = _sealer.seal(operation: operation, nonce: nonce, nowSeconds: serverTime, fields: fields);
    } on EnvelopeSealFailure {
      throw FirebaseAuthException(code: 'enc-auth-failed', message: _messages['enc-auth-failed']);
    }

    final answer = await _send('/auth/enc/$operation', envelope,
        headers: {if (emailProof != null && emailProof.isNotEmpty) 'X-Email-Proof': emailProof});
    final json = answer.json;
    if (answer.status != 200 || json == null) throw mapGatewayFailure(answer.status, json);
    return json;
  }

  Future<EncAuthHttpResponse> _send(String path, Object body, {Map<String, String>? headers}) async {
    try {
      return await _transport.post(path, body: body, headers: headers);
    } on EncAuthTransportFailure {
      throw FirebaseAuthException(code: 'network-request-failed', message: _messages['network-request-failed']);
    }
  }
}
