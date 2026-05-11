import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import 'package:image_picker/image_picker.dart';
import 'dart:convert';
import 'dart:io';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import '../../core/network/api_config.dart';
import '../../core/network/session_manager.dart';
import 'otp_verification_screen.dart';
import 'dashboard_screen.dart';
import 'legal_content_screen.dart';

class CreateProfileScreen extends ConsumerStatefulWidget {
  const CreateProfileScreen({super.key});

  @override
  ConsumerState<CreateProfileScreen> createState() => _CreateProfileScreenState();
}

class _CreateProfileScreenState extends ConsumerState<CreateProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  final TextEditingController _nameController = TextEditingController();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  final TextEditingController _inviteController = TextEditingController();

  bool _obscurePassword = true;
  bool _isInviteValid = false;
  String? _inviteFeedback;
  bool _isLoading = false;
  bool _acceptedPrivacy = false;
  bool _acceptedTerms = false;
  final Dio _dio = Dio();
  File? _profileImage;
  final ImagePicker _picker = ImagePicker();

  // Email inline verification
  bool _emailVerified = false;
  bool _isVerifyingEmail = false;
  bool _showOtpRow = false;
  String? _emailOtpError;
  final List<TextEditingController> _otpControllers =
      List.generate(6, (_) => TextEditingController());
  final List<FocusNode> _otpFocusNodes =
      List.generate(6, (_) => FocusNode());

  @override
  void initState() {
    super.initState();
    _inviteController.addListener(_onInviteChanged);
    _emailController.addListener(_onEmailChanged);
  }

  void _onInviteChanged() {
    final text = _inviteController.text.trim();
    if (text.length >= 6) {
      _validateInviteCode(text);
    } else {
      if (mounted) {
        setState(() {
          _isInviteValid = false;
          _inviteFeedback = null;
        });
      }
    }
  }

  Future<void> _validateInviteCode(String code) async {
    String rawCode = code.replaceAll(' ', '');
    if (rawCode.startsWith('EPIC-')) rawCode = rawCode.substring(5);
    final inviteCode = "EPIC-$rawCode";
    try {
      debugPrint('[EpicVerse][REG] GET /validate-invite/$inviteCode');
      final response = await _dio.get(
        '${ApiConfig.apiUrl}/validate-invite/$inviteCode',
        options: Options(headers: ApiConfig.headers),
      );
      final isValid = response.statusCode == 200 && response.data['valid'] == true;
      debugPrint('[EpicVerse][REG] Invite check status=${response.statusCode} valid=$isValid');
      if (mounted) {
        setState(() {
          _isInviteValid = isValid;
          _inviteFeedback = isValid ? "Valid code" : "Invalid invite code";
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _isInviteValid = false;
          _inviteFeedback = null;
        });
      }
    }
  }

  Future<bool> _checkInviteCodeValid(String inviteCode) async {
    try {
      final response = await _dio.get(
        '${ApiConfig.apiUrl}/validate-invite/$inviteCode',
        options: Options(headers: ApiConfig.headers),
      );
      return response.statusCode == 200 && response.data['valid'] == true;
    } catch (_) {
      return false;
    }
  }

  @override
  void dispose() {
    _inviteController.removeListener(_onInviteChanged);
    _emailController.removeListener(_onEmailChanged);
    _nameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    _inviteController.dispose();
    for (final c in _otpControllers) c.dispose();
    for (final n in _otpFocusNodes) n.dispose();
    super.dispose();
  }

  void _onEmailChanged() {
    if (_showOtpRow || _emailVerified) {
      setState(() {
        _showOtpRow = false;
        _emailVerified = false;
        _emailOtpError = null;
      });
      for (final c in _otpControllers) c.clear();
    }
  }

  Future<void> _sendEmailOtp() async {
    final email = _emailController.text.trim();
    if (email.isEmpty || !email.contains('@')) {
      _showError('Please enter a valid email first');
      return;
    }
    setState(() { _isVerifyingEmail = true; _emailOtpError = null; });
    try {
      await _dio.post(
        '${ApiConfig.apiUrl}/auth/send-email-otp',
        data: FormData.fromMap({'identifier': email}),
        options: Options(headers: ApiConfig.headers),
      );
      setState(() => _showOtpRow = true);
    } on DioException catch (e) {
      if (e.response?.statusCode == 429) {
        _showError('Too many attempts. Please wait before trying again.');
      } else {
        _showError('Failed to send verification code. Please try again.');
      }
    } catch (e) {
      _showError('Failed to send verification code. Please try again.');
    } finally {
      if (mounted) setState(() => _isVerifyingEmail = false);
    }
  }

  Future<void> _verifyEmailOtp() async {
    final otp = _otpControllers.map((c) => c.text).join();
    if (otp.length < 6) return;
    setState(() { _isVerifyingEmail = true; _emailOtpError = null; });
    try {
      await _dio.post(
        '${ApiConfig.apiUrl}/auth/verify-otp',
        data: FormData.fromMap({'identifier': _emailController.text.trim(), 'otp': otp}),
        options: Options(headers: ApiConfig.headers),
      );
      setState(() {
        _emailVerified = true;
        _showOtpRow = false;
      });
      for (final c in _otpControllers) c.clear();
    } catch (e) {
      setState(() => _emailOtpError = 'Invalid or expired code. Try again.');
      for (final c in _otpControllers) c.clear();
      _otpFocusNodes[0].requestFocus();
    } finally {
      if (mounted) setState(() => _isVerifyingEmail = false);
    }
  }

  Future<void> _pickImage() async {
    final XFile? image = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 50);
    if (image != null) {
      setState(() => _profileImage = File(image.path));
    }
  }


  Future<void> _submitForm() async {
    debugPrint('[EpicVerse][REG] GET STARTED tapped');
    if (!_formKey.currentState!.validate()) {
      debugPrint('[EpicVerse][REG] Form validation failed');
      _showError('Please fill all fields');
      return;
    }
    if (!_acceptedPrivacy || !_acceptedTerms) {
      _showError('Please accept Privacy Policy and Terms of Service');
      return;
    }
    if (!_emailVerified) {
      _showError('Please verify your email first');
      return;
    }
    setState(() => _isLoading = true);
    try {
      String rawCode = _inviteController.text.trim().replaceAll(' ', '');
      if (rawCode.startsWith('EPIC-')) rawCode = rawCode.substring(5);
      final inviteCode = "EPIC-$rawCode";

      // Step 1: Verify invite code BEFORE touching Firebase
      debugPrint('[EpicVerse][REG] Step 1: validating invite $inviteCode');
      final isValid = await _checkInviteCodeValid(inviteCode);
      if (!isValid) {
        debugPrint('[EpicVerse][REG] Invite invalid, abort');
        _showError('Invalid invite code. Please check and try again.');
        if (mounted) setState(() => _isInviteValid = false);
        return;
      }

      // Step 2: Create Firebase account
      debugPrint('[EpicVerse][REG] Step 2: createUserWithEmailAndPassword');
      final cred = await FirebaseAuth.instance.createUserWithEmailAndPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text.trim(),
      );
      debugPrint('[EpicVerse][REG] Firebase user created uid=${cred.user?.uid}');
      await _completeRegistration(cred, inviteCode);
    } catch (e) {
      debugPrint('[EpicVerse][REG] Registration error: $e');
      _showError(e.toString());
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  Future<void> _completeRegistration(dynamic cred, String inviteCode) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) return;

    await user.updateDisplayName(_nameController.text.trim());
    String? base64Image;
    if (_profileImage != null) {
      final bytes = await _profileImage!.readAsBytes();
      base64Image = base64Encode(bytes);
    }

    final sessionId = await SessionManager.getSessionId();
    final model = UserModel(
      id: user.uid,
      displayName: _nameController.text.trim(),
      email: _emailController.text.trim(),
      phoneNumber: null,
      profilePicture: base64Image,
      sessionId: sessionId,
    );

    try {
      // Send OTP FIRST — invite_code authorizes the request while still unused.
      debugPrint('[EpicVerse][REG] Step 3: POST /auth/send-otp identifier=${model.email}');
      final otpRes = await _dio.post(
        '${ApiConfig.apiUrl}/auth/send-otp',
        data: FormData.fromMap({'identifier': model.email, 'invite_code': inviteCode}),
        options: Options(headers: ApiConfig.headers),
      );
      debugPrint('[EpicVerse][REG] /auth/send-otp status=${otpRes.statusCode}');

      if (!_emailVerified) {
        // Legacy path: send OTP now (invite_code authorizes the request)
        debugPrint('[EpicVerse][REG] Step 3: POST /auth/send-otp identifier=${model.email}');
        final otpRes = await _dio.post(
          '${ApiConfig.apiUrl}/auth/send-otp',
          data: FormData.fromMap({'identifier': model.email, 'invite_code': inviteCode}),
          options: Options(headers: ApiConfig.headers),
        );
        debugPrint('[EpicVerse][REG] /auth/send-otp status=${otpRes.statusCode}');
      }

      // Sync user — this consumes (marks used) the invite code.
      debugPrint('[EpicVerse][REG] Step 4: POST /sync-user uid=${user.uid}');
      final syncRes = await _dio.post('${ApiConfig.apiUrl}/sync-user', data: {
        "firebase_id": user.uid,
        "display_name": model.displayName,
        "email": model.email,
        "phone_number": null,
        "invite_code": inviteCode,
        "profile_picture": base64Image,
        "session_id": sessionId,
      }, options: Options(headers: await ApiConfig.authHeaders()));
      debugPrint('[EpicVerse][REG] /sync-user status=${syncRes.statusCode}');

      ref.read(userProvider.notifier).setUser(model);
      if (mounted) {
        final navigator = Navigator.of(context);
        if (_emailVerified) {
          // Email already verified inline — mark in DB and go straight to Dashboard
          debugPrint('[EpicVerse][REG] Email pre-verified → mark-verified → Dashboard');
          try {
            await _dio.post(
              '${ApiConfig.apiUrl}/auth/mark-verified',
              options: Options(headers: await ApiConfig.authHeaders()),
            );
          } catch (e) {
            debugPrint('[EpicVerse][REG] mark-verified error (non-fatal): $e');
          }
          navigator.pushAndRemoveUntil(
            MaterialPageRoute(builder: (_) => const DashboardScreen()),
            (route) => false,
          );
        } else {
          navigator.push(MaterialPageRoute(
            builder: (_) => OtpVerificationScreen(
              email: model.email,
              onBack: () => navigator.pop(),
              onVerified: () {
                debugPrint('[EpicVerse][REG] OTP verified → Dashboard');
                navigator.pushAndRemoveUntil(
                  MaterialPageRoute(builder: (_) => const DashboardScreen()),
                  (route) => false,
                );
              },
            ),
          ));
        }
      }
    } catch (e) {
      debugPrint('[EpicVerse][REG] _completeRegistration error: $e');
      _showError("Registration failed: $e");
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message), backgroundColor: AppColors.error));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              _buildAppBar(),
              Expanded(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.all(24),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      children: [
                        _buildProfilePicker(),
                        _buildFieldLabel('DISPLAY NAME'),
                        _buildTextField(_nameController, 'Your Name', Icons.person_outline),
                        const SizedBox(height: 20),
                        _buildFieldLabel('EMAIL'),
                        _buildEmailField(),
                        if (_showOtpRow) ...[
                          const SizedBox(height: 12),
                          _buildInlineOtpRow(),
                        ],
                        if (_emailOtpError != null)
                          Padding(
                            padding: const EdgeInsets.only(top: 6, left: 4),
                            child: Text(_emailOtpError!, style: const TextStyle(color: Colors.redAccent, fontSize: 12)),
                          ),
                        const SizedBox(height: 20),
                        _buildFieldLabel('PASSWORD'),
                        TextFormField(
                          controller: _passwordController,
                          obscureText: _obscurePassword,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildDecoration('******', Icons.lock_outline).copyWith(
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscurePassword ? Icons.visibility_off : Icons.visibility,
                                color: Colors.white54,
                              ),
                              onPressed: () => setState(() => _obscurePassword = !_obscurePassword),
                            ),
                          ),
                          validator: (v) => (v == null || v.isEmpty) ? 'Required' : null,
                        ),
                        const SizedBox(height: 20),
                        _buildFieldLabel('INVITE CODE'),
                        _buildTextField(_inviteController, 'XXXXXX', Icons.vpn_key_outlined, prefix: 'EPIC-'),
                        const SizedBox(height: 20),
                        _buildTermsAndPrivacyRow(),
                        const SizedBox(height: 20),
                        _buildSubmitButton(),
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

  Widget _buildAppBar() => AppBar(
    backgroundColor: Colors.transparent,
    elevation: 0,
    title: const Text('Create Profile', style: TextStyle(fontWeight: FontWeight.bold)),
  );

  Widget _buildProfilePicker() => Center(
    child: Stack(
      children: [
        CircleAvatar(
          radius: 60,
          backgroundImage: _profileImage != null ? FileImage(_profileImage!) : null,
          child: _profileImage == null ? const Icon(Icons.person, size: 50) : null,
        ),
        Positioned(
          bottom: 0, right: 0,
          child: CircleAvatar(
            backgroundColor: AppColors.primaryGold,
            child: IconButton(
              icon: const Icon(Icons.camera_alt, size: 20, color: Colors.black),
              onPressed: _pickImage,
            ),
          ),
        ),
      ],
    ),
  );

  Widget _buildTextField(TextEditingController controller, String hint, IconData icon,
      {bool obscure = false, TextInputType type = TextInputType.text, String? prefix}) {
    return TextFormField(
      controller: controller,
      obscureText: obscure,
      keyboardType: type,
      style: const TextStyle(color: Colors.white),
      decoration: _buildDecoration(hint, icon, prefix: prefix),
      validator: (v) => (v == null || v.isEmpty) ? 'Required' : null,
    );
  }

  InputDecoration _buildDecoration(String hint, IconData icon, {String? prefix}) => InputDecoration(
    hintText: hint,
    prefixIcon: prefix != null
        ? Row(mainAxisSize: MainAxisSize.min, children: [
            const SizedBox(width: 12), Icon(icon), const SizedBox(width: 8), Text(prefix), const SizedBox(width: 4),
          ])
        : Icon(icon),
    filled: true,
    fillColor: Colors.white.withValues(alpha: 0.05),
    border: OutlineInputBorder(borderRadius: BorderRadius.circular(15)),
  );

  Widget _buildSubmitButton() => GestureDetector(
    onTap: _isLoading ? null : _submitForm,
    child: Container(
      width: double.infinity,
      height: 60,
      decoration: BoxDecoration(color: AppColors.primaryGold, borderRadius: BorderRadius.circular(15)),
      alignment: Alignment.center,
      child: _isLoading
          ? const CircularProgressIndicator()
          : const Text('GET STARTED', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
    ),
  );

  Widget _buildFieldLabel(String l) => Align(
    alignment: Alignment.centerLeft,
    child: Padding(
      padding: const EdgeInsets.only(bottom: 8, left: 4),
      child: Text(l, style: const TextStyle(color: AppColors.primaryGold, fontSize: 10, fontWeight: FontWeight.bold)),
    ),
  );

  Widget _buildEmailField() {
    return TextFormField(
      controller: _emailController,
      keyboardType: TextInputType.emailAddress,
      enabled: !_emailVerified,
      style: const TextStyle(color: Colors.white),
      decoration: _buildDecoration('your@email.com', Icons.mail_outline).copyWith(
        suffixIcon: _emailVerified
            ? const Padding(
                padding: EdgeInsets.only(right: 12),
                child: Icon(Icons.verified, color: Colors.greenAccent, size: 22),
              )
            : _isVerifyingEmail
                ? const Padding(
                    padding: EdgeInsets.all(14),
                    child: SizedBox(
                      width: 18, height: 18,
                      child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.primaryGold),
                    ),
                  )
                : TextButton(
                    onPressed: _sendEmailOtp,
                    child: const Text('VERIFY', style: TextStyle(color: AppColors.primaryGold, fontWeight: FontWeight.bold, fontSize: 13)),
                  ),
      ),
      validator: (v) => (v == null || v.isEmpty) ? 'Required' : null,
    );
  }

  Widget _buildInlineOtpRow() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Text('Enter the code sent to your email',
            style: TextStyle(color: AppColors.textMuted, fontSize: 12)),
        const SizedBox(height: 8),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: List.generate(6, (i) => SizedBox(
            width: 42,
            child: TextField(
              controller: _otpControllers[i],
              focusNode: _otpFocusNodes[i],
              textAlign: TextAlign.center,
              keyboardType: TextInputType.number,
              maxLength: 1,
              style: const TextStyle(color: Colors.white, fontSize: 20, fontWeight: FontWeight.bold),
              decoration: InputDecoration(
                counterText: '',
                filled: true,
                fillColor: Colors.white.withValues(alpha: 0.05),
                enabledBorder: OutlineInputBorder(
                  borderSide: BorderSide(color: Colors.white.withValues(alpha: 0.3)),
                  borderRadius: BorderRadius.circular(8),
                ),
                focusedBorder: const OutlineInputBorder(
                  borderSide: BorderSide(color: AppColors.primaryGold, width: 2),
                  borderRadius: BorderRadius.all(Radius.circular(8)),
                ),
              ),
              onChanged: (value) {
                if (value.isNotEmpty && i < 5) _otpFocusNodes[i + 1].requestFocus();
                if (value.isEmpty && i > 0) _otpFocusNodes[i - 1].requestFocus();
                if (_otpControllers.every((c) => c.text.isNotEmpty)) _verifyEmailOtp();
              },
            ),
          )),
        ),
      ],
    );
  }

  Widget _buildTermsAndPrivacyRow() => Column(
    children: [
      Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Checkbox(
            value: _acceptedPrivacy,
            onChanged: (v) => setState(() => _acceptedPrivacy = v ?? false),
            activeColor: AppColors.primaryGold,
            checkColor: Colors.black,
          ),
          Expanded(
            child: GestureDetector(
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => const LegalContentScreen(
                    title: 'Privacy Policy',
                    endpoint: '/legal/privacy',
                  ),
                ),
              ),
              child: RichText(
                text: TextSpan(
                  style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                  children: [
                    const TextSpan(text: 'I agree to the '),
                    TextSpan(
                      text: 'Privacy Policy',
                      style: TextStyle(color: AppColors.primaryGold, fontWeight: FontWeight.bold, decoration: TextDecoration.underline),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
      Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Checkbox(
            value: _acceptedTerms,
            onChanged: (v) => setState(() => _acceptedTerms = v ?? false),
            activeColor: AppColors.primaryGold,
            checkColor: Colors.black,
          ),
          Expanded(
            child: GestureDetector(
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(
                  builder: (_) => const LegalContentScreen(
                    title: 'Terms of Service',
                    endpoint: '/legal/terms',
                  ),
                ),
              ),
              child: RichText(
                text: TextSpan(
                  style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                  children: [
                    const TextSpan(text: 'I agree to the '),
                    TextSpan(
                      text: 'Terms of Service',
                      style: TextStyle(color: AppColors.primaryGold, fontWeight: FontWeight.bold, decoration: TextDecoration.underline),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    ],
  );

}
