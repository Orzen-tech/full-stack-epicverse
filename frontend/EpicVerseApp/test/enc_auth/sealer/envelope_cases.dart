import 'dart:convert';
import 'dart:typed_data';

import 'package:epicverse/core/security/enc_auth/sealer/sealer.dart';
import 'package:epicverse/core/security/enc_auth/sealer/envelope.dart' show sealBytes;
import 'package:pointycastle/export.dart';

import 'harness.dart';
import 'vectors.dart';

final RSAPublicKey _key = parseRsaPublicKeySpki(base64.decode(rsa3072Spki));
const String _email = 'poc-user@example.invalid';
const String _password = 'G2pw-dummy-Secret-1';

String _seal(String op, Map<String, String> fields, {int ts = vectorTs}) =>
    sealCredentials(publicKey: _key, kid: rsa3072Kid, operation: op, nonce: vectorNonce, fields: fields, nowSeconds: ts);

Map<String, String> _fields(String op) =>
    {'email': _email, 'password': _password, if (op == 'change_password') 'new_password': 'G2pw-dummy-New-2'};

final RegExp _b64u = RegExp(r'^[A-Za-z0-9_-]*$');

void run() {
  for (final op in operations.keys) {
    final raw = _seal(op, _fields(op));
    final env = jsonDecode(raw) as Map<String, dynamic>;
    check('$op: exactly the eight envelope fields, in the documented order',
        () => env.keys.join(',') == 'v,kid,op,nonce,ts,ek,iv,ct');
    check('$op: header values', () => env['v'] == 1 && env['kid'] == rsa3072Kid && env['op'] == op && env['nonce'] == vectorNonce && env['ts'] == vectorTs);
    check('$op: ek, iv, ct are canonical unpadded base64url', () {
      for (final name in ['ek', 'iv', 'ct']) {
        final s = env[name] as String;
        if (!_b64u.hasMatch(s) || s.contains('=') || b64uEncode(base64Url.decode(base64Url.normalize(s))) != s) return false;
      }
      return true;
    });
    check('$op: ek is exactly the modulus length, iv 12 bytes', () =>
        base64Url.decode(base64Url.normalize(env['ek'] as String)).length == 384 &&
        base64Url.decode(base64Url.normalize(env['iv'] as String)).length == 12);
    check('$op: ct length is plaintext length + 16', () =>
        base64Url.decode(base64Url.normalize(env['ct'] as String)).length == buildPlaintext(_fields(op)).length + 16);
    check('$op: the serialized envelope contains no plaintext credential', () {
      for (final secret in [_email, _password, 'G2pw-dummy-New-2']) {
        final forms = [
          secret,
          toHex(utf8.encode(secret)),
          base64.encode(utf8.encode(secret)),
          base64Url.encode(utf8.encode(secret)),
          Uri.encodeComponent(secret),
        ];
        if (forms.any(raw.contains)) return false;
      }
      return true;
    });
  }

  check('two seals of the same input differ in ek, iv and ct (fresh randomness)', () {
    final a = jsonDecode(_seal('login', _fields('login'))) as Map<String, dynamic>;
    final b = jsonDecode(_seal('login', _fields('login'))) as Map<String, dynamic>;
    return a['ek'] != b['ek'] && a['iv'] != b['iv'] && a['ct'] != b['ct'];
  });
  check('50 consecutive seals all produce a full-length ek and distinct ivs', () {
    final ivs = <String>{};
    for (var i = 0; i < 50; i++) {
      final env = jsonDecode(_seal('login', _fields('login'))) as Map<String, dynamic>;
      if (base64Url.decode(base64Url.normalize(env['ek'] as String)).length != 384) return false;
      ivs.add(env['iv'] as String);
    }
    return ivs.length == 50;
  });
  check('timestamp bounds 0 and 2^40-1 are accepted', () {
    _seal('login', _fields('login'), ts: 0);
    _seal('login', _fields('login'), ts: 1099511627775);
    return true;
  });
  for (final ts in [-1, 1099511627776, 9007199254740991]) {
    expectCode('timestamp $ts is refused', 'MALFORMED', () => _seal('login', _fields('login'), ts: ts));
  }

  for (final c in validationCases) {
    final name = c[0] as String, op = c[1] as String, expected = c[3] as String;
    final fields = Map<String, String>.from(c[2] as Map);
    if (expected == 'OK') {
      check('validation (Python parity): $name -> OK', () {
        _seal(op, fields);
        return true;
      });
    } else {
      expectCode('validation (Python parity): $name -> $expected', expected, () => _seal(op, fields));
    }
  }

  for (final c in sealBytesCases) {
    final name = c[0], expected = c[4];
    void attempt() => sealBytes(_key, c[1], c[2], c[3], vectorTs, Uint8List.fromList(utf8.encode('{}')));
    if (expected == 'OK') {
      check('header validation (Python parity): $name -> OK', () {
        attempt();
        return true;
      });
    } else {
      expectCode('header validation (Python parity): $name -> $expected', expected, attempt);
    }
  }

  final weak = RSAPublicKey(BigInt.two.pow(2047) + BigInt.one, BigInt.from(65537));
  expectCode('a 2048-bit RSAPublicKey object is refused', 'MALFORMED',
      () => sealCredentials(publicKey: weak, kid: rsa3072Kid, operation: 'login', nonce: vectorNonce, fields: _fields('login'), nowSeconds: vectorTs));

  check('errors carry only a code, never the input', () {
    final secret = 'G2pw-leak-check-$_password';
    for (final fields in [
      {'email': 'no-at-sign', 'password': secret},
      {'email': _email, 'password': '$secret\n'},
      {'email': _email, 'password': secret * 20},
    ]) {
      try {
        _seal('login', fields);
        return false;
      } on EnvelopeError catch (e) {
        final text = '${e.toString()} ${e.code} $e';
        if (text.contains(secret) || text.contains('no-at-sign') || text.contains(_email)) return false;
      }
    }
    return true;
  });
}
