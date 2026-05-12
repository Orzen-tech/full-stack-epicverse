import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'core/theme/app_theme.dart';
import 'core/network/websocket_service.dart';
import 'presentation/screens/splash_screen.dart';
import 'presentation/screens/welcome_screen.dart';

final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();

  try {
    await Firebase.initializeApp();
    print("✅ [FIREBASE] Successfully initialized!");
  } catch (e) {
    print("❌ [FIREBASE-ERROR] Failed to initialize: $e");
  }

  try {
    webSocketService.connect();
  } catch (e) {
    print("❌ [SOCKET-ERROR] $e");
  }

  runApp(
    const ProviderScope(
      child: EpicVerseApp(),
    ),
  );
}

class EpicVerseApp extends StatefulWidget {
  const EpicVerseApp({super.key});

  @override
  State<EpicVerseApp> createState() => _EpicVerseAppState();
}

class _EpicVerseAppState extends State<EpicVerseApp> {
  StreamSubscription<void>? _kickedSub;

  @override
  void initState() {
    super.initState();
    _kickedSub = webSocketService.sessionKicked.listen((_) async {
      debugPrint('[EpicVerse][APP] Session kicked — showing popup then signing out');
      final ctx = navigatorKey.currentContext;
      if (ctx != null) {
        // ignore: use_build_context_synchronously
        await showDialog<void>(
          context: ctx,
          barrierDismissible: false,
          useRootNavigator: true,
          builder: (dCtx) => AlertDialog(
            backgroundColor: const Color(0xFF1B0C2D),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            title: const Text('Signed Out', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
            content: const Text(
              'Your account was signed in on another device. You have been logged out.',
              style: TextStyle(color: Colors.white70),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dCtx).pop(),
                child: const Text('OK', style: TextStyle(color: Color(0xFFD4AF37), fontWeight: FontWeight.bold)),
              ),
            ],
          ),
        );
      }
      await FirebaseAuth.instance.signOut();
      final prefs = await SharedPreferences.getInstance();
      await prefs.clear();
      navigatorKey.currentState?.pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const WelcomeScreen()),
        (_) => false,
      );
    });
  }

  @override
  void dispose() {
    _kickedSub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'EpicVerse',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.epicTheme,
      navigatorKey: navigatorKey,
      home: const SplashScreen(),
    );
  }
}
