// LOCAL end-to-end test of the four encrypted operations: the app's real gateway client and the real G4 sealer talk over HTTP to the
// locally built G6b backend (isolated Docker lab) and the Firebase Auth EMULATOR. Run only by
//   python -B -I scripts/enc_auth_poc/local_flutter/run_local_e2e.py
// which starts the lab and passes the ENC_E2E_* and ENC_AUTH_* values; without them every test here is skipped.
//
// What is NOT covered: the Firebase client SDK itself (signInWithCustomToken is replaced by the emulator's REST equivalent), and
// anything about REAL Firebase (password change, revocation, disabled users, recent-login rules, throttling) - the emulator is not Firebase.
import 'dart:convert';
import 'dart:io';

import 'package:epicverse/core/network/api_client.dart';
import 'package:epicverse/core/network/api_config.dart';
import 'package:epicverse/core/security/enc_auth/enc_auth_client.dart';
import 'package:epicverse/core/security/enc_auth/enc_auth_config.dart';
import 'package:epicverse/core/security/enc_auth/enc_envelope_sealer.dart';
import 'package:epicverse/core/security/enc_auth/encrypted_password_auth.dart';
import 'package:epicverse/core/security/enc_auth/g4_envelope_sealer.dart';
import 'package:epicverse/core/security/enc_auth/password_auth.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_test/flutter_test.dart';

import 'enc_auth_test_support.dart' show FakeCredential, leaks;

const String _api = String.fromEnvironment('ENC_E2E_API'); // e.g. http://127.0.0.1:18110/api/v1
const String _lab = String.fromEnvironment('ENC_E2E_LAB'); // e.g. http://127.0.0.1:18110 (test-only helper routes)
const String _emulator = String.fromEnvironment('ENC_E2E_EMULATOR'); // e.g. http://127.0.0.1:9099

final bool _configured = _api.isNotEmpty && _lab.isNotEmpty && _emulator.isNotEmpty;

/// Set by run_local_e2e.py for the second kind of run: the build is `required` but its key configuration is invalid, so the built-in sealer must
/// not exist and every credential operation must fail closed without a single request reaching the lab.
const bool _expectFailClosed = bool.fromEnvironment('ENC_E2E_EXPECT_FAIL_CLOSED');

Future<(int, Map<String, dynamic>)> _json(String method, String url, [Object? body, Map<String, String>? headers]) async {
  final client = HttpClient();
  try {
    final request = await client.openUrl(method, Uri.parse(url));
    request.headers.set('Content-Type', 'application/json');
    headers?.forEach(request.headers.set);
    if (body != null) request.write(jsonEncode(body));
    final response = await request.close();
    final text = await response.transform(utf8.decoder).join();
    return (response.statusCode, text.isEmpty ? <String, dynamic>{} : jsonDecode(text) as Map<String, dynamic>);
  } finally {
    client.close(force: true);
  }
}

Future<(int, Map<String, dynamic>)> _emulatorPost(String path, Object body) =>
    _json('POST', '$_emulator/identitytoolkit.googleapis.com/v1/$path?key=fake-api-key', body);

Map<String, dynamic> _claims(String jwt) =>
    jsonDecode(utf8.decode(base64Url.decode(base64Url.normalize(jwt.split('.')[1])))) as Map<String, dynamic>;

/// The Firebase session, played by the emulator: exchanges the custom token over REST and remembers who is signed in.
class _EmulatorSession implements FirebaseSessionPort {
  String? email;
  String? uid;
  String? idToken;
  final List<String> uids = [];

  @override
  String? get currentEmail => email;

  @override
  Future<UserCredential> signInWithCustomToken(String token) async {
    final (status, body) = await _emulatorPost('accounts:signInWithCustomToken', {'token': token, 'returnSecureToken': true});
    if (status != 200) throw StateError('the emulator refused the custom token');
    idToken = body['idToken'] as String;
    uid = _claims(idToken!)['user_id'] as String;
    uids.add(uid!);
    return FakeCredential();
  }

  @override
  Future<void> refreshIdToken() async {}

