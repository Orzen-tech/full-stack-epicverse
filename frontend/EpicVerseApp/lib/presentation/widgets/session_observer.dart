import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:firebase_auth/firebase_auth.dart';
import '../core/network/websocket_service.dart';
import '../providers/user_provider.dart';
import '../screens/welcome_screen.dart';

class SessionObserver extends ConsumerStatefulWidget {
  final Widget child;
  const SessionObserver({super.key, required this.child});

  @override
  ConsumerState<SessionObserver> createState() => _SessionObserverState();
}

class _SessionObserverState extends ConsumerState<SessionObserver> {
  StreamSubscription<String>? _logoutSubscription;

  @override
  void initState() {
    super.initState();
    // Listen for the concurrent logout signal from the WebSocket
    _logoutSubscription = webSocketService.concurrentLogoutStream.listen((message) {
      _handleConcurrentLogout(message);
    });
  }

  @override
  void dispose() {
    _logoutSubscription?.cancel();
    super.dispose();
  }

  void _handleConcurrentLogout(String message) {
    if (!mounted) return;

    // Show persistent dialog
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => AlertDialog(
        backgroundColor: const Color(0xFF1A1A2E),
        title: const Row(
          children: [
            Icon(Icons.warning_amber_rounded, color: Colors.amber),
            SizedBox(width: 10),
            Text('Session Conflict', style: TextStyle(color: Colors.white)),
          ],
        ),
        content: Text(
          message,
          style: const TextStyle(color: Colors.white70),
        ),
        actions: [
          TextButton(
            onPressed: () async {
              // 1. Clear Local State
              ref.read(userProvider.notifier).logout();
              
              // 2. Disconnect WebSocket
              webSocketService.disconnect();
              
              // 3. Sign out of Firebase
              await FirebaseAuth.instance.signOut();
              
              if (!mounted) return;
              
              // 4. Force Navigate to Welcome/Login
              Navigator.of(context).pushAndRemoveUntil(
                MaterialPageRoute(builder: (_) => const WelcomeScreen()),
                (route) => false,
              );
            },
            child: const Text('OK', style: TextStyle(color: Colors.amber, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return widget.child;
  }
}
