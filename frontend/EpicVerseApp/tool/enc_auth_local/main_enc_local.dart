// LOCAL DEVELOPMENT ENTRY POINT for the encrypted-auth integration. The sealer is built into the app now (lib/core/security/enc_auth/),
// so this is the normal app started with the encrypted-auth defines:
//   flutter run -t tool/enc_auth_local/main_enc_local.dart --dart-define=ENC_AUTH_MODE=required \
//       --dart-define=ENC_AUTH_KID=... --dart-define=ENC_AUTH_PUBLIC_KEY_B64=... --dart-define=API_BASE_URL=...
// Nothing is registered here: in a `required` build EncAuthRuntime creates the built-in sealer from the two key defines, and without a valid
// key every credential operation fails closed. (The same defines work with the normal entry point, lib/main.dart.)
import 'package:epicverse/main.dart' as app;

Future<void> main() => app.main();
