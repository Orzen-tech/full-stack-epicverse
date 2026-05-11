import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import 'dashboard_screen.dart';
import '../../core/network/api_config.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../core/network/session_manager.dart';
import 'welcome_screen.dart';
import 'otp_verification_screen.dart';
import 'create_profile_screen.dart';

class LoginScreen extends ConsumerStatefulWidget {
  const LoginScreen({super.key});

  @override
  ConsumerState<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends ConsumerState<LoginScreen> {
  final _formKey = GlobalKey<FormState>();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  bool _isLoading = false;
  bool _obscurePassword = true;
  final Dio _dio = Dio();

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _handleLogin() async {
    if (!_formKey.currentState!.validate()) return;

    debugPrint('[EpicVerse][LOGIN] Sign-In tapped email=${_emailController.text.trim()}');
    setState(() => _isLoading = true);

    try {
      // 1. Sign in with Firebase (Primary Identity Check)
      debugPrint('[EpicVerse][LOGIN] Firebase signInWithEmailAndPassword...');
      final UserCredential credential = await FirebaseAuth.instance.signInWithEmailAndPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text.trim(),
      );

      final firebaseUser = credential.user;
      if (firebaseUser == null) throw Exception("Login failed");
      debugPrint('[EpicVerse][LOGIN] Firebase auth OK uid=${firebaseUser.uid}');

      // 2. Optimistic UI with Session Tracking
      final sessionId = await SessionManager.getSessionId();
      final loggedInUser = UserModel(
        id: firebaseUser.uid,
        displayName: firebaseUser.displayName ?? "Epic Explorer",
        email: firebaseUser.email ?? _emailController.text.trim(),
        primaryLanguage: 'English',
        preferredLanguages: ['English'],
        sessionId: sessionId,
      );

      // 3. Update Provider and Local Persistence
      ref.read(userProvider.notifier).setUser(loggedInUser);
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('isLoggedIn', true);

      // 4. Check if Backend Profile Exists (Production Ready Sign-In Step)
      bool profileExists = false;
      try {
        debugPrint('[EpicVerse][LOGIN] GET /user/${firebaseUser.uid}');
        final res = await _dio.get(
          '${ApiConfig.apiUrl}/user/${firebaseUser.uid}',
          options: Options(headers: await ApiConfig.authHeaders()),
        );
        debugPrint('[EpicVerse][LOGIN] /user response status=${res.statusCode}');
        if (res.statusCode == 200) {
          profileExists = true;
          // Update local state with rich backend data immediately
          final fullUser = UserModel.fromJson(res.data);
          ref.read(userProvider.notifier).setUser(fullUser);

          // Block login if OTP was never verified (app closed mid-registration)
          final emailVerified = res.data['email_verified'] ?? false;
          if (!emailVerified) {
            debugPrint('[EpicVerse][LOGIN] email_verified=false → OTP screen');
            if (!mounted) return;
            final navigator = Navigator.of(context);
            navigator.pushReplacement(
              MaterialPageRoute(
                builder: (_) => OtpVerificationScreen(
                  email: firebaseUser.email ?? '',
                  onVerified: () {
                    navigator.pushAndRemoveUntil(
                      MaterialPageRoute(builder: (_) => const DashboardScreen()),
                      (route) => false,
                    );
                  },
                ),
              ),
            );
            return;
          }
        }
      } catch (e) {
        debugPrint("User not found in backend: Proceeding to profile creation flow.");
      }

      // 5. Verification Guard (Only for first-time profile creation)
      if (!profileExists) {
        if (!mounted) return;
        
        // Trigger OTP via Backend
        try {
           debugPrint('[EpicVerse][LOGIN] Profile missing → POST /auth/send-otp (bearer)');
           final idToken = await firebaseUser.getIdToken();
           final formData = FormData.fromMap({'identifier': firebaseUser.email});
           final otpRes = await _dio.post(
             '${ApiConfig.apiUrl}/auth/send-otp',
             data: formData,
             options: Options(headers: {...ApiConfig.headers, 'Authorization': 'Bearer $idToken'}),
           );
           debugPrint('[EpicVerse][LOGIN] /auth/send-otp status=${otpRes.statusCode}');
        } catch (e) {
           debugPrint("OTP Send failed: $e");
           if (mounted) {
             ScaffoldMessenger.of(context).showSnackBar(
               const SnackBar(content: Text("Failed to send verification code. Please try again."), backgroundColor: Colors.redAccent),
             );
           }
           return; // Stop flow if OTP fails
        }

        // Capture NavigatorState before pushReplacement — LoginScreen will be
        // disposed, but this NavigatorState remains valid for onVerified.
        final navigator = Navigator.of(context);
        navigator.pushReplacement(
          MaterialPageRoute(
            builder: (_) => OtpVerificationScreen(
              email: loggedInUser.email,
              onVerified: () {
                debugPrint('[EpicVerse][LOGIN] OTP verified → CreateProfile');
                navigator.pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const CreateProfileScreen()),
                  (route) => false,
                );
              },
            )
          ),
        );
        return;
      }

