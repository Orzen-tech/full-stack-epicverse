import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:epicverse/core/errors/error_mapper.dart';

void main() {
  test('maps camera access denied platform exception to a user-friendly message', () {
    final exception = ErrorMapper.fromException(
      PlatformException(
        code: 'camera_access_denied',
        message: 'The user did not allow camera access.',
      ),
    );

    expect(
      exception.userMessage,
      'Camera access was denied. Please allow camera access to take a photo.',
    );
  });
}
