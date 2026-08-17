class PasswordValidator {
  static String? validate(String? value) {
    if (value == null || value.isEmpty) {
      return 'Password is required';
    }
    if (value.length < 8) {
      return 'Password must be at least 8 characters';
    }
    if (!RegExp(r'[A-Z]').hasMatch(value)) {
      return 'Must contain at least one uppercase letter';
    }
    if (!RegExp(r'[a-z]').hasMatch(value)) {
      return 'Must contain at least one lowercase letter';
    }
    if (!RegExp(r'[0-9]').hasMatch(value)) {
      return 'Must contain at least one number';
    }
    if (!RegExp(r'[!@#\$%\^&\*\(\)_\+\-\=\[\]\{\};:\'\"\\|,.<>\/?~`]+').hasMatch(value)) {
      return 'Must contain at least one special character';
    }
    
    const weakPasswords = [
      '123456', '12345678', '123456789', 'password', '12345', '1234567',
      'qwerty', '1234567890', 'iloveyou', '111111', 'password123', 'admin',
      'welcome', 'letmein'
    ];
    if (weakPasswords.contains(value.toLowerCase())) {
      return 'This password is too common and easily guessable';
    }
    
    return null;
  }
}
