import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/errors/error_mapper.dart';

/// H2: every backend request goes through the pinned, App-Check-aware,
/// MFA-aware `apiClient`. These guards read the source so a new raw client
/// fails CI, and they pin the F-09 transport contract that apiClient provides.
const _migratedScreens = [
  'lib/presentation/screens/faq_screen.dart',
  'lib/presentation/screens/legal_content_screen.dart',
  'lib/presentation/screens/feedback_screen.dart',
  'lib/presentation/screens/settings_screen.dart',
  'lib/presentation/screens/splash_screen.dart',
];

List<File> _dartFiles() => Directory('lib')
    .listSync(recursive: true)
    .whereType<File>()
    .where((f) => f.path.endsWith('.dart'))
    .toList();

String _code(File f) =>
    f.readAsLinesSync().where((l) => !l.trim().startsWith('//')).join('\n');

void main() {
  group('the five migrated files use the pinned apiClient', () {
    for (final path in _migratedScreens) {
      test(path, () {
        final src = _code(File(path));
        expect(
          RegExp(r'\bDio\(').hasMatch(src),
          isFalse,
          reason: 'raw Dio() constructed',
        );
        expect(
          src.contains('package:dio/dio.dart'),
          isFalse,
          reason: 'still imports dio directly',
        );
        expect(src.contains('DioException'), isFalse);
        expect(
          src.contains('ApiConfig.'),
          isFalse,
          reason: 'builds backend URLs by hand',
        );
        expect(src.contains('network/api_client.dart'), isTrue);
        expect(src.contains('apiClient.'), isTrue);
      });
    }

    test('endpoints, methods and bodies are unchanged', () {
      String src(String p) => _code(File(p));
      expect(src(_migratedScreens[0]), contains("apiClient.get('/faq')"));
      expect(
        src(_migratedScreens[1]),
        contains('apiClient.get(widget.endpoint)'),
      );
      expect(
        src(_migratedScreens[2]),
        contains("apiClient.post('/feedback', data: {'message': message})"),
      );
      final settings = src(_migratedScreens[3]);
      expect('/sync-user'.allMatches(settings).length, 2);
      expect(settings, contains("apiClient.delete('/user/\${user.id}')"));
      final splash = src(_migratedScreens[4]);
      expect(splash, contains("apiClient.get('/user/\${firebaseUser.uid}')"));
      expect(
        splash,
        contains(
          "apiClient.post('/user/\${firebaseUser.uid}/cancel-deletion')",
        ),
      );
    });
  });

  test(
    'no raw network client exists anywhere in lib/ except the pinned factory',
    () {
      final offenders = <String>[];
      for (final f in _dartFiles()) {
        final src = _code(f);
        final isPinningService = f.path.endsWith(
          'core/network/ssl_pinning_service.dart',
        );
        if (!isPinningService && RegExp(r'\bDio\(').hasMatch(src)) {
          offenders.add('${f.path}: Dio()');
        }
        if (!isPinningService && RegExp(r'\bHttpClient\(').hasMatch(src)) {
          offenders.add('${f.path}: HttpClient()');
        }
        if (src.contains('package:http/')) {
          offenders.add('${f.path}: package:http');
        }
      }
      expect(offenders, isEmpty, reason: offenders.join('\n'));
    },
  );

  test('no request URL is assembled by hand outside the networking core', () {
    const allowed = [
      'core/network/api_client.dart',
      'core/network/api_config.dart',
      'core/network/websocket_service.dart', // uses wsUrl for the pinned WebSocket
      'presentation/screens/companion_ready_screen.dart', // a debugPrint only
    ];
    final offenders = <String>[];
    for (final f in _dartFiles()) {
      if (allowed.any(f.path.endsWith)) continue;
      if (RegExp(r'ApiConfig\.(apiUrl|baseUrl|wsUrl)').hasMatch(_code(f))) {
        offenders.add(f.path);
      }
    }
    expect(offenders, isEmpty, reason: offenders.join('\n'));
  });

  test('F-09 transport contract of apiClient is intact', () {
    final client = _code(File('lib/core/network/api_client.dart'));
    expect(client, contains('SslPinningService.createPinnedDio'));
    expect(
      client,
      contains('ApiConfig.authHeaders()'),
    ); // ID token + X-MFA-Session
    expect(client, contains('X-Firebase-AppCheck'));
    expect(client, contains('AppExceptionType.mfaSessionRequired'));
    expect(client, contains('MfaReprompt.handler')); // one re-prompt then retry
    final config = _code(File('lib/core/network/api_config.dart'));
    expect(config, contains("'X-MFA-Session'"));
    expect(
      config,
      contains("String.fromEnvironment("),
    ); // production default retained
    final ws = _code(File('lib/core/network/websocket_service.dart'));
    expect(ws, contains("'X-MFA-Session'"));
    expect(ws, contains('createPinnedWebSocketHttpClient'));
  });

  test("splash's 404 branch matches what apiClient actually throws", () {
    final req = RequestOptions(path: '/user/uid');
    final mapped = ErrorMapper.fromException(
      DioException(
        requestOptions: req,
        type: DioExceptionType.badResponse,
        response: Response(
          requestOptions: req,
          statusCode: 404,
          data: {'detail': 'User not found'},
        ),
      ),
    );
    expect(mapped.statusCode, 404);
    expect(mapped.type, AppExceptionType.notFound);
    final splash = _code(File('lib/presentation/screens/splash_screen.dart'));
    expect(splash, contains('on AppException catch (e)'));
    expect(splash, contains('e.statusCode == 404'));
    // a transient server error is NOT mistaken for a missing profile
    final server = ErrorMapper.fromException(
      DioException(
        requestOptions: req,
        type: DioExceptionType.badResponse,
        response: Response(requestOptions: req, statusCode: 503),
      ),
    );
    expect(server.statusCode, isNot(404));
  });
}
