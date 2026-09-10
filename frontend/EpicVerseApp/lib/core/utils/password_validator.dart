/// Single source of truth for the app's password policy.
///
/// [validate] is the gate used by form validators and by the submit-button
/// enable check. [requirementsFor] exposes the *same* rules as independent
/// pass/fail items so the UI can show every requirement at once and update
/// each one live — without the on-screen list ever drifting from what
/// [validate] actually enforces.
class PasswordValidator {
  PasswordValidator._();

  static const int minLength = 8;

  static final RegExp _uppercase = RegExp(r'[A-Z]');
  static final RegExp _lowercase = RegExp(r'[a-z]');
  static final RegExp _number = RegExp(r'[0-9]');
  static final RegExp _special =
      RegExp(r'''[!@#$%^&*()_+\-=\[\]{};:'"\\|,.<>/?~`]+''');

  /// Passwords rejected as too common / easily guessable.
  static const List<String> commonPasswords = [
    '123456', '12345678', '123456789', 'password', '12345', '1234567',
    'qwerty', '1234567890', 'iloveyou', '111111', 'password123', 'admin',
    'welcome', 'letmein'
  ];

  /// Returns an error message for the first unmet rule, or `null` if the
  /// password satisfies the full policy. Behaviour is unchanged from before —
  /// only the internals were refactored so the checks can be reused.
  static String? validate(String? value) {
    if (value == null || value.isEmpty) {
      return 'Password is required';
    }
    if (value.length < minLength) {
      return 'Password must be at least 8 characters';
    }
    if (!_uppercase.hasMatch(value)) {
      return 'Must contain at least one uppercase letter';
    }
    if (!_lowercase.hasMatch(value)) {
      return 'Must contain at least one lowercase letter';
    }
    if (!_number.hasMatch(value)) {
      return 'Must contain at least one number';
    }
    if (!_special.hasMatch(value)) {
      return 'Must contain at least one special character';
    }

    if (commonPasswords.contains(value.toLowerCase())) {
      return 'This password is too common and easily guessable';
    }

    return null;
  }

  /// True when [value] satisfies every rule enforced by [validate].
  /// Use this to enable/disable a submit button.
  static bool meetsPolicy(String value) => validate(value) == null;

  /// The full requirement list with each rule evaluated independently against
  /// [value]. Mirrors [validate] exactly — if you change one, change the other.
  static List<PasswordRequirement> requirementsFor(String value) {
    return [
      PasswordRequirement(
        'At least 8 characters',
        value.length >= minLength,
      ),
      PasswordRequirement(
        'At least 1 uppercase letter (A–Z)',
        _uppercase.hasMatch(value),
      ),
      PasswordRequirement(
        'At least 1 lowercase letter (a–z)',
        _lowercase.hasMatch(value),
      ),
      PasswordRequirement(
        'At least 1 number (0–9)',
        _number.hasMatch(value),
      ),
      PasswordRequirement(
        'At least 1 special character',
        _special.hasMatch(value),
      ),
      PasswordRequirement(
        'Not a common or guessable password',
        value.isNotEmpty && !commonPasswords.contains(value.toLowerCase()),
      ),
    ];
  }
}

/// One password rule and whether the current input satisfies it.
class PasswordRequirement {
  const PasswordRequirement(this.label, this.satisfied);

  final String label;
  final bool satisfied;
}
