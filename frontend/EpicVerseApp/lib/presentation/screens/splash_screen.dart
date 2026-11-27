import 'package:flutter/foundation.dart' show kReleaseMode;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'welcome_screen.dart';
import 'dashboard_screen.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import '../../core/errors/app_exception.dart';
import '../../core/network/mfa_session_manager.dart';
import '../../core/security/mfa_flow.dart';
import '../../core/security/session_gate.dart';
import '../../core/security/splash_auth.dart';
import '../../core/security/device_integrity.dart';
import '../widgets/security_alert_dialog.dart';
import 'mfa_required_gate.dart';

class SplashScreen extends ConsumerStatefulWidget {
  const SplashScreen({super.key});

  /// Outside dependencies of the start-up sign-in check. Tests may replace them; release builds never do.
  @visibleForTesting
  static SplashAuthDeps? depsForTest;
  static SplashAuthDeps get deps => (kReleaseMode ? null : depsForTest) ?? SplashAuthDeps.production;

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

  /// Finding #4: one shared check (DeviceIntegrity) before anything else. Only
  /// a confirmed compromise blocks; an unknown result keeps normal access and
  /// is checked again at login/signup.
  Future<void> _checkRootAndProceed() async {
    final result = await DeviceIntegrity.check();
    if (!mounted) return;

    if (result.state == IntegrityState.compromised) {
      showSecurityAlertDialog(context);
      return; // Stop execution — do not proceed to auth check
    }

    // Safe, or unknown (transient failure): proceed normally
    _checkAuth();
  }

  // One navigation per splash: a later failure can never navigate a second time.
  bool _navigated = false;

  void _replaceWith(Widget screen, {bool fade = false}) {
    if (_navigated || !mounted) return;
    _navigated = true;
    Navigator.of(context).pushReplacement(fade
        ? PageRouteBuilder(
            transitionDuration: const Duration(milliseconds: 800),
            pageBuilder: (context, animation, secondaryAnimation) => screen,
            transitionsBuilder: (context, animation, secondaryAnimation, child) {
              return FadeTransition(opacity: animation, child: child);
            },
          )
        : MaterialPageRoute(builder: (_) => screen));
  }

  void _goWelcome(SplashAuthDeps deps, {bool fade = false}) =>
      _replaceWith(deps.welcome?.call() ?? const WelcomeScreen(), fade: fade);

  /// The retry/blocked screen. Keeps the Firebase sign-in; Retry re-runs the whole splash check.
  void _goGate({bool mfa = false, bool rateLimited = false, int? retryAfterSeconds, String? title, String? message}) {
    _replaceWith(MfaRequiredGate(
      rateLimited: rateLimited,
      retryAfterSeconds: retryAfterSeconds,
      title: mfa ? null : title,
      message: mfa ? null : message,
      retryBuilder: () => const SplashScreen(),
    ));
  }

  Future<void> _signOutToWelcome(SplashAuthDeps deps, SharedPreferences prefs) async {
    try {
      await deps.signOut();
    } catch (e) {
      debugPrint('[EpicVerse][SPLASH] local sign-out failed: ${e.runtimeType}');
    }
    await prefs.setBool('isLoggedIn', false);
    _goWelcome(deps);
  }

  /// Routes a failed profile check to a safe screen. Never the Dashboard.
  Future<void> _handleFailure(SplashFailure failure, Object error, SplashAuthDeps deps, SharedPreferences prefs) async {
    switch (failure) {
      case SplashFailure.incompleteSignup:
        // Profile genuinely doesn't exist — signup was never completed. The Login flow knows how to resume it.
        await prefs.setBool('isLoggedIn', false);
        _goWelcome(deps, fade: true);
      case SplashFailure.signedOut:
        // Expired, revoked or otherwise invalid sign-in: sign out locally (never account-wide) and start over.
        await _signOutToWelcome(deps, prefs);
      case SplashFailure.mfaRequired:
      case SplashFailure.mfaRepromptNeeded:
        _goGate(mfa: true);
      case SplashFailure.blocked:
        _goGate(
          title: "Can't continue",
          message: "We couldn't confirm your account right now. Try again, or sign out and sign in again.",
        );
      case SplashFailure.rateLimited:
        _goGate(
          rateLimited: true,
          retryAfterSeconds: error is AppException ? error.retryAfterSeconds : null,
          title: 'Please wait',
          message: 'Too many requests. Please wait a moment and try again.',
        );
      case SplashFailure.retry:
        _goGate(
          title: "Can't reach EpicVerse",
          message: "We couldn't load your account. Check your connection and try again.",
        );
    }
  }

  /// The signed-in user's validated profile, or null after routing to a safe screen. A 403 that asks for MFA
  /// gets one re-prompt here and, if verified, one more fetch; ApiClient already re-prompts the 401 form.
  Future<Map<String, dynamic>?> _loadProfile(SplashAuthDeps deps, String uid, SharedPreferences prefs) async {
    var reprompted = false;
    while (true) {
      try {
        final response = await deps.client().get('/user/$uid');
        return validatedProfile(response.statusCode, response.data, uid);
      } catch (e) {
        if (!mounted) return null;
        var failure = classifyProfileFailure(e);
        debugPrint('[EpicVerse][SPLASH] profile check failed: ${failure.name} (${e.runtimeType})');
        if (failure == SplashFailure.mfaRepromptNeeded && !reprompted) {
          reprompted = true;
          final verified = await deps.reprompt();
          if (!mounted) return null;
          if (verified) continue;
          failure = SplashFailure.mfaRequired;
        }
        await _handleFailure(failure, e, deps, prefs);
        return null;
      }
    }
  }

