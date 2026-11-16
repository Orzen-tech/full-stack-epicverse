import 'package:epicverse/core/errors/app_exception.dart';
import 'package:epicverse/core/errors/error_handler.dart';
import 'package:epicverse/core/widgets/error_card.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('every error type has a short title and a severity', () {
    for (final t in AppExceptionType.values) {
      expect(errorTitleFor(t).isNotEmpty, isTrue);
      expect(errorTitleFor(t).length, lessThanOrEqualTo(30));
    }
    expect(errorSeverityFor(AppExceptionType.network), ErrorSeverity.connection);
    expect(errorSeverityFor(AppExceptionType.timeout), ErrorSeverity.connection);
    expect(errorSeverityFor(AppExceptionType.rateLimit), ErrorSeverity.info);
    expect(errorSeverityFor(AppExceptionType.server), ErrorSeverity.failure);
  });

  Future<void> show(WidgetTester tester, {VoidCallback? onRetry, AppExceptionType? type}) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: Builder(
          builder: (context) => TextButton(
            onPressed: () => type == null
                ? ErrorHandler.showErrorSnackBar('Pick a mode first.', context: context, onRetry: onRetry)
                : ErrorHandler.showErrorSnackBar('Msg.', context: context, onRetry: onRetry, type: type),
            child: const Text('go'),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('go'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 600));
  }

  testWidgets('card shows title, message and Retry; Retry runs and closes it', (tester) async {
    var n = 0;
    await show(tester, onRetry: () => n++, type: AppExceptionType.network);
    expect(find.text('Connection problem'), findsOneWidget);
    expect(find.text('Msg.'), findsOneWidget);
    await tester.tap(find.text('Retry'));
    expect(n, 1);
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('Msg.'), findsNothing);
  });

  testWidgets('dismiss button closes the card; no Retry without a callback', (tester) async {
    await show(tester);
    expect(find.text('Check your details'), findsOneWidget);
    expect(find.text('Retry'), findsNothing);
    await tester.tap(find.byTooltip('Dismiss'));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    expect(find.text('Pick a mode first.'), findsNothing);
  });

  testWidgets('a new error replaces the old one', (tester) async {
    await show(tester);
    await tester.tap(find.text('go'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 600));
    expect(find.text('Pick a mode first.'), findsOneWidget);
  });
}
