import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/network/api_client.dart';
import 'package:epicverse/core/network/api_config.dart';
import 'package:flutter_test/flutter_test.dart';

// W3: ApiClient reports only the HTTP status of real requests to the network-health signal.
// A local fake transport is used: no sockets, no real backend. Signed out via the existing
// ApiConfig.identityForTest seam, so no Firebase is touched.

class _FakeAdapter implements HttpClientAdapter {
  _FakeAdapter(this.respond);
  final Future<ResponseBody> Function(RequestOptions options) respond;
  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream, Future<void>? cancelFuture) =>
      respond(options);
  @override
  void close({bool force = false}) {}
}

ResponseBody _json(int status, Object body) => ResponseBody.fromString(
      jsonEncode(body),
      status,
      headers: {
        Headers.contentTypeHeader: [Headers.jsonContentType],
      },
    );

ApiClient _client(Future<ResponseBody> Function(RequestOptions) respond, void Function(int?) reporter) =>
    ApiClient(baseUrl: 'https://api.example.test', httpClientAdapter: _FakeAdapter(respond), healthReporter: reporter);

void main() {
  setUp(() => ApiConfig.identityForTest = () async => null);
  tearDown(() => ApiConfig.identityForTest = null);

  test('2xx: reported as reachable, response unchanged', () async {
    final reports = <int?>[];
    final c = _client((_) async => _json(200, {'ok': true}), reports.add);
    final r = await c.get<dynamic>('/thing');
    expect(r.statusCode, 200);
    expect(r.data, {'ok': true});
    expect(reports, [200]);
  });

  test('4xx: reported (reachable), original error still thrown', () async {
    final reports = <int?>[];
    final c = _client((_) async => _json(404, {'detail': 'nope'}), reports.add);
    await expectLater(c.get<dynamic>('/missing'),
        throwsA(isA<AppException>().having((e) => e.statusCode, 'statusCode', 404)));
    expect(reports, [404]);
  });

  test('5xx: reported as a failure status', () async {
    final reports = <int?>[];
    final c = _client((_) async => _json(503, {'detail': 'down'}), reports.add);
    await expectLater(c.get<dynamic>('/x'), throwsA(isA<AppException>()));
    expect(reports, [503]);
  });

  test('connection failure and timeout: reported as null (no answer)', () async {
    final reports = <int?>[];
    final refused = _client((_) async => throw const SocketException('connection refused'), reports.add);
    await expectLater(refused.get<dynamic>('/x'), throwsA(isA<AppException>()));
    final slow = _client(
        (o) async => throw DioException(requestOptions: o, type: DioExceptionType.receiveTimeout), reports.add);
    await expectLater(slow.get<dynamic>('/x'), throwsA(isA<AppException>()));
    expect(reports, [null, null]);
  });

  test('cancelled request: nothing reported', () async {
    final reports = <int?>[];
    final c = _client((_) async => _json(200, {}), reports.add);
    final token = CancelToken()..cancel('user left');
    await expectLater(c.get<dynamic>('/x', cancelToken: token), throwsA(isA<AppException>()));
    expect(reports, isEmpty);
  });

  test('a throwing reporter cannot change a successful response', () async {
    final c = _client((_) async => _json(200, {'v': 1}), (_) => throw StateError('reporter bug'));
    final r = await c.get<dynamic>('/thing');
    expect(r.statusCode, 200);
    expect(r.data, {'v': 1});
  });

  test('a throwing reporter cannot change the original request exception', () async {
    Future<AppException> failWith(void Function(int?) reporter, Future<ResponseBody> Function(RequestOptions) respond) async {
      try {
        await _client(respond, reporter).get<dynamic>('/x');
      } on AppException catch (e) {
        return e;
      }
      fail('expected the request to fail');
    }

    void boom(int? _) => throw StateError('reporter bug');
    for (final respond in <Future<ResponseBody> Function(RequestOptions)>[
      (_) async => _json(404, {'detail': 'nope'}),
      (_) async => _json(503, {'detail': 'down'}),
      (_) async => throw const SocketException('connection refused'),
    ]) {
      final normal = await failWith((_) {}, respond);
      final withBrokenReporter = await failWith(boom, respond);
      expect(withBrokenReporter.type, normal.type);
      expect(withBrokenReporter.statusCode, normal.statusCode);
      expect(withBrokenReporter.userMessage, normal.userMessage);
    }
  });
}