  @override
  Future<void> signOut() async {
    uid = idToken = null;
  }
}

class _RecordingTransport implements EncAuthTransport {
  _RecordingTransport(this._inner);

  final EncAuthTransport _inner;
  final List<String> wire = [];

  @override
  Future<EncAuthHttpResponse> post(String path, {required Object body, Map<String, String>? headers}) {
    wire.add(path);
    wire.add(body is String ? body : jsonEncode(body));
    headers?.forEach((k, v) => wire.add('$k: $v'));
    return _inner.post(path, body: body, headers: headers);
  }
}

Future<FirebaseAuthException> _failure(Future<Object?> Function() run) async {
  try {
    await run();
  } on FirebaseAuthException catch (e) {
    return e;
  }
  fail('expected a FirebaseAuthException');
}

void main() {
  final skip = _configured && !_expectFailClosed ? null : 'set by scripts/enc_auth_poc/local_flutter/run_local_e2e.py (valid-key run)';
  late _EmulatorSession session;
  late _RecordingTransport transport;
  late EncryptedPasswordAuth auth;

  final stamp = DateTime.now().millisecondsSinceEpoch;
  final email = 'e2e-$stamp@example.invalid';
  final password = 'E2e-First-Pw!$stamp';
  final newPassword = 'E2e-Second-Pw!$stamp';

  setUpAll(() {
    if (!_configured || _expectFailClosed) return;
    // The PRODUCTION sealer: the one EncAuthRuntime creates by itself in an ENC_AUTH_MODE=required build from ENC_AUTH_KID / ENC_AUTH_PUBLIC_KEY_B64.
    expect(EncAuthConfig.isRequired, isTrue, reason: 'run with --dart-define=ENC_AUTH_MODE=required');
    expect(EncAuthRuntime.sealer, isA<G4EnvelopeSealer>());
    session = _EmulatorSession();
    ApiConfig.identityForTest = () async => session.idToken == null ? null : (uid: session.uid!, token: session.idToken!);
    transport = _RecordingTransport(ApiClientEncAuthTransport(ApiClient(baseUrl: _api)));
    auth = EncryptedPasswordAuth(client: EncAuthClient(transport: transport, sealer: EncAuthRuntime.sealer!), session: session);
  });

  tearDownAll(() => ApiConfig.identityForTest = null);

  group('invalid key configuration in a required build', () {
    final failClosedSkip = _configured && _expectFailClosed ? null : 'set by run_local_e2e.py (invalid-key run)';

    test('there is no sealer; every operation fails closed with enc-auth-unavailable; nothing is created; no plaintext path is tried', () async {
      expect(EncAuthConfig.isRequired, isTrue);
      expect(EncAuthRuntime.sealer, isNull);
      final address = 'closed-$stamp@example.invalid';
      final attempts = <Future<Object?> Function()>[
        () => PasswordAuth.signInWithEmailAndPassword(email: address, password: 'Closed-Pw!$stamp'),
        () => PasswordAuth.signUpOrRecover(email: address, password: 'Closed-Pw!$stamp', emailProof: 'not-a-real-proof'),
        () => PasswordAuth.reauthenticate('Closed-Pw!$stamp'),
        () => PasswordAuth.changePassword(current: 'Closed-Pw!$stamp', next: 'Closed-Pw!x$stamp'),
      ];
      for (final run in attempts) {
        final error = await _failure(run);
        expect(error.code, 'enc-auth-unavailable');
      }
      final (status, body) = await _emulatorPost('accounts:signInWithPassword', {'email': address, 'password': 'Closed-Pw!$stamp', 'returnSecureToken': true});
      expect(status, 400, reason: 'the account must not exist: no signup reached the gateway or the emulator');
      expect(jsonEncode(body), isNot(contains('idToken')));
    }, skip: failClosedSkip);
  });

  Future<String> proofFor(String address) async {
    final (status, body) = await _json('POST', '$_lab/__g6b/issue_proof', {'email': address});
    expect(status, 200);
    expect(body['result'], 'success');
    return body['proof'] as String;
  }

  Future<(int, Map<String, dynamic>)> emulatorLogin(String address, String secret) =>
      _emulatorPost('accounts:signInWithPassword', {'email': address, 'password': secret, 'returnSecureToken': true});

  test('signup creates the account with exactly this password (checked against the emulator) and signs in as it', skip: skip, () async {
    session.email = email;
    final proof = await proofFor(email);
    expect(await auth.signUpOrRecover(email: email, password: password, emailProof: proof), SignUpOutcome.created);
    final (status, body) = await emulatorLogin(email, password);
    expect(status, 200, reason: 'the account must exist with the signup password');
    expect(session.uid, body['localId'], reason: 'the session belongs to the account that was created');
  });

  test('a second signup with the same password recovers the same account; a different password is refused', skip: skip, () async {
    final proof = await proofFor(email);
    final before = session.uid;
    expect(await auth.signUpOrRecover(email: email, password: password, emailProof: proof), SignUpOutcome.recovered);
    expect(session.uid, before);
    expect(await auth.signUpOrRecover(email: email, password: 'Another-Pw!$stamp', emailProof: proof), SignUpOutcome.alreadyRegistered);
    expect(session.uid, before, reason: 'a refused signup must not change the session');
  });

  test('signup without a valid e-mail proof is refused and creates nothing', skip: skip, () async {
    final other = 'noproof-$stamp@example.invalid';
    final error = await _failure(() => auth.signUpOrRecover(email: other, password: password, emailProof: 'evp1_${'x' * 43}'));
    expect(error.code, 'email-proof-invalid');
    final (status, _) = await emulatorLogin(other, password);
    expect(status, 400, reason: 'no account may exist');
  });

  test('login: the right password signs in as the same uid; a wrong password and an unknown e-mail are the same refusal', skip: skip, () async {
    final uid = session.uid;
    await session.signOut();
    await auth.signIn(email: email, password: password);
    expect(session.uid, uid);
    final wrong = await _failure(() => auth.signIn(email: email, password: 'Wrong-Pw!$stamp'));
    final unknown = await _failure(() => auth.signIn(email: 'nobody-$stamp@example.invalid', password: 'Wrong-Pw!$stamp'));
    expect(wrong.code, 'invalid-credential');
    expect(unknown.code, 'invalid-credential');
    expect(session.uid, uid, reason: 'a refused login must not change the session');
  });

  test('re-authentication: the right password yields a fresh session for the same uid, a wrong one is refused', skip: skip, () async {
    session.email = email;
    final uid = session.uid;
    await auth.reauthenticate(password);
    expect(session.uid, uid);
    final error = await _failure(() => auth.reauthenticate('Wrong-Pw!$stamp'));
    expect(error.code, 'invalid-credential');
  });

  test('password change: old password stops working, new one works, uid unchanged; an equal or wrong current password is refused', skip: skip, () async {
    session.email = email;
    final uid = session.uid;
    expect((await _failure(() => auth.changePassword(current: password, next: password))).code, 'password-unchanged');
    expect((await _failure(() => auth.changePassword(current: 'Wrong-Pw!$stamp', next: newPassword))).code, 'invalid-credential');
    expect((await emulatorLogin(email, password)).$1, 200, reason: 'nothing may have changed yet');

    await auth.changePassword(current: password, next: newPassword);
    expect((await emulatorLogin(email, password)).$1, 400, reason: 'the old password must be dead');
    final (status, body) = await emulatorLogin(email, newPassword);
    expect(status, 200);
    expect(body['localId'], uid);
    expect(session.uid, uid, reason: 'the session after the change is the same account');
  });

  test('nothing credential-like ever crossed the wire in clear, base64, base64url, URL-encoded or hex form', skip: skip, () {
    for (final secret in [email, password, newPassword, 'Wrong-Pw!$stamp', 'E2e-First-Pw', 'E2e-Second-Pw']) {
      expect(leaks(transport.wire, secret), isFalse, reason: 'a secret form appeared in a request to the gateway');
    }
    expect(transport.wire.where((w) => w == '/auth/enc/nonce').length, greaterThanOrEqualTo(10));
  });
}
