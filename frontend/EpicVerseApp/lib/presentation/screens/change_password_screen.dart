import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';

import '../../core/constants/app_colors.dart';
import '../../core/utils/password_validator.dart';
import '../widgets/network_background.dart';

class ChangePasswordScreen extends StatefulWidget {
  const ChangePasswordScreen({super.key});

  @override
  State<ChangePasswordScreen> createState() => _ChangePasswordScreenState();
}

class _ChangePasswordScreenState extends State<ChangePasswordScreen> {
  final _formKey = GlobalKey<FormState>();
  final _currentPasswordController = TextEditingController();
  final _newPasswordController = TextEditingController();
  final _confirmPasswordController = TextEditingController();
  bool _isLoading = false;
  bool _obscureCurrentPassword = true;
  bool _obscureNewPassword = true;
  bool _obscureConfirmPassword = true;
  String? _message;
  bool _messageIsError = false;

  @override
  void dispose() {
    _currentPasswordController.dispose();
    _newPasswordController.dispose();
    _confirmPasswordController.dispose();
    super.dispose();
  }

  Future<void> _changePassword() async {
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() => _message = null);
    if (!_formKey.currentState!.validate()) return;

    final firebaseUser = FirebaseAuth.instance.currentUser;
    final email = firebaseUser?.email;
    if (firebaseUser == null || email == null || email.isEmpty) {
      _showMessage('Please sign in again before changing your password.', true);
      return;
    }

    setState(() => _isLoading = true);
    try {
      final credential = EmailAuthProvider.credential(
        email: email,
        password: _currentPasswordController.text,
      );
      await firebaseUser.reauthenticateWithCredential(credential);

      if (_newPasswordController.text == _currentPasswordController.text) {
        _showMessage('New password must be different from your current password.', true);
        return;
      }

      await firebaseUser.updatePassword(_newPasswordController.text);
      _currentPasswordController.clear();
      _newPasswordController.clear();
      _confirmPasswordController.clear();
      _showMessage('Password changed successfully.', false);
    } on FirebaseAuthException catch (error) {
      final message = switch (error.code) {
        'wrong-password' || 'invalid-credential' || 'user-mismatch' =>
          'Current password is incorrect.',
        'requires-recent-login' =>
          'Please sign in again before changing your password.',
        'network-request-failed' =>
          'Unable to connect. Please check your internet connection and try again.',
        'weak-password' => 'New password does not meet the password policy.',
        _ => 'Unable to change password. Please try again.',
      };
      _showMessage(message, true);
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  void _showMessage(String message, bool isError) {
    if (!mounted) return;
    setState(() {
      _message = message;
      _messageIsError = isError;
    });
  }

  String? _required(String? value) {
    if (value == null || value.isEmpty) return 'This field is required.';
    return null;
  }

  String? _validateNewPassword(String? value) {
    final requiredError = _required(value);
    if (requiredError != null) return requiredError;
    return PasswordValidator.validate(value);
  }

  String? _validateConfirmation(String? value) {
    final requiredError = _required(value);
    if (requiredError != null) return requiredError;
    if (value != _newPasswordController.text) return 'Passwords do not match.';
    return null;
  }

  InputDecoration _decoration(String label, bool obscure, VoidCallback onToggle) {
    return InputDecoration(
      labelText: label,
      labelStyle: const TextStyle(color: AppColors.textMuted),
      errorStyle: const TextStyle(color: Colors.redAccent),
      enabledBorder: const UnderlineInputBorder(
        borderSide: BorderSide(color: Colors.white24),
      ),
      focusedBorder: const UnderlineInputBorder(
        borderSide: BorderSide(color: AppColors.primaryGold),
      ),
      errorBorder: const UnderlineInputBorder(
        borderSide: BorderSide(color: Colors.redAccent),
      ),
      focusedErrorBorder: const UnderlineInputBorder(
        borderSide: BorderSide(color: Colors.redAccent),
      ),
      suffixIcon: IconButton(
        onPressed: onToggle,
        tooltip: obscure ? 'Show password' : 'Hide password',
        icon: Icon(
          obscure ? Icons.visibility_outlined : Icons.visibility_off_outlined,
          color: AppColors.textMuted,
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.fromLTRB(24, 16, 24, 32),
              children: [
                Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.arrow_back, color: AppColors.textPrimary),
                    ),
                    const SizedBox(width: 16),
                    const Text(
                      'Change Password',
                      style: TextStyle(
                        color: AppColors.textPrimary,
                        fontSize: 24,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 32),
                const Text(
                  'Verify your identity and choose a new password.',
                  style: TextStyle(color: AppColors.textSecondary, fontSize: 14),
                ),
                const SizedBox(height: 28),
                TextFormField(
                  controller: _currentPasswordController,
                  obscureText: _obscureCurrentPassword,
                  enabled: !_isLoading,
                  validator: _required,
                  style: const TextStyle(color: AppColors.textPrimary),
                  decoration: _decoration(
                    'Current Password',
                    _obscureCurrentPassword,
                    () => setState(() => _obscureCurrentPassword = !_obscureCurrentPassword),
                  ),
                ),
                const SizedBox(height: 22),
                TextFormField(
                  controller: _newPasswordController,
                  obscureText: _obscureNewPassword,
                  enabled: !_isLoading,
                  validator: _validateNewPassword,
                  style: const TextStyle(color: AppColors.textPrimary),
                  decoration: _decoration(
                    'New Password',
                    _obscureNewPassword,
                    () => setState(() => _obscureNewPassword = !_obscureNewPassword),
                  ),
                ),
                const SizedBox(height: 22),
                TextFormField(
                  controller: _confirmPasswordController,
                  obscureText: _obscureConfirmPassword,
                  enabled: !_isLoading,
                  validator: _validateConfirmation,
                  style: const TextStyle(color: AppColors.textPrimary),
                  decoration: _decoration(
                    'Confirm New Password',
                    _obscureConfirmPassword,
                    () => setState(() => _obscureConfirmPassword = !_obscureConfirmPassword),
                  ),
                ),
                const SizedBox(height: 30),
                SizedBox(
                  height: 52,
                  child: ElevatedButton(
                    onPressed: _isLoading ? null : _changePassword,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.primaryGold,
                      foregroundColor: Colors.black,
                      disabledBackgroundColor: AppColors.primaryGold.withOpacity(0.5),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    ),
                    child: _isLoading
                        ? const SizedBox(
                            width: 22,
                            height: 22,
                            child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black),
                          )
                        : const Text('Change Password'),
                  ),
                ),
                if (_message != null) ...[
                  const SizedBox(height: 18),
                  Text(
                    _message!,
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: _messageIsError ? Colors.redAccent : Colors.greenAccent,
                      fontSize: 14,
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}