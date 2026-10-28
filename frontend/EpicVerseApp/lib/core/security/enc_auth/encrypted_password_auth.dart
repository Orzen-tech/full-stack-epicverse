import 'package:firebase_auth/firebase_auth.dart';
import '../../network/api_client.dart';
import 'enc_auth_client.dart';
import 'enc_envelope_sealer.dart';
import 'password_auth.dart';

/// The few Firebase session calls the encrypted path needs, behind a seam so they can be tested without Firebase.
abstract class FirebaseSessionPort {
  /// E-mail of the signed-in user, if any.
  String? get currentEmail;

  Future<UserCredential> signInWithCustomToken(String token);

  /// getIdToken(true): makes the new sign-in time reach the backend.
  Future<void> refreshIdToken();

  Future<void> signOut();
}

class FirebaseAuthSessionPort implements FirebaseSessionPort {
  const FirebaseAuthSessionPort();

  @override
  String? get currentEmail => FirebaseAuth.instance.currentUser?.email;

  @override
  Future<UserCredential> signInWithCustomToken(String token) => FirebaseAuth.instance.signInWithCustomToken(token);

  @override
  Future<void> refreshIdToken() async {
    await FirebaseAuth.instance.currentUser?.getIdToken(true);
  }

  @override
  Future<void> signOut() => FirebaseAuth.instance.signOut();
}

/// Encrypted login / signup / re-authentication / password change. Credentials leave the app only inside the sealed envelope; the
/// gateway answers with a Firebase custom token for the account it verified, and the app signs in with that token.
/// There is no plaintext fallback anywhere in this class: any problem is a [FirebaseAuthException] and nothing else is tried.
class EncryptedPasswordAuth implements PasswordAuthBackend {
  EncryptedPasswordAuth({required EncAuthClient client, required FirebaseSessionPort session})
      : _client = client,
        _session = session;

  /// The real wiring: the pinned apiClient and the registered sealer. With no sealer registered every operation fails closed.
  factory EncryptedPasswordAuth.production() {
    final sealer = EncAuthRuntime.sealer;
    if (sealer == null) return _Unavailable();
    return EncryptedPasswordAuth(
      client: EncAuthClient(transport: ApiClientEncAuthTransport(apiClient), sealer: sealer),
      session: const FirebaseAuthSessionPort(),
    );
  }

  final EncAuthClient _client;
  final FirebaseSessionPort _session;

  String _customToken(Map<String, dynamic> body) {
    final token = body['custom_token'];
    if (token is! String || token.isEmpty) throw mapGatewayFailure(502, null);
    return token;
  }

  @override
  Future<UserCredential> signIn({required String email, required String password}) async {
    final body = await _client.call('login', {'email': email, 'password': password});
    return _session.signInWithCustomToken(_customToken(body));
  }

  @override
  Future<SignUpOutcome> signUpOrRecover({required String email, required String password, String? emailProof}) async {
    final Map<String, dynamic> body;
    try {
      body = await _client.call('signup', {'email': email, 'password': password}, emailProof: emailProof);
    } on FirebaseAuthException catch (e) {
      if (e.code == 'email-already-in-use') return SignUpOutcome.alreadyRegistered;
      rethrow;
    }
    await _session.signInWithCustomToken(_customToken(body));
    return body['recovered'] == true ? SignUpOutcome.recovered : SignUpOutcome.created;
  }

  @override
  Future<void> reauthenticate(String password) async {
    final email = _session.currentEmail;
    if (email == null || email.isEmpty) throw StateError('No signed-in user with an email address');
    final body = await _client.call('reauth', {'email': email, 'password': password});
    await _session.signInWithCustomToken(_customToken(body));
    await _session.refreshIdToken();
  }

  @override
  Future<void> changePassword({required String current, required String next}) async {
    final email = _session.currentEmail;
    if (email == null || email.isEmpty) throw FirebaseAuthException(code: 'requires-recent-login');
    final body = await _client.call('change_password', {'email': email, 'password': current, 'new_password': next});
    final token = body['custom_token'];
    if (token is String && token.isNotEmpty) {
      await _session.signInWithCustomToken(token); // the old sessions were revoked; this is the fresh one
    } else {
      await _session.signOut(); // the password changed but no session could be issued: the user signs in again
    }
  }
}

/// Used when no sealer is registered: every operation fails closed with a fixed error. Nothing is sent anywhere.
class _Unavailable extends EncryptedPasswordAuth {
  _Unavailable() : super(client: EncAuthClient(transport: _NoTransport(), sealer: _NoSealer()), session: const FirebaseAuthSessionPort());

  @override
  Future<UserCredential> signIn({required String email, required String password}) => Future.error(encAuthUnavailable());

  @override
  Future<SignUpOutcome> signUpOrRecover({required String email, required String password, String? emailProof}) =>
      Future.error(encAuthUnavailable());

  @override
  Future<void> reauthenticate(String password) => Future.error(encAuthUnavailable());

  @override
  Future<void> changePassword({required String current, required String next}) => Future.error(encAuthUnavailable());
}

class _NoTransport implements EncAuthTransport {
  @override
  Future<EncAuthHttpResponse> post(String path, {required Object body, Map<String, String>? headers}) =>
      Future.error(const EncAuthTransportFailure());
}

class _NoSealer implements EnvelopeSealer {
  @override
  String seal({required String operation, required String nonce, required int nowSeconds, required Map<String, String> fields}) =>
      throw const EnvelopeSealFailure('NO_SEALER');
}
