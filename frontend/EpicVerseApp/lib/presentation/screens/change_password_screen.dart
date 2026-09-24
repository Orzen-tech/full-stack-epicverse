import 'dart:async';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:dio/dio.dart';

import '../../core/constants/app_colors.dart';
import '../../core/network/api_client.dart';
import '../../core/utils/password_validator.dart';
import '../widgets/network_background.dart';
import '../widgets/password_requirements.dart';

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

  // "Forgot Password?" (sends a reset email to the signed-in user's own
  // Firebase account email) — independent of the change-password form.
  bool _isSendingReset = false;
  int _resetCooldownSeconds = 0;
  Timer? _resetCooldownTimer;

  @override
  void dispose() {
    _currentPasswordController.dispose();
    _newPasswordController.dispose();
    _confirmPasswordController.dispose();
    _resetCooldownTimer?.cancel();
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

  void _startResetCooldown([int seconds = 60]) {
    _resetCooldownTimer?.cancel();
    setState(() => _resetCooldownSeconds = seconds);
    _resetCooldownTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted) {
        timer.cancel();
        return;
      }
      setState(() {
        _resetCooldownSeconds--;
        if (_resetCooldownSeconds <= 0) timer.cancel();
      });
    });
  }

  /// Lets the user reset their password via Firebase's normal email-link
  /// flow when they can't remember their current password — without ever
  /// bypassing re-authentication for the Change Password action itself.
  /// Always uses the signed-in user's own account email; never a
  /// user-typed address.
  Future<void> _handleForgotPassword() async {
    FocusManager.instance.primaryFocus?.unfocus();
    final email = FirebaseAuth.instance.currentUser?.email;
    if (email == null || email.isEmpty) {
      _showMessage('Please sign in again before resetting your password.', true);
      return;
    }

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: const Color(0xFF1B0C2D),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Reset Password', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
        content: Text(
          "We'll send a password reset link to your registered email:\n$email",
          style: const TextStyle(color: Colors.white70, fontSize: 13),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, false),
            child: const Text('Cancel', style: TextStyle(color: Colors.white54)),
          ),
          TextButton(
            onPressed: () => Navigator.pop(dialogContext, true),
            child: const Text('Send', style: TextStyle(color: AppColors.primaryGold, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );

    if (confirmed != true || !mounted) return;

    setState(() => _isSendingReset = true);
    try {
      // Routed through the backend (rate-limited) instead of calling
      // Firebase directly from the client.
      await apiClient.post(
        '/auth/send-password-reset',
        data: FormData.fromMap({'identifier': email}),
      );
      if (!mounted) return;
      _startResetCooldown();
      await showDialog<void>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          backgroundColor: const Color(0xFF1B0C2D),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          title: const Text('Check Your Email', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
          content: Text(
            'Password reset link sent to your registered email:\n$email',
            style: const TextStyle(color: Colors.white70, fontSize: 13),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext),
              child: const Text('OK', style: TextStyle(color: AppColors.primaryGold, fontWeight: FontWeight.bold)),
            ),
          ],
        ),
      );
    } catch (_) {
      // Never surface raw FirebaseAuthException detail to the user.
      if (mounted) {
        _showMessage('Something went wrong sending the reset email. Please try again.', true);
      }
    } finally {
      if (mounted) setState(() => _isSendingReset = false);
    }
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
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton(
                    onPressed: (_isLoading || _isSendingReset || _resetCooldownSeconds > 0)
                        ? null
                        : _handleForgotPassword,
                    child: Text(
                      _resetCooldownSeconds > 0
                          ? 'Forgot Password? (${_resetCooldownSeconds}s)'
                          : 'Forgot Password?',
                      style: const TextStyle(
                        color: AppColors.primaryGold,
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 6),
                TextFormField(
                  controller: _newPasswordController,
                  obscureText: _obscureNewPassword,
                  enabled: !_isLoading,
                  onChanged: (_) => setState(() {}),
                  validator: _validateNewPassword,
                  style: const TextStyle(color: AppColors.textPrimary),
                  decoration: _decoration(
                    'New Password',
                    _obscureNewPassword,
                    () => setState(() => _obscureNewPassword = !_obscureNewPassword),
                  ),
                ),
                PasswordRequirementsChecklist(
                  password: _newPasswordController.text,
                ),
                const SizedBox(height: 22),
                TextFormField(
                  controller: _confirmPasswordController,
                  obscureText: _obscureConfirmPassword,
                  enabled: !_isLoading,
                  onChanged: (_) => setState(() {}),
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
                    onPressed: (_isLoading ||
                            !PasswordValidator.meetsPolicy(_newPasswordController.text) ||
                            _newPasswordController.text != _confirmPasswordController.text)
                        ? null
                        : _changePassword,
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