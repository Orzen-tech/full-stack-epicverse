import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:dio/dio.dart';
import '../../core/constants/app_colors.dart';
import '../../core/network/api_config.dart';
import '../widgets/network_background.dart';

class OtpVerificationScreen extends StatefulWidget {
  final String email;
  final VoidCallback onVerified;

  const OtpVerificationScreen({
    super.key,
    required this.email,
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

  @override
  void dispose() {
    for (var controller in _controllers) {
      controller.dispose();
    }
    for (var node in _focusNodes) {
      node.dispose();
    }
    super.dispose();
  }

  bool _isVerified = false;

  Future<void> _verifyOtp() async {
    if (_isVerified) return; // Prevent double trigger
    String otp = _controllers.map((c) => c.text).join();
    if (otp.length < 6) return;

    if (mounted) {
      setState(() {
        _isLoading = true;
        _errorMessage = null;
      });
    }

    Response? response;
    try {
      final dio = Dio();
      final formData = FormData.fromMap({
        'email': widget.email,
        'otp': otp,
      });

      response = await dio.post(
        '${ApiConfig.apiUrl}/auth/verify-otp',
        data: formData,
      );

      if (response != null) {
        debugPrint("OTP Response Status: ${response.statusCode}");
        debugPrint("OTP Response Body: ${response.data}");
      }

      if (response != null && response.statusCode == 200) {
        if (!mounted) return;
        // 1. Mark as verified immediately for UI feedback
        _isVerified = true; 
        setState(() => _errorMessage = null);
        
        print("[OTP] Success! Calling onVerified callback...");
        
        // 2. Trigger the callback immediately
        widget.onVerified();
      } else {
        setState(() {
          _errorMessage = response?.data?['detail'] ?? "Verification failed";
        });
      }
    } catch (e) {
      print("OTP VERIFY ERROR: $e");
      if (response != null && response.statusCode == 200) {
         // If we are here, the BACKEND worked, but onVerified() crashed.
         // Don't show "Invalid Code", show "Navigation Error"
         setState(() => _errorMessage = "Login successful, but Dashboard failed to load. Check assets.");
         return;
      }
      
      setState(() {
        if (e is DioException) {
           _errorMessage = e.response?.data?['detail'] ?? "Network error: ${e.message}";
        } else {
           _errorMessage = "Invalid or expired code. Please try again.";
        }
      });
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _resendOtp() async {
    setState(() => _isLoading = true);
    try {
      final dio = Dio();
      final formData = FormData.fromMap({'email': widget.email});
      await dio.post('${ApiConfig.apiUrl}/auth/send-otp', data: formData);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("A new code has been sent to your email.")),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text("Failed to resend code.")),
        );
      }
    } finally {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
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
                    const Icon(Icons.mark_email_read_outlined, size: 80, color: AppColors.primaryGold),
                    const SizedBox(height: 24),
                    const Text(
                      'Verify Your Email',
                      style: TextStyle(
                        color: Colors.white,
                        fontSize: 28,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                    const SizedBox(height: 12),
                    Text(
                      'We sent a 6-digit code to\n${widget.email}',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: Colors.white.withValues(alpha: 0.7),
                        fontSize: 16,
                      ),
                    ),
                    const SizedBox(height: 48),
                    
                    // OTP Input Row
                    Row(
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
                            // iOS Auto-Fill Support
                            autofillHints: const [AutofillHints.oneTimeCode],
                            decoration: InputDecoration(
                              counterText: "",
                              enabledBorder: OutlineInputBorder(
                                borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.3)),
                                borderRadius: BorderRadius.circular(8),
                              ),
                              focusedBorder: OutlineInputBorder(
                                borderSide: const BorderSide(color: AppColors.primaryGold, width: 2),
                                borderRadius: BorderRadius.circular(8),
                              ),
                              fillColor: Colors.white.withValues(alpha: 0.05),
                              filled: true,
                            ),
                            onChanged: (value) {
                              if (value.isNotEmpty && index < 5) {
                                _focusNodes[index + 1].requestFocus();
                              }
                              if (value.isEmpty && index > 0) {
                                _focusNodes[index - 1].requestFocus();
                              }
                              if (_controllers.every((c) => c.text.isNotEmpty)) {
                                _verifyOtp();
                              }
                            },
                          ),
                        );
                      }),
                    ),
                    
                    if (_isVerified) ...[
                      const SizedBox(height: 24),
                      const Text(
                        "valid code",
                        style: TextStyle(color: Colors.greenAccent, fontSize: 16, fontWeight: FontWeight.bold),
                      ),
                    ] else if (_errorMessage != null) ...[
                      const SizedBox(height: 24),
                      const Text(
                        "code invalid or expired",
                        style: TextStyle(color: Colors.redAccent, fontSize: 14),
                      ),
                    ],
                    
                    const SizedBox(height: 48),
                    
                    if (_isLoading)
                      const CircularProgressIndicator(color: AppColors.primaryGold)
                    else ...[
                      ElevatedButton(
                        onPressed: () {
                          print("[OTP] Verify button clicked!");
                          _verifyOtp();
                        },
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
                        child: const Text(
                          "Didn't receive code? Resend",
                          style: TextStyle(color: AppColors.primaryGold),
                        ),
                      ),
                    ],
                  ],
                ),
                Positioned(
                  top: 0,
                  left: -8,
                  child: IconButton(
                    icon: const Icon(Icons.arrow_back, color: Colors.white),
                    onPressed: () => Navigator.pop(context),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
