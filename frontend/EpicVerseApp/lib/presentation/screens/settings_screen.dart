import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import '../../providers/user_provider.dart';
import 'package:dio/dio.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/network/api_config.dart';
import 'login_screen.dart';
import 'welcome_screen.dart';
import 'legal_content_screen.dart';
import 'faq_screen.dart';
import 'feedback_screen.dart';

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen> {
  final Dio _dio = Dio();

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
                  await _dio.post(
                    '${ApiConfig.apiUrl}/sync-user',
                    data: {
                      "uid": updatedUser.id,
                      "display_name": updatedUser.displayName,
                      "email": updatedUser.email,
                      "primary_language": updatedUser.primaryLanguage,
                      "profile_picture": updatedUser.profilePicture,
                    },
                    options: Options(headers: await ApiConfig.authHeaders()),
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
                  final XFile? image = await picker.pickImage(source: ImageSource.camera, imageQuality: 50);
                  if (image != null) await _updateProfilePhoto(image.path);
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
      await _dio.post(
        '${ApiConfig.apiUrl}/sync-user',
        data: {
          "uid": updatedUser.id,
          "display_name": updatedUser.displayName,
          "email": updatedUser.email,
          "primary_language": updatedUser.primaryLanguage,
          "profile_picture": updatedUser.profilePicture,
        },
        options: Options(headers: await ApiConfig.authHeaders()),
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
              await FirebaseAuth.instance.signOut();
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
                final idToken = await FirebaseAuth.instance.currentUser?.getIdToken();
                await _dio.delete(
                  '${ApiConfig.apiUrl}/user/${user.id}',
                  options: Options(headers: {
                    ...ApiConfig.headers,
                    if (idToken != null) 'Authorization': 'Bearer $idToken',
                  }),
                );

                // NOTE: We intentionally DO NOT delete the Firebase user here.
                // Keeping the Firebase account alive is what lets the user
                // sign back in during the 30-day grace period and trigger
                // the server-side auto-cancel.
                await FirebaseAuth.instance.signOut();

                // Clear local state
                final prefs = await SharedPreferences.getInstance();
                await prefs.clear();
                ref.read(userProvider.notifier).logout();

                rootNavigator.pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const WelcomeScreen()),
                  (route) => false,
                );
              } catch (e) {
                if (mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      content: Text('Failed to schedule deletion: $e'),
                      backgroundColor: Colors.red,
                    ),
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
