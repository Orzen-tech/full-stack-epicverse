import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:flutter_jailbreak_detection/flutter_jailbreak_detection.dart';
import 'welcome_screen.dart';
import 'dashboard_screen.dart';
import 'otp_verification_screen.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:dio/dio.dart';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import '../../core/network/api_config.dart';
import '../../core/constants/app_colors.dart';

class SplashScreen extends ConsumerStatefulWidget {
  const SplashScreen({super.key});

  @override
  ConsumerState<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends ConsumerState<SplashScreen> with TickerProviderStateMixin {
  late AnimationController _controller;
  late AnimationController _spinController;
  late Animation<double> _logoOpacity;
  late Animation<double> _logoScale;
  late Animation<double> _bgScale;

  @override
  void initState() {
    super.initState();
    
    _controller = AnimationController(
      duration: const Duration(milliseconds: 2500),
      vsync: this,
    );

    _logoOpacity = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _controller,
        curve: Interval(0.1, 0.6, curve: Curves.easeIn),
      ),
    );

    _logoScale = Tween<double>(begin: 0.85, end: 1.0).animate(
      CurvedAnimation(
        parent: _controller,
        curve: Interval(0.1, 0.7, curve: Curves.easeOutBack),
      ),
    );

    _bgScale = Tween<double>(begin: 1.0, end: 1.15).animate(
      CurvedAnimation(
        parent: _controller,
        curve: Curves.easeOut,
      ),
    );

    // Continuous spin for loading symbol
    _spinController = AnimationController(
      duration: const Duration(seconds: 4),
      vsync: this,
    )..repeat();

