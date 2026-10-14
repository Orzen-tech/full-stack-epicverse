import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import '../../providers/user_provider.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/network/api_client.dart';
import '../../core/network/mfa_session_manager.dart';
import '../../core/errors/app_exception.dart';
import '../../core/errors/error_mapper.dart';
import '../../core/security/mfa_flow.dart';
import '../../core/security/mfa_enable_flow.dart';
import '../../core/security/password_reauth.dart';
import 'login_screen.dart';
import 'welcome_screen.dart';
import 'legal_content_screen.dart';
import 'faq_screen.dart';
import 'feedback_screen.dart';
import 'change_password_screen.dart';
import '../../core/errors/error_handler.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  @override
  Widget build(BuildContext context) {
    final user = ref.watch(userProvider);

    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              // Header
              Padding(
                padding: const EdgeInsets.all(24.0),
                child: Row(
                  children: [
                    IconButton(
                      onPressed: () => Navigator.pop(context),
                      icon: const Icon(Icons.arrow_back, color: AppColors.textPrimary),
                    ),
                    const SizedBox(width: 16),
                    const Text(
                      'Settings',
                      style: TextStyle(
                        color: AppColors.textPrimary,
                        fontSize: 24,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ],
                ),
              ),

              // Profile Card
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Container(
                  padding: const EdgeInsets.all(20),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.05),
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: Colors.white.withOpacity(0.1)),
                  ),
                  child: Row(
                    children: [
                      // Avatar with camera badge
                      GestureDetector(
                        onTap: () => _showImageSourceActionSheet(context),
                        child: Stack(
                          children: [
                            Container(
                              width: 64,
                              height: 64,
                              decoration: BoxDecoration(
                                color: Colors.white.withOpacity(0.1),
                                shape: BoxShape.circle,
                                border: Border.all(color: AppColors.primaryGold, width: 2),
                                image: user?.profilePicture != null
                                  ? DecorationImage(
                                      image: MemoryImage(base64Decode(user!.profilePicture!)),
                                      fit: BoxFit.cover,
                                    )
                                  : null,
                              ),
                              child: user?.profilePicture == null
                                ? const Icon(Icons.person_outline_rounded, color: Colors.white, size: 32)
                                : null,
                            ),
                            Positioned(
                              right: 0,
                              bottom: 0,
                              child: Container(
                                padding: const EdgeInsets.all(4),
                                decoration: const BoxDecoration(
                                  color: AppColors.primaryGold,
                                  shape: BoxShape.circle,
                                ),
                                child: const Icon(Icons.camera_alt_rounded, size: 12, color: Colors.black),
                              ),
                            ),
                          ],
                        ),
                      ),
                      const SizedBox(width: 16),
                      // Name + edit
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              user?.displayName ?? 'User',
                              style: const TextStyle(
                                color: AppColors.textPrimary,
                                fontSize: 18,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                            const SizedBox(height: 2),
                            Text(
                              user?.email ?? '',
                              style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        onPressed: () => _showChangeNameDialog(context, user),
                        icon: const Icon(Icons.edit_outlined, color: AppColors.primaryGold, size: 20),
                        tooltip: 'Edit name',
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 24),

              // Settings Options
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  children: [
                    _buildSettingsOption(
                      icon: Icons.help_outline,
                      title: 'FAQ',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => const FAQScreen()),
                      ),
                    ),
                    const SizedBox(height: 16),
                    _buildSettingsOption(
                      icon: Icons.feedback_outlined,
                      title: 'Send Feedback',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => const FeedbackScreen()),
                      ),
                    ),
                    const SizedBox(height: 24),
                    const Divider(color: Colors.white24),
                    const SizedBox(height: 24),
                    _buildMfaToggle(context, user),
                    const SizedBox(height: 24),
                    _buildSettingsOption(
                      icon: Icons.lock_outline,
                      title: 'Change Password',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => const ChangePasswordScreen()),
                      ),
                    ),
                    const SizedBox(height: 24),
                    const Divider(color: Colors.white24),
                    const SizedBox(height: 24),
                    _buildSettingsOption(
                      icon: Icons.privacy_tip_outlined,
                      title: 'Privacy Policy',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => const LegalContentScreen(
                            title: 'Privacy Policy',
                            endpoint: '/legal/privacy',
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    _buildSettingsOption(
                      icon: Icons.smart_toy_outlined,
                      title: 'AI Usage Disclosure',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => const LegalContentScreen(
                            title: 'AI Usage Disclosure',
                            endpoint: '/legal/ai-disclosure',
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 16),
                    _buildSettingsOption(
                      icon: Icons.description_outlined,
                      title: 'Terms of Service',
                      onTap: () => Navigator.push(
                        context,
                        MaterialPageRoute(
                          builder: (_) => const LegalContentScreen(
                            title: 'Terms of Service',
                            endpoint: '/legal/terms',
                          ),
                        ),
                      ),
                    ),
                    const SizedBox(height: 24),
                    const Divider(color: Colors.white24),
                    const SizedBox(height: 24),
                    _buildSettingsOption(
                      icon: Icons.logout_outlined,
                      title: 'Logout',
                      onTap: () => _showLogoutDialog(context),
                    ),
                    const SizedBox(height: 16),
                    _buildSettingsOption(
                      icon: Icons.delete_outline,
                      title: 'Delete Account',
                      onTap: () => _showDeleteAccountDialog(context, user),
                      isDestructive: true,
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildMfaToggle(BuildContext context, user) {
    final isEnabled = user?.mfaEnabled ?? false;
    final mfaState = MfaSettingsState(_mfaCapability);
    final toggle = Container(
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.05),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withOpacity(0.1)),
      ),
      child: SwitchListTile(
        secondary: Icon(Icons.verified_user_outlined, color: AppColors.primaryGold),
        title: const Text(
          'Two-Factor Authentication',
          style: TextStyle(color: AppColors.textPrimary, fontSize: 16),
        ),
        subtitle: Text(
          mfaState.message ?? (isEnabled ? 'Email OTP required on login' : 'Tap to enable extra security'),
          style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
        ),
        value: isEnabled,
        activeThumbColor: AppColors.primaryGold,
        // Enabled only once the modern backend is confirmed. Never falls back to the legacy /user/update-mfa.
        onChanged: (_mfaBusy || !mfaState.toggleEnabled) ? null : (value) => _updateMfa(value),
      ),
    );
    if (!mfaState.canRetry) return toggle;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        toggle,
        TextButton(
          onPressed: _mfaBusy ? null : _retryMfaCheck,
          child: const Text('Retry'),
        ),
      ],
    );
  }

  @override
  void initState() {
    super.initState();
    _refreshMfaState();
  }

  bool _mfaBusy = false;
  MfaSettingsCapability _mfaCapability = MfaSettingsCapability.unknown;

  /// The toggle always reflects the server's state, never a local guess. It also records whether the backend
  /// supports the modern MFA endpoints (null status = it predates F-09) or the check itself failed.
  Future<void> _refreshMfaState() async {
    var capability = MfaSettingsCapability.unavailable;
    try {
      final status = await MfaFlow.fetchSessionStatus();
      capability = MfaSettingsState.capabilityFor(status);
      final user = ref.read(userProvider);
      if (status != null && user != null && mounted) {
        ref.read(userProvider.notifier).setUser(user.copyWith(mfaEnabled: status.mfaEnabled));
      }
    } catch (_) {}
    if (mounted) setState(() => _mfaCapability = capability);
  }

  Future<void> _retryMfaCheck() async {
    setState(() => _mfaCapability = MfaSettingsCapability.unknown);
    await _refreshMfaState();
  }

  Future<void> _updateMfa(bool enable) async {
    if (_mfaBusy || _mfaCapability != MfaSettingsCapability.modern) return;
    setState(() => _mfaBusy = true);
    try {
      if (enable) {
        // Explicit Firebase password re-authentication on EVERY attempt (the server also enforces a recent sign-in). If
        // the sign-in goes stale while the code is being entered, the user is asked for the password again once.
        final result = await MfaEnableFlow.run(
          nav: Navigator.of(context),
          reauthenticate: _confirmPassword,
          notify: (m) => _showMfaSnack(m, error: true),
        );
        if (result == MfaEnableResult.enabled) _showMfaSnack('Two-factor authentication is now on.');
      } else {
        await _disableMfa();
      }
    } finally {
      await _refreshMfaState();
      if (mounted) setState(() => _mfaBusy = false);
    }
  }

  Future<void> _disableMfa() async {
    if (!await _confirmPassword()) return; // explicit password every time
    try {
      await apiClient.post('/user/mfa/disable');
    } on AppException catch (e) {
      if (e.type == AppExceptionType.recentSignInRequired && await _confirmPassword()) {
        try {
          await apiClient.post('/user/mfa/disable');
        } on AppException catch (e2) {
          _showMfaSnack(_disableError(e2), error: true);
          return;
        }
      } else {
        _showMfaSnack(_disableError(e), error: true);
        return;
      }
    }
    await MfaSessionManager.clearAll();
    _showMfaSnack('Two-factor authentication is now off.');
  }

  String _disableError(AppException e) {
    switch (e.type) {
      case AppExceptionType.mfaSessionRequired:
        return 'Verification is required to turn off two-factor authentication.';
      case AppExceptionType.recentSignInRequired:
        return 'Please sign in again to continue.';
      case AppExceptionType.network:
      case AppExceptionType.timeout:
        return 'No internet connection. Please try again.';
      default:
        return 'Could not turn off two-factor authentication. Please try again.';
    }
  }

  /// Always asks for the password and re-authenticates with Firebase (no "signed in recently" shortcut), so the
  /// sign-in time the server checks is fresh. The password goes only to Firebase. Returns false if the user cancels
  /// or the password is wrong (the reason is shown).
  Future<bool> _confirmPassword() async {
    final reauth = PasswordReauth.current;
    if (!reauth.canReauthenticate) return false;
    final password = await _promptPassword();
    if (password == null || password.isEmpty) return false;
    try {
      await reauth.reauthenticate(password);
      return true;
    } catch (e) {
      _showMfaSnack(ErrorMapper.fromException(e).userMessage, error: true);
      return false;
    }
  }

  Future<String?> _promptPassword() =>
      showDialog<String>(context: context, builder: (_) => const _PasswordPromptDialog());

  void _showMfaSnack(String message, {bool error = false}) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(message),
      backgroundColor: error ? Colors.red.shade700 : null,
    ));
  }

  Widget _buildSettingsOption({
    required IconData icon,
    required String title,
    required VoidCallback onTap,
    bool isDestructive = false,
  }) {
    return Container(
      decoration: BoxDecoration(
        color: Colors.white.withOpacity(0.05),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withOpacity(0.1)),
      ),
      child: ListTile(
        leading: Icon(icon, color: isDestructive ? Colors.red : AppColors.primaryGold),
        title: Text(
          title,
          style: TextStyle(
            color: isDestructive ? Colors.red : AppColors.textPrimary,
            fontSize: 16,
          ),
        ),
        trailing: Icon(Icons.chevron_right, color: AppColors.textMuted),
        onTap: onTap,
      ),
    );
  }

  void _showAIUsageDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: AppColors.surfaceElevated,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Text('AI Usage Disclosure', style: TextStyle(color: AppColors.primaryGold)),
        content: const Text(
          'EpicVerse uses OpenAI\'s Realtime API to generate responses for your companion.\n\n'
          '• Data Shared: Voice recordings and message transcripts are sent to OpenAI.\n'
          '• Purpose: To provide real-time, context-aware conversations.\n'
          '• Privacy: Data is processed according to OpenAI\'s privacy policies.',
          style: TextStyle(color: AppColors.textSecondary),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close', style: TextStyle(color: AppColors.primaryGold)),
          ),
        ],
      ),
    );
  }

  void _showChangeNameDialog(BuildContext context, user) {
    if (user == null) return;
    final controller = TextEditingController(text: user.displayName);

    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: AppColors.surfaceElevated,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Text('Change Name', style: TextStyle(color: AppColors.textPrimary)),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: const TextStyle(color: AppColors.textPrimary),
          decoration: const InputDecoration(
            hintText: 'Enter your name',
            hintStyle: TextStyle(color: AppColors.textMuted),
            enabledBorder: UnderlineInputBorder(borderSide: BorderSide(color: AppColors.primaryGold)),
            focusedBorder: UnderlineInputBorder(borderSide: BorderSide(color: AppColors.accentGold)),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel', style: TextStyle(color: AppColors.textSecondary)),
          ),
          TextButton(
            onPressed: () async {
              final newName = controller.text.trim();
              if (newName.isNotEmpty) {
                final updatedUser = user.copyWith(displayName: newName);
                ref.read(userProvider.notifier).setUser(updatedUser);

                try {
                  await FirebaseAuth.instance.currentUser?.updateDisplayName(newName);
                  await apiClient.post(
                    '/sync-user',
                    data: {
                      "uid": updatedUser.id,
                      "display_name": updatedUser.displayName,
                      "primary_language": updatedUser.primaryLanguage,
                      "profile_picture": updatedUser.profilePicture,
                    },
                  );
                } catch (e) {
                  debugPrint("Error updating name: $e");
                }
              }
              if (mounted) Navigator.pop(context);
            },
            child: const Text('Save', style: TextStyle(color: AppColors.primaryGold, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  void _showImageSourceActionSheet(BuildContext context) {
    final picker = ImagePicker();
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surfaceElevated,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(25))),
      builder: (context) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                leading: const Icon(Icons.camera_alt_rounded, color: AppColors.primaryGold),
                title: const Text('Take Photo', style: TextStyle(color: AppColors.textPrimary)),
                onTap: () async {
                  Navigator.pop(context);
                  try {
                    final XFile? image = await picker.pickImage(source: ImageSource.camera, imageQuality: 50);
                    if (image != null) await _updateProfilePhoto(image.path);
                  } on PlatformException catch (e) {
                    final mapped = ErrorMapper.fromException(e);
                    if (mounted) {
                      ErrorHandler.showWarningSnackBar(mapped.userMessage, context: context);
                    }
                  } catch (e) {
                    if (mounted) {
                      ErrorHandler.showWarningSnackBar('Unable to open camera right now. Please try again.', context: context);
                    }
                  }
                },
              ),
              ListTile(
                leading: const Icon(Icons.photo_library_rounded, color: AppColors.primaryGold),
                title: const Text('Choose from Gallery', style: TextStyle(color: AppColors.textPrimary)),
                onTap: () async {
                  Navigator.pop(context);
                  final XFile? image = await picker.pickImage(source: ImageSource.gallery, imageQuality: 50);
                  if (image != null) await _updateProfilePhoto(image.path);
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _updateProfilePhoto(String path) async {
    final user = ref.read(userProvider);
    if (user == null) return;
    try {
      final bytes = await File(path).readAsBytes();
      final base64Image = base64Encode(bytes);
      final updatedUser = user.copyWith(profilePicture: base64Image);
      ref.read(userProvider.notifier).setUser(updatedUser);
      await apiClient.post(
        '/sync-user',
        data: {
          "uid": updatedUser.id,
          "display_name": updatedUser.displayName,
          "primary_language": updatedUser.primaryLanguage,
          "profile_picture": updatedUser.profilePicture,
        },
      );
    } catch (e) {
      debugPrint("Error updating profile photo: $e");
    }
  }

  void _showLogoutDialog(BuildContext context) {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: AppColors.surfaceElevated,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Text('Logout', style: TextStyle(color: AppColors.textPrimary)),
        content: const Text('Are you sure you want to logout?', style: TextStyle(color: AppColors.textSecondary)),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel', style: TextStyle(color: AppColors.textSecondary)),
          ),
          TextButton(
            onPressed: () async {
              final prefs = await SharedPreferences.getInstance();
              await prefs.remove('isLoggedIn');
              await MfaFlow.signOut();
              if (!context.mounted) return;
              Navigator.of(context).pushAndRemoveUntil(
                MaterialPageRoute(builder: (_) => const LoginScreen()),
                (route) => false,
              );
            },
            child: const Text('Logout', style: TextStyle(color: Colors.red, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  void _showDeleteAccountDialog(BuildContext context, user) {
    if (user == null) return;

    showDialog(
      context: context,
      builder: (dialogContext) => AlertDialog(
        backgroundColor: AppColors.surfaceElevated,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Text('Delete Account', style: TextStyle(color: Colors.red)),
        content: const Text(
          'Your account will be scheduled for deletion in 30 days.\n\n'
          'If you sign back in within 30 days, the deletion will be automatically cancelled '
          'and your account will be fully restored.\n\n'
          'After 30 days, your account and all data will be permanently deleted.',
          style: TextStyle(color: AppColors.textSecondary),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: const Text('Cancel', style: TextStyle(color: AppColors.textSecondary)),
          ),
          TextButton(
            onPressed: () async {
              // Capture navigator before any awaits / pops so later navigation
              // survives this screen's disposal.
              final rootNavigator = Navigator.of(context, rootNavigator: true);
              Navigator.pop(dialogContext); // close the confirm dialog first
              try {
                // Soft-delete on backend: marks deletion_requested_at = NOW().
                // Must be authenticated so backend can verify caller uid matches.
                await apiClient.delete('/user/${user.id}');

                // NOTE: We intentionally DO NOT delete the Firebase user here.
                // Keeping the Firebase account alive is what lets the user
                // sign back in during the 30-day grace period and trigger
                // the server-side auto-cancel.
                await MfaFlow.signOut();

                // Clear local state
                final prefs = await SharedPreferences.getInstance();
                await prefs.clear();
                ref.read(userProvider.notifier).logout();

                rootNavigator.pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const WelcomeScreen()),
                  (route) => false,
                );
              } catch (e, st) {
                if (mounted) {
                  ErrorHandler.handleError(
                    e,
                    stackTrace: st,
                    context: context,
                    screenName: 'SettingsScreen',
                  );
                }
              }
            },
            child: const Text('Delete', style: TextStyle(color: Colors.red, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

}


/// The password prompt owns its text controller, so the controller is disposed only after the dialog has fully
/// closed (disposing it right after showDialog returns used it while the closing animation was still running).
class _PasswordPromptDialog extends StatefulWidget {
  const _PasswordPromptDialog();

  @override
  State<_PasswordPromptDialog> createState() => _PasswordPromptDialogState();
}

class _PasswordPromptDialogState extends State<_PasswordPromptDialog> {
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AlertDialog(
      backgroundColor: AppColors.surfaceElevated,
      title: const Text('Confirm your password', style: TextStyle(color: AppColors.textPrimary)),
      content: TextField(
        controller: _controller,
        obscureText: true,
        autofocus: true,
        style: const TextStyle(color: AppColors.textPrimary),
        decoration: const InputDecoration(hintText: 'Password'),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
        TextButton(onPressed: () => Navigator.pop(context, _controller.text), child: const Text('Continue')),
      ],
    );
  }
}