  Future<void> _checkAuth() async {
    // 1. Show Splash Image (Logo) for 3 seconds (allow animation to fully breathe)
    await Future.delayed(const Duration(milliseconds: 3000));

    // 2. Check Auth State and Fetch Real Identity
    final deps = SplashScreen.deps;
    final String? uid = deps.currentUid();
    final prefs = await SharedPreferences.getInstance();
    final bool isLoggedInLocally = prefs.getBool('isLoggedIn') ?? false;

    if (!mounted) return;

    if (uid == null) {
      // No Firebase sign-in: never the Dashboard. A leftover "logged in" flag is stale.
      if (isLoggedInLocally) {
        debugPrint('[EpicVerse][SPLASH] stale login flag without a Firebase user → cleared');
        await prefs.setBool('isLoggedIn', false);
      }
      _goWelcome(deps, fade: true);
      return;
    }

    // 3. Re-Hydrate User Profile from Backend SQL before navigating. Any failure fails closed.
    final data = await _loadProfile(deps, uid, prefs);
    if (data == null || !mounted) return;

    try {
      final user = UserModel.fromJson(data);
      ref.read(userProvider.notifier).setUser(user);

      // F-05 compatibility: GET /user/{uid} is now a pure read and no
      // longer auto-cancels a pending deletion. Preserve the promised
      // "sign back in within 30 days -> automatically cancelled"
      // behavior via the authenticated cancel-deletion endpoint. Kept
      // in its own try/catch so a failure here never blocks start-up.
      final deletionRequestedAt = data['deletion_requested_at'];
      if (deletionRequestedAt != null) {
        debugPrint('[EpicVerse][SPLASH] Pending deletion detected — cancelling');
        try {
          final cancelRes = await deps.client().post('/user/$uid/cancel-deletion');
          if (cancelRes.statusCode != 200) {
            debugPrint('[EpicVerse][SPLASH] cancel-deletion non-200 status=${cancelRes.statusCode}');
          }
        } catch (e) {
          debugPrint('[EpicVerse][SPLASH] cancel-deletion failed: ${e.runtimeType}');
        }
      }

      // F-09: server-authoritative state. A null status with probe.legacy means the backend predates F-09;
      // probe.unavailable means the server could not be asked right now (a temporary problem).
      final probe = await deps.probeSessionStatus();
      final SessionStatus? status = probe.status;
      if (probe.legacy) await MfaSessionManager.clearAll(); // rolled back / old backend: no server sessions exist
      if (probe.unavailable) {
        debugPrint('[EpicVerse][SPLASH] session-status unavailable (temporary)');
        if (SessionGate.needsGateWhenStatusUnavailable(data)) {
          if (!mounted) return;
          _goGate(mfa: true, rateLimited: probe.retryAfterSeconds != null, retryAfterSeconds: probe.retryAfterSeconds);
          return;
        }
      }

      final emailVerified = SessionGate.emailVerified(status, data);
      if (!emailVerified) {
        // F-09 H1: authenticated, server-confirmed verification of THIS
        // signed-in account. On success the normal start-up continues.
        debugPrint('[EpicVerse][SPLASH] email_verified=false → verification flow');
        if (!mounted) return;
        final verified = await deps.verifyEmail(Navigator.of(context));
        if (!verified) {
          await _signOutToWelcome(deps, prefs);
          return;
        }
      }

      // MFA sessions live in memory only, so a restored Firebase
      // sign-in must pass the server's MFA challenge again.
      if (SessionGate.needsServerMfaChallenge(status)) {
        debugPrint('[EpicVerse][SPLASH] MFA required by server → challenge');
        if (!mounted) return;
        final result = await deps.verifyMfa(Navigator.of(context));
        if (result.isTemporary) {
          // Rate limit or network/server trouble: keep the Firebase sign-in and let the user retry.
          if (!mounted) return;
          _goGate(
            mfa: true,
            rateLimited: result.outcome == MfaChallengeOutcome.rateLimited,
            retryAfterSeconds: result.retryAfterSeconds,
          );
          return;
        }
        if (!result.verified) {
          await _signOutToWelcome(deps, prefs);
          return;
        }
      }
    } catch (e) {
      // Malformed profile data or an unexpected error in the checks: fail closed (retry screen), sign-in kept.
      debugPrint('[EpicVerse][SPLASH] start-up checks failed: ${e.runtimeType}');
      if (!mounted) return;
      _goGate(
        title: "Can't reach EpicVerse",
        message: "We couldn't load your account. Check your connection and try again.",
      );
      return;
    }

    if (!mounted) return;
    // Every check above passed.
    _replaceWith(deps.dashboard?.call() ?? const DashboardScreen(), fade: true);
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
