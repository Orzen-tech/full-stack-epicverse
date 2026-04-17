import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'core/theme/app_theme.dart';
import 'presentation/screens/welcome_screen.dart';
import 'presentation/screens/dashboard_screen.dart';
import 'core/network/websocket_service.dart';

import 'presentation/screens/splash_screen.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  
  // Initialize Firebase with Error Details
  try {
    await Firebase.initializeApp();
    print("✅ [FIREBASE] Successfully initialized!");
  } catch (e) {
    print("❌ [FIREBASE-ERROR] Failed to initialize: $e");
    print("💡 [TIP] For iOS, ensure GoogleService-Info.plist is added to Xcode and 'Target Membership' is checked.");
  }
  
  // Start server connection
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

class EpicVerseApp extends StatelessWidget {
  const EpicVerseApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'EpicVerse',
      debugShowCheckedModeBanner: false,
      theme: AppTheme.epicTheme,
      home: const SplashScreen(),
    );
  }
}
