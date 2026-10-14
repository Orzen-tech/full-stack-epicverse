import 'dart:async';

import 'package:flutter/material.dart';

import '../../core/constants/app_colors.dart';
import '../../core/security/mfa_flow.dart';
import '../widgets/network_background.dart';
import 'welcome_screen.dart';

/// Shown when an MFA user's verification is still required but could not be completed right now because of a
/// TEMPORARY problem (no network, server error, or too many code requests). The Firebase sign-in is kept; the user
/// can retry, or sign out explicitly. Nothing is shown behind this screen until verification succeeds.
class MfaRequiredGate extends StatefulWidget {
  const MfaRequiredGate({
    super.key,
    required this.rateLimited,
    required this.retryBuilder,
    this.retryAfterSeconds,
  });

  final bool rateLimited;
  final int? retryAfterSeconds;

  /// Builds the screen that re-runs the startup checks (the splash screen).
  final Widget Function() retryBuilder;

  static int cooldownFor(bool rateLimited, int? retryAfterSeconds) {
    if (!rateLimited) return 0;
    final s = retryAfterSeconds ?? 60;
    return s.clamp(5, 600);
  }

  static String messageFor(bool rateLimited, int cooldown) {
    if (!rateLimited) return "We couldn't reach the server to check your verification. Check your connection and try again.";
    final minutes = (cooldown / 60).ceil().clamp(1, 10);
    return 'Too many code requests. Please wait about $minutes minute${minutes == 1 ? '' : 's'} and try again.';
  }

  @override
  State<MfaRequiredGate> createState() => _MfaRequiredGateState();
}

class _MfaRequiredGateState extends State<MfaRequiredGate> {
  late int _cooldown = MfaRequiredGate.cooldownFor(widget.rateLimited, widget.retryAfterSeconds);
  Timer? _timer;

  @override
  void initState() {
    super.initState();
    if (_cooldown > 0) {
      _timer = Timer.periodic(const Duration(seconds: 1), (t) {
        if (!mounted) {
          t.cancel();
          return;
        }
        setState(() {
          _cooldown--;
          if (_cooldown <= 0) t.cancel();
        });
      });
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  Future<void> _signOut() async {
    await MfaFlow.signOut();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const WelcomeScreen()),
      (route) => false,
    );
  }

  @override
  Widget build(BuildContext context) {
    final message = MfaRequiredGate.messageFor(widget.rateLimited, _cooldown > 0 ? _cooldown : 60);
    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(Icons.verified_user_outlined, color: AppColors.primaryGold, size: 56),
                  const SizedBox(height: 16),
                  const Text(
                    'Verification needed',
                    style: TextStyle(color: AppColors.textPrimary, fontSize: 22, fontWeight: FontWeight.bold),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    message,
                    textAlign: TextAlign.center,
                    style: const TextStyle(color: AppColors.textMuted, fontSize: 14),
                  ),
                  const SizedBox(height: 24),
                  ElevatedButton(
                    onPressed: _cooldown > 0
                        ? null
                        : () => Navigator.of(context).pushReplacement(
                              MaterialPageRoute(builder: (_) => widget.retryBuilder()),
                            ),
                    child: Text(_cooldown > 0 ? 'Retry in ${_cooldown}s' : 'Retry'),
                  ),
                  const SizedBox(height: 8),
                  TextButton(onPressed: _signOut, child: const Text('Sign out')),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
