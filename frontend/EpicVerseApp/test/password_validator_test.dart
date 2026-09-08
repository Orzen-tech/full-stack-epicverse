import 'package:flutter_test/flutter_test.dart';

import 'package:epicverse/core/utils/password_validator.dart';

void main() {
  test('accepts a password that meets the policy', () {
    expect(PasswordValidator.validate('Secure!Pass1'), isNull);
  });

  test('rejects passwords missing required character classes', () {
    expect(PasswordValidator.validate('short'), isNotNull);
    expect(PasswordValidator.validate('lowercase1!'), contains('uppercase'));
    expect(PasswordValidator.validate('UPPERCASE1!'), contains('lowercase'));
    expect(PasswordValidator.validate('NoNumber!'), contains('number'));
    expect(PasswordValidator.validate('NoSpecial1'), contains('special'));
  });

  test('rejects common passwords', () {
    expect(PasswordValidator.validate('password123'), isNotNull);
  });
}