    _controller.forward();
    _checkRootAndProceed();
  }

  @override
  void dispose() {
    _controller.dispose();
    _spinController.dispose();
    super.dispose();
  }

  /// Checks if the device is rooted/jailbroken before proceeding.
  /// If rooted, shows a non-dismissible security warning dialog.
  Future<void> _checkRootAndProceed() async {
    bool isRooted = false;
    try {
      isRooted = await FlutterJailbreakDetection.jailbroken;
    } catch (_) {
      // If detection fails, fail safe — assume not rooted
      isRooted = false;
    }

    if (!mounted) return;

    if (isRooted) {
      showDialog(
        context: context,
        barrierDismissible: false,
        builder: (context) => PopScope(
          canPop: false,
          child: AlertDialog(
            backgroundColor: const Color(0xFF1B0C2D),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: Color(0xFFFF4C4C), width: 1.5),
            ),
            title: const Row(
              children: [
                Icon(Icons.security, color: Color(0xFFFF4C4C), size: 24),
                SizedBox(width: 10),
                Text(
                  'Security Alert',
                  style: TextStyle(
                    color: Color(0xFFFF4C4C),
                    fontWeight: FontWeight.bold,
                    fontSize: 18,
                  ),
                ),
              ],
            ),
            content: const Text(
              'This device appears to be rooted or jailbroken.\n\n'
              'EpicVerse cannot run on rooted or jailbroken devices to protect '
              'your account security and sensitive data.',
              style: TextStyle(color: Colors.white70, fontSize: 14, height: 1.5),
            ),
          ),
        ),
      );
      return; // Stop execution — do not proceed to auth check
    }

    // Device is safe — proceed normally
    _checkAuth();
  }

  Future<void> _checkAuth() async {
    // 1. Show Splash Image (Logo) for 3 seconds (allow animation to fully breathe)
    await Future.delayed(const Duration(milliseconds: 3000));

    // 2. Check Auth State and Fetch Real Identitiy
    final User? firebaseUser = FirebaseAuth.instance.currentUser;
    final prefs = await SharedPreferences.getInstance();
    final bool isLoggedInLocally = prefs.getBool('isLoggedIn') ?? false;

    if (!mounted) return;

    if (firebaseUser != null || isLoggedInLocally) {
      // 3. Re-Hydrate User Profile from Backend SQL before navigating
      if (firebaseUser != null) {
        try {
          final dio = Dio();
          final response = await dio.get(
            '${ApiConfig.apiUrl}/user/${firebaseUser.uid}',
            options: Options(headers: ApiConfig.headers),
          );
          
          if (response.statusCode == 200) {
            final data = response.data;
            final user = UserModel.fromJson(data);
            ref.read(userProvider.notifier).setUser(user);

            final emailVerified = data['email_verified'] ?? false;
            if (!emailVerified) {
              debugPrint('[EpicVerse][SPLASH] email_verified=false → OTP screen');
              if (!mounted) return;
              final navigator = Navigator.of(context);
              navigator.pushReplacement(
                PageRouteBuilder(
                  transitionDuration: const Duration(milliseconds: 800),
                  pageBuilder: (ctx, a, b) => OtpVerificationScreen(
                    email: firebaseUser.email ?? '',
                    onVerified: () {
                      navigator.pushAndRemoveUntil(
                        MaterialPageRoute(builder: (_) => const DashboardScreen()),
                        (route) => false,
                      );
                    },
                  ),
                  transitionsBuilder: (_, animation, __, child) =>
                      FadeTransition(opacity: animation, child: child),
                ),
              );
              return;
            }
          }
        } on DioException catch (e) {
          if (e.response?.statusCode == 404) {
            // Profile genuinely doesn't exist — signup was never completed.
            // Do NOT let this into the Dashboard; send them back through
            // the normal Login flow, which already knows how to resume
            // an incomplete signup (OTP → Create Profile).
            debugPrint('[EpicVerse][SPLASH] Profile not found (404) → incomplete signup, sign out');
            await prefs.setBool('isLoggedIn', false);
            if (!mounted) return;
            Navigator.of(context).pushReplacement(
              PageRouteBuilder(
                transitionDuration: const Duration(milliseconds: 800),
                pageBuilder: (context, animation, secondaryAnimation) => const WelcomeScreen(),
                transitionsBuilder: (context, animation, secondaryAnimation, child) {
                  return FadeTransition(opacity: animation, child: child);
                },
              ),
            );
            return;
          }
          debugPrint("Splash: Failed to fetch profile (non-404): $e");
          // Transient network/server error — don't punish an existing,
          // already-verified user for a momentary backend hiccup.
          ref.read(userProvider.notifier).setUser(UserModel(
            id: firebaseUser.uid,
            displayName: firebaseUser.displayName ?? "Explorer",
            email: firebaseUser.email ?? "",
            primaryLanguage: 'English',
            preferredLanguages: const ['English'],
          ));
        } catch (e) {
          debugPrint("Splash: Failed to fetch profile (unexpected): $e");
          ref.read(userProvider.notifier).setUser(UserModel(
            id: firebaseUser.uid,
            displayName: firebaseUser.displayName ?? "Explorer",
            email: firebaseUser.email ?? "",
            primaryLanguage: 'English',
            preferredLanguages: const ['English'],
          ));
        }
      }
      
      if (!mounted) return;

      Navigator.of(context).pushReplacement(
        PageRouteBuilder(
          transitionDuration: const Duration(milliseconds: 800),
          pageBuilder: (context, animation, secondaryAnimation) => const DashboardScreen(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return FadeTransition(opacity: animation, child: child);
          },
        ),
      );
    } else {
      Navigator.of(context).pushReplacement(
        PageRouteBuilder(
          transitionDuration: const Duration(milliseconds: 800),
          pageBuilder: (context, animation, secondaryAnimation) => const WelcomeScreen(),
          transitionsBuilder: (context, animation, secondaryAnimation, child) {
            return FadeTransition(opacity: animation, child: child);
          },
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: const Color(0xFF1B0C2D), // Deep EpicVerse Purple
      body: Stack(
        children: [
          // Background Glow Animation
          Positioned.fill(
            child: ScaleTransition(
              scale: _bgScale,
              child: Container(
                decoration: const BoxDecoration(
                  gradient: RadialGradient(
                    colors: [
                      Color(0xFF321650),
                      Color(0xFF1B0C2D),
                    ],
                    radius: 1.2,
                  ),
                ),
              ),
            ),
          ),
          Center(
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                FadeTransition(
                  opacity: _logoOpacity,
                  child: ScaleTransition(
                    scale: _logoScale,
                    child: Hero(
                      tag: 'app_logo',
                      child: Image.asset(
                        'assets/images/epicverse_full_logo.webp',
                        width: 380, // Optimized for horizontal widescreen logo
                        fit: BoxFit.contain,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 50),
                FadeTransition(
                  opacity: _logoOpacity,
                  child: RotationTransition(
                    turns: _spinController,
                    child: Image.asset(
                      'assets/images/loading_image.webp',
                      width: 64,
                      height: 64,
                      fit: BoxFit.contain,
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
