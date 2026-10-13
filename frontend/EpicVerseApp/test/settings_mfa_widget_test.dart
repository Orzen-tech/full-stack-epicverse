import 'package:dio/dio.dart';
import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/network/api_config.dart';
import 'package:epicverse/core/security/mfa_flow.dart';
import 'package:epicverse/models/user_model.dart';
import 'package:epicverse/presentation/screens/settings_screen.dart';
import 'package:epicverse/providers/user_provider.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

// Widget tests for Settings -> Two-factor authentication (G1). The session-status read is stubbed; every OTHER
// apiClient request is counted and aborted before it can leave the process, so the tests can prove that no
// request (in particular no legacy /user/update-mfa) is made.

class _SeededUser extends UserNotifier {
  _SeededUser(this._user);
  final UserModel _user;
  @override
  UserModel? build() => _user;
}

UserModel _user({required bool mfa}) => UserModel(id: 'fake-uid', displayName: 'Fake User', email: 'fake@example.invalid', mfaEnabled: mfa);

Response<dynamic> _ok(Map<String, dynamic> body) =>
    Response(requestOptions: RequestOptions(path: '/auth/session-status'), statusCode: 200, data: body);

AppException _legacy404() {
  final req = RequestOptions(path: '/auth/session-status');
  return AppException(
    userMessage: 'x',
    statusCode: 404,
    type: AppExceptionType.notFound,
    originalException: DioException(
      requestOptions: req,
      type: DioExceptionType.badResponse,
      response: Response(requestOptions: req, statusCode: 404, data: {'detail': 'Not Found'}),
    ),
  );
}

AppException _networkDown() => AppException(
      userMessage: 'No internet connection.',
      statusCode: null,
      type: AppExceptionType.network,
      originalException: DioException(
        requestOptions: RequestOptions(path: '/auth/session-status'),
        type: DioExceptionType.connectionError,
      ),
    );

AppException _serverError() => AppException(
      userMessage: 'Server error',
      statusCode: 500,
      type: AppExceptionType.server,
      originalException: Exception('500'),
    );

const _legacyText = 'Available after our next server update.';

void main() {
  var otherRequests = 0;

  setUp(() {
    otherRequests = 0;
    // Any apiClient request is counted and aborted in its interceptor, before any network access.
    ApiConfig.identityForTest = () async {
      otherRequests++;
      throw StateError('no network in widget tests');
    };
  });

  tearDown(() {
    ApiConfig.identityForTest = null;
    setMfaStatusTransportForTest(null);
  });

  Future<void> pumpSettings(WidgetTester tester, UserModel user) async {
    tester.view.physicalSize = const Size(900, 3000);
    tester.view.devicePixelRatio = 1.0;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(ProviderScope(
      overrides: [userProvider.overrideWith(() => _SeededUser(user))],
      child: const MaterialApp(home: SettingsScreen()),
    ));
    await tester.pump(); // initState kicks off the capability check
    await tester.pump(const Duration(milliseconds: 50));
  }

  SwitchListTile toggle(WidgetTester tester) => tester.widget<SwitchListTile>(find.byType(SwitchListTile).first);

  testWidgets('while the capability is unknown the toggle is disabled', (tester) async {
    setMfaStatusTransportForTest((path) => Future<Response<dynamic>>.delayed(const Duration(seconds: 5), () => _ok({})));
    await pumpSettings(tester, _user(mfa: false));
    expect(toggle(tester).onChanged, isNull);
    expect(find.text('Checking availability...'), findsOneWidget);
    await tester.pump(const Duration(seconds: 6)); // let the stubbed delay finish
  });

  testWidgets('legacy backend: toggle disabled, explanation visible, current MFA state shown, no request', (tester) async {
    setMfaStatusTransportForTest((path) async => throw _legacy404());
    await pumpSettings(tester, _user(mfa: true));
    expect(toggle(tester).onChanged, isNull);
    expect(toggle(tester).value, isTrue); // the existing MFA state is preserved
    expect(find.text(_legacyText), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
    await tester.tap(find.byType(SwitchListTile).first, warnIfMissed: false); // a tap on a disabled switch does nothing
    await tester.pump();
    expect(toggle(tester).value, isTrue);
    expect(otherRequests, 0, reason: 'no apiClient request (so no /user/update-mfa) may be made');
  });

  testWidgets('modern backend: toggle enabled after capability confirmation; shows the server MFA state', (tester) async {
    setMfaStatusTransportForTest((path) async => _ok({'email_verified': true, 'mfa_enabled': true, 'mfa_session_valid': true}));
    await pumpSettings(tester, _user(mfa: false)); // profile says off, server says on: the server wins
    expect(toggle(tester).onChanged, isNotNull);
    expect(toggle(tester).value, isTrue);
    expect(find.text('Email OTP required on login'), findsOneWidget);
    expect(find.text(_legacyText), findsNothing);
    expect(otherRequests, 0);
  });

  testWidgets('modern backend with MFA off shows the enable prompt', (tester) async {
    setMfaStatusTransportForTest((path) async => _ok({'email_verified': true, 'mfa_enabled': false, 'mfa_session_valid': false}));
    await pumpSettings(tester, _user(mfa: false));
    expect(toggle(tester).onChanged, isNotNull);
    expect(toggle(tester).value, isFalse);
    expect(find.text('Tap to enable extra security'), findsOneWidget);
  });

  for (final failure in <String, AppException Function()>{'network failure': _networkDown, 'server error': _serverError}.entries) {
    testWidgets('${failure.key}: toggle disabled and Retry visible; retry success restores the modern state', (tester) async {
      var healthy = false;
      setMfaStatusTransportForTest((path) async {
        if (!healthy) throw failure.value();
        return _ok({'email_verified': true, 'mfa_enabled': true, 'mfa_session_valid': true});
      });
      await pumpSettings(tester, _user(mfa: true));
      expect(toggle(tester).onChanged, isNull);
      expect(find.text('Retry'), findsOneWidget);
      expect(find.text('Could not check availability right now.'), findsOneWidget);
      expect(find.text(_legacyText), findsNothing);
      expect(toggle(tester).value, isTrue); // existing state preserved while unavailable

      healthy = true;
      await tester.tap(find.text('Retry'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 50));
      expect(toggle(tester).onChanged, isNotNull);
      expect(toggle(tester).value, isTrue);
      expect(find.text('Retry'), findsNothing);
      expect(find.text('Email OTP required on login'), findsOneWidget);
      expect(otherRequests, 0);
    });
  }

  testWidgets('retry that hits a legacy backend ends in the legacy explanation, never enabled', (tester) async {
    var calls = 0;
    setMfaStatusTransportForTest((path) async {
      calls++;
      throw calls == 1 ? _networkDown() : _legacy404();
    });
    await pumpSettings(tester, _user(mfa: false));
    expect(find.text('Retry'), findsOneWidget);
    await tester.tap(find.text('Retry'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 50));
    expect(toggle(tester).onChanged, isNull);
    expect(find.text(_legacyText), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
    expect(otherRequests, 0);
  });
}
