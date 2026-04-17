import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../../core/constants/app_assets.dart';
import '../widgets/glass_card.dart';
import '../widgets/network_background.dart';
import 'dart:io';
import 'package:image_picker/image_picker.dart';
import '../../providers/user_provider.dart';
import 'package:dio/dio.dart';
import 'companion_ready_screen.dart';
import 'welcome_screen.dart';
import 'login_screen.dart';
import 'mode_selection_screen.dart';
import '../../core/network/api_config.dart';
import '../widgets/epicverse_logo.dart';
import 'dart:convert';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'dart:math' as math;

class DashboardScreen extends ConsumerStatefulWidget {
  const DashboardScreen({super.key});

  @override
  ConsumerState<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends ConsumerState<DashboardScreen> with TickerProviderStateMixin {
  late AnimationController _entryController;
  late AnimationController _floatController;
  late AnimationController _glowController;
  
  late Animation<double> _scaleAnimation;
  late Animation<double> _fadeAnimation;
  late Animation<Offset> _slideAnimation;
  late Animation<double> _floatAnimation;
  late Animation<double> _glowAnimation;

  @override
  void initState() {
    super.initState();
    
    // 1. Entry Animation (Launch)
    _entryController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2000),
    );

    _scaleAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _entryController,
        curve: const Interval(0.3, 1.0, curve: Curves.elasticOut),
      ),
    );

    _fadeAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _entryController,
        curve: const Interval(0.0, 0.4, curve: Curves.easeIn),
      ),
    );

    _slideAnimation = Tween<Offset>(begin: const Offset(0, 0.2), end: Offset.zero).animate(
      CurvedAnimation(
        parent: _entryController,
        curve: const Interval(0.2, 0.8, curve: Curves.easeOutCubic),
      ),
    );

    // 2. Continuous Floating Animation
    _floatController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 4),
    )..repeat(reverse: true);

    _floatAnimation = Tween<double>(begin: -10, end: 10).animate(
      CurvedAnimation(
        parent: _floatController,
        curve: Curves.easeInOutSine,
      ),
    );

    // 3. Continuous Glow Pulse Animation
    _glowController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 3),
    )..repeat(reverse: true);

    _glowAnimation = Tween<double>(begin: 1.0, end: 1.4).animate(
      CurvedAnimation(
        parent: _glowController,
        curve: Curves.easeInOut,
      ),
    );

    _entryController.forward();
  }

  @override
  void dispose() {
    _entryController.dispose();
    _floatController.dispose();
    _glowController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final user = ref.watch(userProvider);
    final String displayName = user?.displayName ?? 'Explorer';
    debugPrint("Dashboard Build: User=${user?.id}, PhotoPresent=${user?.profilePicture != null}");

    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              // Animated AppBar with Staggered Fade & Slide
              FadeTransition(
                opacity: _fadeAnimation,
                child: SlideTransition(
                  position: _slideAnimation,
                  child: _buildAppBar(context, ref, displayName),
                ),
              ),
              
              Expanded(
                child: Center(
                  child: AnimatedBuilder(
                    animation: Listenable.merge([_floatAnimation, _glowAnimation]),
                    builder: (context, child) {
                      return Transform.translate(
                        offset: Offset(0, _floatAnimation.value),
                        child: ScaleTransition(
                          scale: _scaleAnimation,
                          child: FadeTransition(
                            opacity: _fadeAnimation,
                            child: GestureDetector(
                              onTap: () {
                                Navigator.of(context).push(
                                  MaterialPageRoute(builder: (_) => const ModeSelectionScreen()),
                                );
                              },
                              child: Container(
                                width: 220,
                                height: 220,
                                decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  gradient: const LinearGradient(
                                    colors: [Color(0xFF432571), Color(0xFF2C1349)],
                                    begin: Alignment.topLeft,
                                    end: Alignment.bottomRight,
                                  ),
                                  border: Border.all(
                                    color: AppColors.primaryGold.withOpacity(0.6 * _glowAnimation.value.clamp(0.0, 1.0)), 
                                    width: 3,
                                  ),
                                  boxShadow: [
                                    BoxShadow(
                                      color: Colors.black.withOpacity(0.7),
                                      blurRadius: 30,
                                      spreadRadius: 10,
                                      offset: const Offset(0, 15),
                                    ),
                                    BoxShadow(
                                      color: AppColors.primaryGold.withOpacity(0.25 * _glowAnimation.value),
                                      blurRadius: 50 * _glowAnimation.value,
                                      spreadRadius: 2,
                                    ),
                                  ],
                                ),
                                padding: const EdgeInsets.all(25),
                                child: Center(
                                  child: Image.asset(
                                    'assets/images/button_png.png',
                                    width: 170,
                                    fit: BoxFit.contain,
                                    errorBuilder: (context, error, stackTrace) {
                                      return const Icon(Icons.play_circle_fill, color: AppColors.primaryGold, size: 80);
                                    },
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ),
              
              const SizedBox(height: 60), 
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildAppBar(BuildContext context, WidgetRef ref, String name) {
    final user = ref.watch(userProvider);
    final picker = ImagePicker();

    return Padding(
      padding: const EdgeInsets.all(24.0),
      child: Row(
        children: [
          GestureDetector(
            onTap: () => _showImageSourceActionSheet(context, ref, picker),
            child: Hero(
              tag: 'user_avatar',
              child: Stack(
                children: [
                  Container(
                    width: 48,
                    height: 48,
                    decoration: BoxDecoration(
                      color: Colors.white.withOpacity(0.1),
                      shape: BoxShape.circle,
                      border: Border.all(color: Colors.white.withOpacity(0.4), width: 1.5),
                      image: user?.profilePicture != null 
                        ? DecorationImage(
                            image: MemoryImage(base64Decode(user!.profilePicture!)),
                            fit: BoxFit.cover,
                          ) 
                        : null,
                    ),
                    child: user?.profilePicture == null 
                      ? const Icon(Icons.person_outline_rounded, color: Colors.white, size: 26)
                      : null,
                  ),
                  Positioned(
                    right: 0,
                    bottom: 0,
                    child: Container(
                      padding: const EdgeInsets.all(3),
                      decoration: const BoxDecoration(
                        color: AppColors.primaryGold,
                        shape: BoxShape.circle,
                      ),
                      child: const Icon(Icons.camera_alt_rounded, size: 9, color: Colors.black),
                    ),
                  ),
                ],
              ),
            ),
          ),
          const SizedBox(width: 16),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Welcome back', style: TextStyle(color: AppColors.textMuted, fontSize: 11, letterSpacing: 0.5)),
              Text(name, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 19, color: AppColors.textPrimary)),
            ],
          ),
          const Spacer(),
          IconButton(
            onPressed: () => _showEditProfileDialog(context, ref),
            icon: const Icon(Icons.edit_note_rounded, color: AppColors.textPrimary),
            tooltip: 'Edit Profile',
          ),
          IconButton(
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
            icon: const Icon(AppIcons.exit, color: AppColors.textPrimary),
            tooltip: 'Logout',
          ),
        ],
      ),
    );
  }

  void _showEditProfileDialog(BuildContext context, WidgetRef ref) {
    final user = ref.read(userProvider);
    if (user == null) return;
    final controller = TextEditingController(text: user.displayName);
    
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: AppColors.surfaceElevated,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: const Text('Edit Name', style: TextStyle(color: AppColors.textPrimary)),
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
                // 1. Update UI and Firebase Auth (Primary state)
                final updatedUser = user.copyWith(displayName: newName);
                ref.read(userProvider.notifier).setUser(updatedUser);
                
                try {
                  // Synchronize name with Firebase if possible
                  await FirebaseAuth.instance.currentUser?.updateDisplayName(newName);

                  // 2. Synchronize with SQL Database (Persistent state)
                  final dio = Dio();
                  await dio.post(
                    '${ApiConfig.apiUrl}/sync-user',
                    data: {
                      "uid": updatedUser.id,
                      "display_name": updatedUser.displayName,
                      "email": updatedUser.email,
                      "primary_language": updatedUser.primaryLanguage,
                      "profile_picture": updatedUser.profilePicture,
                    },
                    options: Options(headers: ApiConfig.headers), // Critical for ngrok
                  );
                } catch (e) {
                  debugPrint("Error syncing profile updates: $e");
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

  void _showImageSourceActionSheet(BuildContext context, WidgetRef ref, ImagePicker picker) {
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
                  if (image != null) await _updateProfile(ref, image.path);
                },
              ),
              ListTile(
                leading: const Icon(Icons.photo_library_rounded, color: AppColors.primaryGold),
                title: const Text('Choose from Gallery', style: TextStyle(color: AppColors.textPrimary)),
                onTap: () async {
                  Navigator.pop(context);
                  final XFile? image = await picker.pickImage(source: ImageSource.gallery, imageQuality: 50);
                  if (image != null) await _updateProfile(ref, image.path);
                },
              ),
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _updateProfile(WidgetRef ref, String path) async {
    final user = ref.read(userProvider);
    if (user != null) {
      try {
        final bytes = await File(path).readAsBytes();
        final base64Image = base64Encode(bytes);
        final updatedUser = user.copyWith(profilePicture: base64Image);
        ref.read(userProvider.notifier).setUser(updatedUser);
        final dio = Dio();
        await dio.post(
          '${ApiConfig.apiUrl}/sync-user',
          data: {
            "uid": updatedUser.id,
            "display_name": updatedUser.displayName,
            "email": updatedUser.email,
            "primary_language": updatedUser.primaryLanguage,
            "profile_picture": updatedUser.profilePicture, 
          },
          options: Options(headers: ApiConfig.headers),
        );
      } catch (e) {
        debugPrint("Error updating profile image: $e");
      }
    }
  }
}