      // 6. Resume Journey: Already has profile, skip directly to Dashboard
      if (!mounted) return;
      debugPrint('[EpicVerse][LOGIN] Profile exists → Dashboard');
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const DashboardScreen()),
      );

      // 5. BACKGROUND BACKGROUND: Sync/Fetch with Backend silently
      // We don't await this to keep the login instant.
      _syncUserInBackground(firebaseUser.uid, loggedInUser);

    } on FirebaseAuthException catch (e) {
      debugPrint('[EpicVerse][LOGIN] FirebaseAuthException code=${e.code} msg=${e.message}');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.message ?? 'Login failed'), backgroundColor: Colors.redAccent),
      );
    } catch (e) {
      debugPrint('[EpicVerse][LOGIN] Login error: $e');
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString()), backgroundColor: Colors.redAccent),
      );
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  /// Silently update/fetch the full profile from the backend server
  Future<void> _syncUserInBackground(String uid, UserModel fallback) async {
    try {
      final response = await _dio.get(
        '${ApiConfig.apiUrl}/user/$uid',
        options: Options(headers: await ApiConfig.authHeaders()),
      );
      
      if (response.statusCode == 200) {
        final data = response.data;
        // Use the factory to ensure consistent field mapping (including profile_picture)
        final fullUser = UserModel.fromJson(data);
        
        // Silently update the global state with rich backend data
        ref.read(userProvider.notifier).setUser(fullUser);
      }
    } catch (e) {
      // If user doesn't exist in backend, trigger initial sync
      final syncHeaders = await ApiConfig.authHeaders();
      _dio.post(
        '${ApiConfig.apiUrl}/sync-user',
        data: {
          "uid": uid,
          "display_name": fallback.displayName,
          "email": fallback.email,
          "primary_language": fallback.primaryLanguage,
          "profile_picture": null,
          "session_id": fallback.sessionId,
        },
        options: Options(headers: syncHeaders),
      ).catchError((e) {
        debugPrint("Background sync failed: $e");
        return Response(requestOptions: RequestOptions(path: ''), statusCode: 500);
      });
    }
  }

  Future<void> _handleForgotPassword() async {
    final dialogController = TextEditingController(text: _emailController.text.trim());

    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (ctx) {
        bool sending = false;
        String? errorMsg;

        return StatefulBuilder(
          builder: (ctx, setDialogState) => AlertDialog(
            backgroundColor: const Color(0xFF1B0C2D),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
            title: const Text('Reset Password', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text("We'll send a reset link to your email.", style: TextStyle(color: Colors.white70, fontSize: 13)),
                const SizedBox(height: 16),
                TextField(
                  controller: dialogController,
                  keyboardType: TextInputType.emailAddress,
                  autofocus: true,
                  style: const TextStyle(color: Colors.white),
                  decoration: InputDecoration(
                    hintText: 'your@email.com',
                    hintStyle: const TextStyle(color: Colors.white38),
                    prefixIcon: const Icon(Icons.email_outlined, color: Colors.white38, size: 20),
                    filled: true,
                    fillColor: Colors.white.withValues(alpha: 0.07),
                    border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: BorderSide.none),
                  ),
                ),
                if (errorMsg != null) ...[
                  const SizedBox(height: 10),
                  Text(errorMsg!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
                ],
              ],
            ),
            actions: [
              TextButton(
                onPressed: sending ? null : () => Navigator.pop(ctx),
                child: const Text('Cancel', style: TextStyle(color: Colors.white54)),
              ),
              TextButton(
                onPressed: sending ? null : () async {
                  final email = dialogController.text.trim();
                  if (email.isEmpty) {
                    setDialogState(() => errorMsg = 'Please enter your email.');
                    return;
                  }
                  setDialogState(() { sending = true; errorMsg = null; });
                  try {
                    await _dio.post(
                      '${ApiConfig.apiUrl}/auth/send-password-reset',
                      data: FormData.fromMap({'identifier': email}),
                      options: Options(headers: ApiConfig.headers),
                    );
                    if (ctx.mounted) Navigator.pop(ctx);
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Reset link sent! Check your inbox.'), backgroundColor: Colors.green),
                      );
                    }
                  } on DioException catch (e) {
                    final msg = e.response?.data?['detail'] ?? 'Failed to send reset email.';
                    setDialogState(() { sending = false; errorMsg = msg; });
                  } catch (_) {
                    setDialogState(() { sending = false; errorMsg = 'An error occurred. Please try again.'; });
                  }
                },
                child: sending
                    ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Color(0xFFD4AF37)))
                    : const Text('Send Link', style: TextStyle(color: Color(0xFFD4AF37), fontWeight: FontWeight.bold)),
              ),
            ],
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              AppBar(
                backgroundColor: Colors.transparent,
                elevation: 0,
                leading: IconButton(
                  icon: const Icon(Icons.arrow_back, color: AppColors.textPrimary),
                  onPressed: () {
                    if (Navigator.of(context).canPop()) {
                      Navigator.of(context).pop();
                    } else {
                      Navigator.of(context).pushReplacement(
                        MaterialPageRoute(builder: (_) => const WelcomeScreen()),
                      );
                    }
                  },
                ),
                title: const Text('Login', style: TextStyle(fontWeight: FontWeight.bold)),
              ),
              Expanded(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.all(24),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const SizedBox(height: 40),
                        const Text(
                          'WELCOME BACK',
                          style: TextStyle(
                            color: AppColors.primaryGold,
                            fontSize: 12,
                            letterSpacing: 4,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        const SizedBox(height: 8),
                        const Text(
                          'Resume your journey',
                          style: TextStyle(color: AppColors.textPrimary, fontSize: 24, fontWeight: FontWeight.bold),
                        ),
                        const SizedBox(height: 48),
                        
                        _buildFieldLabel('EMAIL'),
                        TextFormField(
                          controller: _emailController,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildInputDecoration('your@email.com', Icons.email_outlined),
                          validator: (v) => (v == null || v.isEmpty) ? 'Required' : null,
                        ),
                        
                        const SizedBox(height: 24),
                        _buildFieldLabel('PASSWORD'),
                        TextFormField(
                          controller: _passwordController,
                          obscureText: _obscurePassword,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildInputDecoration('••••••••', Icons.lock_outline).copyWith(
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscurePassword ? Icons.visibility_off : Icons.visibility,
                                color: AppColors.textMuted,
                              ),
                              onPressed: () => setState(() => _obscurePassword = !_obscurePassword),
                            ),
                          ),
                          validator: (v) => (v == null || v.isEmpty) ? 'Required' : null,
                        ),
                        
                        const SizedBox(height: 12),
                        Align(
                          alignment: Alignment.centerRight,
                          child: TextButton(
                            onPressed: _isLoading ? null : _handleForgotPassword,
                            child: const Text(
                              'Forgot Password?',
                              style: TextStyle(
                                color: AppColors.primaryGold,
                                fontWeight: FontWeight.bold,
                                fontSize: 13,
                              ),
                            ),
                          ),
                        ),
                        
                        const SizedBox(height: 24),
                        GestureDetector(
                          onTap: _isLoading ? null : _handleLogin,
                          child: Container(
                            width: double.infinity,
                            height: 60,
                            decoration: BoxDecoration(
                              gradient: const LinearGradient(
                                colors: [Color(0xFF41246D), Color(0xFF331652)],
                                begin: Alignment.topCenter,
                                end: Alignment.bottomCenter,
                              ),
                              borderRadius: BorderRadius.circular(14),
                              border: Border.all(color: Colors.white.withValues(alpha: 0.1)),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withValues(alpha: 0.3),
                                  blurRadius: 15,
                                  offset: const Offset(0, 8),
                                ),
                              ],
                            ),
                            alignment: Alignment.center,
                            child: _isLoading 
                              ? const CircularProgressIndicator(color: Colors.white)
                              : const Text('Sign In', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16)),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  InputDecoration _buildInputDecoration(String hint, IconData icon) {
    return InputDecoration(
      hintText: hint,
      hintStyle: const TextStyle(color: AppColors.textMuted, fontSize: 14),
      prefixIcon: Icon(icon, color: AppColors.primaryGold.withValues(alpha: 0.7), size: 20),
      filled: true,
      fillColor: Colors.white.withValues(alpha: 0.05),
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.1))),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.1))),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.primaryGold, width: 1.5)),
    );
  }

  Widget _buildFieldLabel(String label) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10, left: 4),
      child: Text(label, style: const TextStyle(color: AppColors.primaryGold, fontSize: 11, fontWeight: FontWeight.w800, letterSpacing: 1.5)),
    );
  }
}
