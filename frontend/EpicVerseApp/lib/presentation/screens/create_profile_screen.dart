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

  bool _isInviteValid = false;
  String? _inviteFeedback;
  bool _isLoading = false;
  bool _acceptedPrivacy = false;
  bool _acceptedTerms = false;
  final Dio _dio = Dio();
  File? _profileImage;
  final ImagePicker _picker = ImagePicker();

  @override
  void initState() {
    super.initState();
    _inviteController.addListener(_onInviteChanged);
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
    _nameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    _inviteController.dispose();
    super.dispose();
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

      // Then sync user — this consumes (marks used) the invite code.
      debugPrint('[EpicVerse][REG] Step 4: POST /sync-user uid=${user.uid}');
      final syncRes = await _dio.post('${ApiConfig.apiUrl}/sync-user', data: {
        "firebase_id": user.uid,
        "display_name": model.displayName,
        "email": model.email,
        "phone_number": null,
        "invite_code": inviteCode,
        "profile_picture": base64Image,
        "session_id": sessionId,
      }, options: Options(headers: ApiConfig.headers));
      debugPrint('[EpicVerse][REG] /sync-user status=${syncRes.statusCode}');

      ref.read(userProvider.notifier).setUser(model);
      if (mounted) {
        // Capture NavigatorState before pushReplacement so the onVerified
        // callback can still navigate after this screen is disposed.
        final navigator = Navigator.of(context);
        navigator.pushReplacement(MaterialPageRoute(
          builder: (_) => OtpVerificationScreen(
            email: model.email,
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
                        _buildTextField(_emailController, 'your@email.com', Icons.mail_outline, type: TextInputType.emailAddress),
                        const SizedBox(height: 20),
                        _buildFieldLabel('PASSWORD'),
                        _buildTextField(_passwordController, '******', Icons.lock_outline, obscure: true),
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
