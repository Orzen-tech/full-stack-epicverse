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

  test('meetsPolicy agrees with validate', () {
    expect(PasswordValidator.meetsPolicy('Secure!Pass1'), isTrue);
    expect(PasswordValidator.meetsPolicy('12345678'), isFalse);
    expect(PasswordValidator.meetsPolicy(''), isFalse);
  });

  test('requirementsFor evaluates every rule independently', () {
    final reqs = PasswordValidator.requirementsFor('1234abcd');
    // ['>=8 chars', 'uppercase', 'lowercase', 'number', 'special', 'not common']
    expect(reqs[0].satisfied, isTrue); // 8 characters
    expect(reqs[1].satisfied, isFalse); // uppercase
    expect(reqs[2].satisfied, isTrue); // lowercase
    expect(reqs[3].satisfied, isTrue); // number
    expect(reqs[4].satisfied, isFalse); // special
    expect(reqs[5].satisfied, isTrue); // '1234abcd' is not in the common list

    // A known common password fails the "not common" rule independently.
    expect(
      PasswordValidator.requirementsFor('12345678')[5].satisfied,
      isFalse,
    );
  });

  test('requirementsFor all-satisfied exactly matches meetsPolicy', () {
    const good = 'Secure!Pass1';
    expect(
      PasswordValidator.requirementsFor(good).every((r) => r.satisfied),
      PasswordValidator.meetsPolicy(good),
    );
  });
}
