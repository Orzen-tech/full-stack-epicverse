import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'welcome_screen.dart';
import 'dashboard_screen.dart';
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

    _controller.forward();
    _checkAuth();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
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
            final user = UserModel(
              id: firebaseUser.uid,
              displayName: data['display_name'] ?? firebaseUser.displayName ?? "Explorer",
              email: data['email'] ?? firebaseUser.email ?? "",
              primaryLanguage: data['primary_language'] ?? 'English',
              preferredLanguages: [data['primary_language'] ?? 'English'],
              profilePicture: data['profile_picture'],
            );
            ref.read(userProvider.notifier).setUser(user);
          }
        } catch (e) {
          debugPrint("Splash: Failed to fetch profile: $e");
          // Fallback to basic firebase info if DB is offline (ensure all required fields are present)
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
                        'assets/images/epicverse_full_logo.png',
                        width: 380, // Optimized for horizontal widescreen logo
                        fit: BoxFit.contain,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 50),
                FadeTransition(
                  opacity: _logoOpacity,
                  child: const CircularProgressIndicator(
                    color: Color(0xFFC5A358), // Gold
                    strokeWidth: 2,
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
