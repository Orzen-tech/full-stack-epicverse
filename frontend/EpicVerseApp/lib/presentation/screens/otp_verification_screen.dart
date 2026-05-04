import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import '../../core/constants/app_colors.dart';
import '../../core/network/api_config.dart';
import '../widgets/network_background.dart';

class OtpVerificationScreen extends StatefulWidget {
  final String? email;
  final String? phone;
  final String? verificationId;
  final VoidCallback onVerified;

  const OtpVerificationScreen({
    super.key,
    this.email,
    this.phone,
    this.verificationId,
    required this.onVerified,
  });

  @override
  State<OtpVerificationScreen> createState() => _OtpVerificationScreenState();
}

class _OtpVerificationScreenState extends State<OtpVerificationScreen> {
  final List<TextEditingController> _controllers = List.generate(6, (index) => TextEditingController());
  final List<FocusNode> _focusNodes = List.generate(6, (index) => FocusNode());
  bool _isLoading = false;
  String? _errorMessage;
  bool _isVerified = false;

  // Countdown timer
  int _remainingSeconds = 60;
  Timer? _timer;

  // Cooldown for rate limiting (after 3 resends)
  int _cooldownSeconds = 0;
  Timer? _cooldownTimer;

  @override
  void initState() {
    super.initState();
    _startTimer();
  }

  @override
  void dispose() {
    _timer?.cancel();
    _cooldownTimer?.cancel();
    for (var controller in _controllers) controller.dispose();
    for (var node in _focusNodes) node.dispose();
    super.dispose();
  }

