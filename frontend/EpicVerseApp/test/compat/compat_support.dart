// Support for the local backend-contract tests (test/compat/*). They run the app's real networking stack against
// isolated local copies of the two backend versions, using FAKE users only. They are skipped unless the runner
// passes --dart-define=COMPAT_BACKEND=L|M and --dart-define=API_BASE_URL=http://127.0.0.1:<port>.
import 'dart:convert';
import 'dart:io';

import 'package:epicverse/core/network/api_config.dart';

const compatBackend = String.fromEnvironment('COMPAT_BACKEND');
const compatOutbox = String.fromEnvironment('COMPAT_OUTBOX');
final bool compatEnabled = compatBackend == 'L' || compatBackend == 'M'; // classic suites: deployed (L) / newer (M)
final bool compatAdditive = compatBackend == 'A'; // the additive MFA backend
final bool compatIsLegacy = compatBackend == 'L'; // L = deployed d733776, M = newer e121583

String _b64(Map<String, dynamic> m) => base64Url.encode(utf8.encode(jsonEncode(m))).replaceAll('=', '');

/// Unsigned Firebase-emulator-style ID token for a fake user. Only the local test backends accept it.
String fakeIdToken(String uid, String email, {int? authTime}) {
  final now = DateTime.now().millisecondsSinceEpoch ~/ 1000;
  const project = 'demo-epicverse';
  return '${_b64({'alg': 'none', 'typ': 'JWT'})}.${_b64({
    'iss': 'https://securetoken.google.com/$project',
    'aud': project,
    'sub': uid,
    'user_id': uid,
    'auth_time': authTime ?? now,
    'iat': now,
    'exp': now + 3600,
    'email': email,
    'email_verified': false,
    'firebase': {'identities': {'email': [email]}, 'sign_in_provider': 'password'},
  })}.';
}

/// [ageSeconds] is how long ago the (fake) Firebase sign-in happened: the token's auth_time.
void signInAs(String uid, String email, {int ageSeconds = 0}) {
  final authTime = DateTime.now().millisecondsSinceEpoch ~/ 1000 - ageSeconds;
  ApiConfig.identityForTest = () async => (uid: uid, token: fakeIdToken(uid, email, authTime: authTime));
}

/// Test-harness-only route of the isolated local backends: clears limiter/MFA rows and resets the fake users.
Future<void> resetBackend() async {
  final base = const String.fromEnvironment('API_BASE_URL');
  final client = HttpClient();
  try {
    final req = await client.postUrl(Uri.parse('$base/__test/reset'));
    final res = await req.close();
    await res.drain<void>();
    if (res.statusCode != 200) throw StateError('backend reset failed: ${res.statusCode}');
  } finally {
    client.close();
  }
}

/// Signed out: the override stays installed (returning no identity) so tests never touch FirebaseAuth.
void signOutFake() => ApiConfig.identityForTest = () async => null;

/// Latest one-time code the local backend "emailed" to [email] (captured, never sent).
Future<String> latestOtp(String email) async {
  final f = File('$compatOutbox/$compatBackend.jsonl');
  for (var i = 0; i < 40; i++) {
    if (f.existsSync()) {
      final lines = f.readAsLinesSync().where((l) => l.trim().isNotEmpty).map((l) => jsonDecode(l) as Map);
      final mine = lines.where((m) => (m['email'] as String).toLowerCase() == email.toLowerCase()).toList();
      if (mine.isNotEmpty) return mine.last['otp'] as String;
    }
    await Future<void>.delayed(const Duration(milliseconds: 100));
  }
  throw StateError('no captured OTP for $email');
}
