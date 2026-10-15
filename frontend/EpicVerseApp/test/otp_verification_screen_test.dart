import 'dart:async';

import 'package:dio/dio.dart';
import 'package:epicverse/presentation/screens/otp_verification_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

// OTP entry screen: a code is submitted only when it goes from incomplete to complete; a rejected code empties the
// boxes so a correction (or a repeated tap) can never re-submit stale or intermediate values; one request is in flight
// at a time. The backend limits (5 attempts, 1 minute) are not touched. Everything runs against fake callbacks: no
// network, no Firebase.

class _Harness {
  final List<String> submitted = [];
  int verified = 0;
  int resends = 0;
}

Finder _box(int i) => find.byType(TextField).at(i);

List<String> _boxes(WidgetTester tester) =>
    tester.widgetList<TextField>(find.byType(TextField)).map((t) => t.controller!.text).toList();

Future<void> _pump(
  WidgetTester tester,
  _Harness h, {
  required Future<String?> Function(String otp) onSubmit,
  Future<String?> Function()? onResend,
}) async {
  await tester.pumpWidget(MaterialApp(
    home: OtpVerificationScreen(
      email: 'test@example.invalid',
      onSubmitOtp: (otp) {
        h.submitted.add(otp);
        return onSubmit(otp);
      },
      onResend: onResend == null
          ? null
          : () {
              h.resends++;
              return onResend();
            },
      onVerified: () => h.verified++,
      onBack: () {},
    ),
  ));
  await tester.pump();
}

Future<void> _type(WidgetTester tester, String digits, {int from = 0}) async {
  for (var i = 0; i < digits.length; i++) {
    await tester.enterText(_box(from + i), digits[i]);
    await tester.pump();
  }
}

Future<void> _tearDown(WidgetTester tester) async {
  await tester.pumpWidget(const SizedBox()); // disposes the screen and cancels its timers
}

void main() {
  testWidgets('correct code on the first attempt: exactly one submission, no repeats', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => null);
    await _type(tester, '123456');
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['123456']);
    expect(h.verified, 1);
    await tester.pump(const Duration(seconds: 2));
    expect(h.submitted, ['123456'], reason: 'nothing may be submitted again after success');
    await _tearDown(tester);
  });

  testWidgets('five digits never submit; the sixth submits once', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => null);
    await _type(tester, '12345');
    await tester.pump(const Duration(seconds: 1));
    expect(h.submitted, isEmpty);
    await _type(tester, '6', from: 5);
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['123456']);
    await _tearDown(tester);
  });

  testWidgets('a wrong code clears the boxes, shows the message and is not re-submitted by clearing', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => 'Invalid or expired code');
    await _type(tester, '123456');
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['123456']);
    expect(h.verified, 0);
    expect(find.text('Invalid or expired code'), findsOneWidget);
    expect(_boxes(tester), List.filled(6, ''), reason: 'a rejected code must not stay in the boxes');
    await tester.pump(const Duration(seconds: 2));
    expect(h.submitted, ['123456'], reason: 'clearing the boxes must not trigger a verification');
    await _tearDown(tester);
  });

  testWidgets('correcting a failed code: second, full code is the only further submission', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (otp) async => otp == '654321' ? null : 'Invalid or expired code');
    await _type(tester, '123456');
    await tester.pump(const Duration(milliseconds: 300));
    await _type(tester, '654321');
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['123456', '654321'], reason: 'no intermediate or mixed values may be submitted');
    expect(h.verified, 1);
    await _tearDown(tester);
  });

  testWidgets('backspace then retyping submits once, when the code is complete again', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => null);
    await _type(tester, '12345');
    await tester.enterText(_box(4), ''); // backspace
    await tester.pump();
    expect(h.submitted, isEmpty);
    await _type(tester, '5', from: 4);
    expect(h.submitted, isEmpty, reason: 'still one box short');
    await _type(tester, '6', from: 5);
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['123456']);
    await _tearDown(tester);
  });

  testWidgets('in-flight protection: edits and taps while a verification is pending add no second request', (tester) async {
    final h = _Harness();
    final pending = Completer<String?>();
    await _pump(tester, h, onSubmit: (_) => pending.future);
    await _type(tester, '123456');
    await tester.pump();
    expect(h.submitted, ['123456']);
    expect(find.text('VERIFY'), findsNothing, reason: 'the button is replaced by a spinner while loading');
    // Edit the last digit away and back: the code becomes complete again while the first call is still pending.
    await tester.enterText(_box(5), '');
    await tester.pump();
    await tester.enterText(_box(5), '9');
    await tester.pump();
    expect(h.submitted, ['123456'], reason: 'a second request must not start while one is in flight');
    pending.complete('Invalid or expired code');
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted.length, 1);
    expect(_boxes(tester), List.filled(6, ''));
    await _tearDown(tester);
  });

  testWidgets('VERIFY with an incomplete code never calls the backend', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => null);
    await tester.tap(find.text('VERIFY'));
    await tester.pump();
    expect(h.submitted, isEmpty, reason: 'empty boxes');
    await _type(tester, '12345');
    await tester.tap(find.text('VERIFY'));
    await tester.pump();
    expect(h.submitted, isEmpty, reason: 'five digits are not a code');
    await _tearDown(tester);
  });

  testWidgets('after a failure, tapping VERIFY repeatedly cannot re-submit the rejected code', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => 'Invalid or expired code');
    await _type(tester, '123456');
    await tester.pump(const Duration(milliseconds: 300));
    for (var i = 0; i < 3; i++) {
      await tester.tap(find.text('VERIFY'));
      await tester.pump();
    }
    expect(h.submitted, ['123456']);
    await _tearDown(tester);
  });

  testWidgets('a 400 raised as an exception is a confirmed rejection: message shown and boxes cleared', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async {
      final req = RequestOptions(path: '/verify');
      throw DioException(
        requestOptions: req,
        response: Response(requestOptions: req, statusCode: 400),
        type: DioExceptionType.badResponse,
      );
    });
    await _type(tester, '123456');
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['123456']);
    expect(find.text('Invalid or expired verification code. Please try again.'), findsOneWidget);
    expect(_boxes(tester), List.filled(6, ''));
    await _tearDown(tester);
  });

  testWidgets('a successful resend clears a stale partial code, which is then not mixed with the new one', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => null, onResend: () async => null);
    await _type(tester, '123'); // a partly typed, now stale code
    await tester.pump(const Duration(seconds: 61)); // the resend countdown ends
    await tester.tap(find.textContaining('Resend'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.resends, 1);
    expect(_boxes(tester), List.filled(6, ''), reason: 'the stale code must be gone');
    expect(h.submitted, isEmpty);
    await _type(tester, '654321');
    await tester.pump(const Duration(milliseconds: 300));
    expect(h.submitted, ['654321']);
    await _tearDown(tester);
  });

  testWidgets('pasting a whole code into one box stays a single digit and submits nothing (existing behaviour)', (tester) async {
    final h = _Harness();
    await _pump(tester, h, onSubmit: (_) async => null);
    await tester.enterText(_box(0), '123456');
    await tester.pump(const Duration(milliseconds: 300));
    expect(_boxes(tester).first.length, lessThanOrEqualTo(1));
    expect(h.submitted, isEmpty);
    await _tearDown(tester);
  });
}
