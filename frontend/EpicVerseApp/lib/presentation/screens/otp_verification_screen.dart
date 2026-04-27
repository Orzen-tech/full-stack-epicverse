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

  @override
  void dispose() {
    for (var controller in _controllers) controller.dispose();
    for (var node in _focusNodes) node.dispose();
    super.dispose();
  }

  Future<void> _verifyOtp() async {
    if (_isVerified) return;
    String otp = _controllers.map((c) => c.text).join();
    if (otp.length < 6) return;

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      if (widget.phone != null && widget.verificationId != null) {
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
        final dio = Dio();
        final formData = FormData.fromMap({
          'identifier': widget.email,
          'otp': otp,
        });

        final response = await dio.post(
          '${ApiConfig.apiUrl}/auth/verify-otp',
          data: formData,
        );

        if (response.statusCode == 200) {
          _isVerified = true;
          widget.onVerified();
        }
      }
    } catch (e) {
      setState(() {
        _errorMessage = e is FirebaseAuthException ? e.message : "Invalid or expired code.";
      });
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _resendOtp() async {
    setState(() => _isLoading = true);
    try {
      if (widget.phone != null) {
        // Firebase handles resending via the verifyPhoneNumber call again
        // For simplicity, we suggest going back or we could trigger another verify call here
        _showSnackBar("Please go back and request a new code.");
      } else if (widget.email != null) {
        final dio = Dio();
        final formData = FormData.fromMap({'identifier': widget.email});
        await dio.post('${ApiConfig.apiUrl}/auth/send-otp', data: formData);
        _showSnackBar("A new code has been sent to your email.");
      }
    } catch (e) {
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
                    const SizedBox(height: 48),
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
        TextButton(
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