  void _startTimer() {
    _timer?.cancel();
    setState(() => _remainingSeconds = 60);
    _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_remainingSeconds > 0) {
        if (mounted) setState(() => _remainingSeconds--);
      } else {
        _timer?.cancel();
      }
    });
  }

  String get _formattedTime {
    final minutes = (_remainingSeconds ~/ 60).toString().padLeft(2, '0');
    final seconds = (_remainingSeconds % 60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }

  String get _formattedCooldown {
    final minutes = (_cooldownSeconds ~/ 60).toString().padLeft(2, '0');
    final seconds = (_cooldownSeconds % 60).toString().padLeft(2, '0');
    return '$minutes:$seconds';
  }

  void _startCooldown(int seconds) {
    _cooldownTimer?.cancel();
    setState(() => _cooldownSeconds = seconds);
    _cooldownTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (_cooldownSeconds > 0) {
        if (mounted) setState(() => _cooldownSeconds--);
      } else {
        _cooldownTimer?.cancel();
        if (mounted) setState(() {}); // refresh UI
      }
    });
  }

  Future<void> _verifyOtp() async {
    if (_isVerified) return;
    String otp = _controllers.map((c) => c.text).join();
    if (otp.length < 6) return;

    debugPrint('[EpicVerse][OTP] Verify tapped (auto) length=${otp.length}');
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      if (widget.phone != null && widget.verificationId != null) {
        debugPrint('[EpicVerse][OTP] Verifying via Firebase phone');
        // --- FIREBASE PHONE VERIFICATION ---
        PhoneAuthCredential credential = PhoneAuthProvider.credential(
          verificationId: widget.verificationId!,
          smsCode: otp,
        );
        await FirebaseAuth.instance.signInWithCredential(credential);
        _isVerified = true;
        widget.onVerified();
      } else if (widget.email != null) {
        // --- CUSTOM EMAIL VERIFICATION ---
        debugPrint('[EpicVerse][OTP] POST /auth/verify-otp identifier=${widget.email}');
        final dio = Dio();
        final formData = FormData.fromMap({
          'identifier': widget.email,
          'otp': otp,
        });

        final response = await dio.post(
          '${ApiConfig.apiUrl}/auth/verify-otp',
          data: formData,
        );

        debugPrint('[EpicVerse][OTP] /auth/verify-otp status=${response.statusCode}');
        if (response.statusCode == 200) {
          _isVerified = true;
          debugPrint('[EpicVerse][OTP] Verification SUCCESS');
          widget.onVerified();
        }
      }
    } catch (e) {
      debugPrint('[EpicVerse][OTP] Verify error: $e');
      setState(() {
        _errorMessage = e is FirebaseAuthException ? e.message : "Invalid or expired code.";
      });
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _resendOtp() async {
    debugPrint('[EpicVerse][OTP] Resend tapped');
    setState(() => _isLoading = true);
    try {
      if (widget.phone != null) {
        // Firebase handles resending via the verifyPhoneNumber call again
        // For simplicity, we suggest going back or we could trigger another verify call here
        _showSnackBar("Please go back and request a new code.");
      } else if (widget.email != null) {
        final dio = Dio();
        final user = FirebaseAuth.instance.currentUser;
        final idToken = await user?.getIdToken();
        debugPrint('[EpicVerse][OTP] POST /auth/send-otp (resend) bearer=${idToken != null}');
        final formData = FormData.fromMap({'identifier': widget.email});
        final res = await dio.post(
          '${ApiConfig.apiUrl}/auth/send-otp',
          data: formData,
          options: Options(headers: {
            'Content-Type': 'application/json',
            if (idToken != null) 'Authorization': 'Bearer $idToken',
          }),
        );
        debugPrint('[EpicVerse][OTP] /auth/send-otp resend status=${res.statusCode}');
        _startTimer(); // Restart countdown on resend
        _showSnackBar("A new code has been sent to your email.");
      }
    } on DioException catch (e) {
      debugPrint('[EpicVerse][OTP] Resend error: $e');
      if (e.response?.statusCode == 429) {
        // Rate limited - parse Retry-After header
        final retryAfter = int.tryParse(e.response?.headers.value('retry-after') ?? '600') ?? 600;
        _startCooldown(retryAfter);
        final msg = e.response?.data?['detail'] ?? 'Too many requests. Please try again later.';
        _showSnackBar(msg);
      } else {
        _showSnackBar("Failed to resend code.");
      }
    } catch (e) {
      debugPrint('[EpicVerse][OTP] Resend error: $e');
      _showSnackBar("Failed to resend code.");
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  void _showSnackBar(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
  }

  @override
  Widget build(BuildContext context) {
    final isPhone = widget.phone != null;
    final identifier = isPhone ? widget.phone : widget.email;

    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 24.0),
            child: Stack(
              children: [
                Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      isPhone ? Icons.phone_android_outlined : Icons.mark_email_read_outlined, 
                      size: 80, 
                      color: AppColors.primaryGold
                    ),
                    const SizedBox(height: 24),
                    Text(
                      isPhone ? 'Verify Phone' : 'Verify Email',
                      style: const TextStyle(color: Colors.white, fontSize: 28, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      'We sent a 6-digit code to\n$identifier',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: Colors.white.withOpacity(0.7), fontSize: 16),
                    ),
                    const SizedBox(height: 48),
                    _buildOtpInput(),
                    if (_isVerified) _buildStatusText("valid code", Colors.greenAccent)
                    else if (_errorMessage != null) _buildStatusText(_errorMessage!, Colors.redAccent),
                    const SizedBox(height: 32),
                    _buildActionButtons(),
                  ],
                ),
                _buildBackButton(),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildOtpInput() {
    return Row(
      mainAxisAlignment: MainAxisAlignment.spaceBetween,
      children: List.generate(6, (index) {
        return SizedBox(
          width: 45,
          child: TextField(
            controller: _controllers[index],
            focusNode: _focusNodes[index],
            textAlign: TextAlign.center,
            keyboardType: TextInputType.number,
            maxLength: 1,
            style: const TextStyle(color: Colors.white, fontSize: 24, fontWeight: FontWeight.bold),
            autofillHints: const [AutofillHints.oneTimeCode],
            decoration: InputDecoration(
              counterText: "",
              enabledBorder: OutlineInputBorder(borderSide: BorderSide(color: Colors.white.withOpacity(0.3)), borderRadius: BorderRadius.circular(8)),
              focusedBorder: OutlineInputBorder(borderSide: const BorderSide(color: AppColors.primaryGold, width: 2), borderRadius: BorderRadius.circular(8)),
              fillColor: Colors.white.withOpacity(0.05),
              filled: true,
            ),
            onChanged: (value) {
              if (value.isNotEmpty && index < 5) _focusNodes[index + 1].requestFocus();
              if (value.isEmpty && index > 0) _focusNodes[index - 1].requestFocus();
              if (_controllers.every((c) => c.text.isNotEmpty)) _verifyOtp();
            },
          ),
        );
      }),
    );
  }

  Widget _buildStatusText(String text, Color color) => Padding(
    padding: const EdgeInsets.only(top: 24),
    child: Text(text, style: TextStyle(color: color, fontSize: 14, fontWeight: FontWeight.bold)),
  );

  Widget _buildActionButtons() {
    if (_isLoading) return const CircularProgressIndicator(color: AppColors.primaryGold);
    return Column(
      children: [
        ElevatedButton(
          onPressed: _verifyOtp,
          style: ElevatedButton.styleFrom(
            backgroundColor: AppColors.primaryGold,
            foregroundColor: Colors.black,
            minimumSize: const Size(double.infinity, 56),
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          ),
          child: const Text('VERIFY', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
        ),
        const SizedBox(height: 24),
        _cooldownSeconds > 0
          ? Text(
              'Rate limit reached. Retry in $_formattedCooldown',
              style: const TextStyle(color: Colors.redAccent, fontSize: 14, fontWeight: FontWeight.w500),
            )
          : _remainingSeconds > 0
            ? Text(
                'Resend in $_formattedTime',
                style: TextStyle(color: Colors.white.withOpacity(0.5), fontSize: 14),
              )
            : TextButton(
                onPressed: _resendOtp,
                child: const Text("Didn't receive code? Resend", style: TextStyle(color: AppColors.primaryGold)),
              ),
      ],
    );
  }

  Widget _buildBackButton() => Positioned(
    top: 0, left: -8,
    child: IconButton(icon: const Icon(Icons.arrow_back, color: Colors.white), onPressed: () => Navigator.pop(context)),
  );
}
