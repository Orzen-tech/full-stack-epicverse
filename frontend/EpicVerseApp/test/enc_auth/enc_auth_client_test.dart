// The encrypted-credential gateway client: nonce -> seal -> send, error mapping, and "no plaintext on the wire".
// Uses the REAL G4 sealer with a public test key; the gateway is a script. No network, no Firebase.
import 'dart:convert';

import 'package:epicverse/core/security/enc_auth/enc_auth_client.dart';
import 'package:epicverse/core/security/enc_auth/enc_envelope_sealer.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter_test/flutter_test.dart';

import 'enc_auth_test_support.dart';

const String email = 'person.under.test@example.invalid';
const String password = 'Correct-Horse-9!Battery';
const String newPassword = 'N3w-Staple-Horse!Battery';

Future<FirebaseAuthException> failure(Future<Object?> Function() run) async {
  try {
    await run();
  } on FirebaseAuthException catch (e) {
    return e;
  }
  fail('expected a FirebaseAuthException');
}

void main() {
  group('request sequence and envelope', () {
    for (final operation in ['login', 'signup', 'reauth', 'change_password']) {
      test('$operation: nonce first, then ONE sealed envelope with the version 1 shape', () async {
        final gateway = FakeGateway();
        final client = EncAuthClient(transport: gateway, sealer: testSealer());
        final fields = {
          'email': email,
          'password': password,
          if (operation == 'change_password') 'new_password': newPassword,
        };
        final body = await client.call(operation, fields);
        expect(body['custom_token'], 'synthetic-custom-token');

        expect(gateway.posts.map((p) => p.path), ['/auth/enc/nonce', '/auth/enc/$operation']);
        expect(gateway.posts.first.body, {'op': operation});
        final envelope = jsonDecode(gateway.posts.last.body as String) as Map<String, dynamic>;
        expect(envelope.keys.toSet(), {'v', 'kid', 'op', 'nonce', 'ts', 'ek', 'iv', 'ct'});
        expect(envelope['v'], 1);
        expect(envelope['kid'], testKeyId);
        expect(envelope['op'], operation);
        expect(envelope['nonce'], 'A' * 43);
        expect(envelope['ts'], 1800000000, reason: 'the server time from the nonce answer is used, not the device clock');
        expect(gateway.posts.last.body, isA<String>(), reason: 'the envelope is sent byte for byte, not re-encoded');
      });
    }

    test('signup carries the e-mail proof ONLY in the X-Email-Proof header', () async {
      final gateway = FakeGateway();
      const proof = 'evp1_synthetic-proof-token-0123456789abcdefghijklmnopq';
      await EncAuthClient(transport: gateway, sealer: testSealer()).call('signup', {'email': email, 'password': password}, emailProof: proof);
      expect(gateway.posts.last.headers['X-Email-Proof'], proof);
      expect(gateway.posts.first.headers.containsKey('X-Email-Proof'), isFalse);
      expect(gateway.posts.map((p) => p.bodyText).any((text) => text.contains(proof)), isFalse);
      await EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': password});
      expect(gateway.posts.last.headers.containsKey('X-Email-Proof'), isFalse);
    });

    test('no e-mail address or password appears on the wire in ANY form (clear, URL, base64, base64url, hex)', () async {
      final gateway = FakeGateway();
      final client = EncAuthClient(transport: gateway, sealer: testSealer());
      await client.call('change_password', {'email': email, 'password': password, 'new_password': newPassword});
      await client.call('login', {'email': email, 'password': password});
      final wire = [
        for (final p in gateway.posts) ...[p.path, p.bodyText, ...p.headers.keys, ...p.headers.values],
      ];
      for (final secret in [email, password, newPassword, 'person.under.test', 'Correct-Horse', 'Staple-Horse']) {
        expect(leaks(wire, secret), isFalse, reason: 'a secret form was found on the wire');
      }
    });

    test('the same credentials never produce the same envelope twice (fresh key, IV and nonce binding)', () async {
      final gateway = FakeGateway();
      final client = EncAuthClient(transport: gateway, sealer: testSealer());
      await client.call('login', {'email': email, 'password': password});
      await client.call('login', {'email': email, 'password': password});
      final bodies = gateway.posts.where((p) => p.path == '/auth/enc/login').map((p) => p.body as String).toList();
      expect(bodies.toSet().length, 2);
    });
  });

  group('failure mapping (fixed codes, fixed text, nothing taken from the answer)', () {
    final cases = <String, (int, Map<String, dynamic>, String)>{
      'wrong password': (400, {'error': 'invalid-credential'}, 'invalid-credential'),
      'disabled': (400, {'error': 'user-disabled'}, 'user-disabled'),
      'duplicate': (400, {'error': 'email-already-in-use'}, 'email-already-in-use'),
      'weak': (400, {'error': 'weak-password', 'password_changed': false}, 'weak-password'),
      'unchanged': (400, {'error': 'password-unchanged'}, 'password-unchanged'),
      'proof': (403, {'error': 'email-proof-invalid'}, 'email-proof-invalid'),
      'limited': (429, {'error': 'too-many-requests'}, 'too-many-requests'),
      'unavailable': (503, {'error': 'AUTH_UNAVAILABLE'}, 'network-request-failed'),
      'unknown outcome': (503, {'error': 'AUTH_UNAVAILABLE', 'password_changed': 'unknown'}, 'password-change-unknown'),
      'envelope refused': (400, {'error': 'AUTH_FAILED'}, 'enc-auth-failed'),
      'bad bearer': (401, {'error': 'AUTH_FAILED'}, 'user-token-expired'),
      'unlabelled 429': (429, {'detail': 'x'}, 'too-many-requests'),
      'unlabelled 500': (500, {'detail': 'x'}, 'network-request-failed'),
      'unexpected 418': (418, {'x': 1}, 'enc-auth-failed'),
    };
    cases.forEach((name, spec) {
      test(name, () async {
        final gateway = FakeGateway(opStatus: spec.$1, opBody: spec.$2);
        final error = await failure(() => EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': password}));
        expect(error.code, spec.$3);
        expect(error.message, isNotEmpty);
        expect('${error.code} ${error.message}', isNot(contains(email)));
        expect('${error.code} ${error.message}', isNot(contains(password)));
      });
    });

    test('a failing nonce request stops everything: the envelope is never built or sent', () async {
      for (final spec in [(503, {'error': 'AUTH_UNAVAILABLE'}), (429, {'error': 'too-many-requests'}), (400, {'error': 'AUTH_FAILED'})]) {
        final gateway = FakeGateway(nonceStatus: spec.$1, nonceBody: spec.$2);
        await failure(() => EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': password}));
        expect(gateway.posts.map((p) => p.path), ['/auth/enc/nonce']);
      }
    });

    test('a malformed nonce answer is refused', () async {
      for (final body in <Map<String, dynamic>?>[null, {}, {'nonce': 5, 'server_time': 1}, {'nonce': 'x' * 43, 'server_time': 'now'}]) {
        final gateway = FakeGateway(nonceBody: body);
        final error = await failure(() => EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': password}));
        expect(error.code, anyOf('network-request-failed', 'enc-auth-failed'));
        expect(gateway.posts, hasLength(1));
      }
    });

    test('no connection: reported as a network error, exactly one attempt, no retry', () async {
      final gateway = FakeGateway(failTransport: true);
      final error = await failure(() => EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': password}));
      expect(error.code, 'network-request-failed');
      expect(gateway.posts, hasLength(1));
    });

    test('a sealer failure sends NOTHING after the nonce - there is no plaintext fallback', () async {
      final gateway = FakeGateway();
      final error = await failure(() => EncAuthClient(transport: gateway, sealer: _FailingSealer()).call('login', {'email': email, 'password': password}));
      expect(error.code, 'enc-auth-failed');
      expect(gateway.posts.map((p) => p.path), ['/auth/enc/nonce']);
      expect(leaks(gateway.posts.map((p) => p.bodyText), password), isFalse);
    });

    test('invalid input is refused by the sealer before anything leaves the device', () async {
      final gateway = FakeGateway();
      final error = await failure(() => EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': ''}));
      expect(error.code, 'enc-auth-failed');
      expect(gateway.posts.map((p) => p.path), ['/auth/enc/nonce']);
    });

    test('an operation answer without a body is an error, not a success', () async {
      final gateway = FakeGateway(opStatus: 200, opBody: null);
      final error = await failure(() => EncAuthClient(transport: gateway, sealer: testSealer()).call('login', {'email': email, 'password': password}));
      expect(error.code, isNotEmpty);
    });
  });
}

class _FailingSealer implements EnvelopeSealer {
  @override
  String seal({required String operation, required String nonce, required int nowSeconds, required Map<String, String> fields}) =>
      throw const EnvelopeSealFailure('ENCRYPT_FAILED');
}
