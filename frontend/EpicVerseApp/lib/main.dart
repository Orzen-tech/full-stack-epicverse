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
  
  // Initialize Firebase
  await Firebase.initializeApp();
  
  // Start server connection immediately on app startup
  webSocketService.connect();

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
