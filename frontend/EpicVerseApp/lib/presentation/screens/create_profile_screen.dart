import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../../core/constants/app_assets.dart';
import '../widgets/network_background.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import 'package:image_picker/image_picker.dart';
import 'package:mobile_scanner/mobile_scanner.dart';
import 'dart:convert';
import 'dart:io';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import 'dashboard_screen.dart';
import '../../core/network/api_config.dart';
import '../../core/network/session_manager.dart';
import 'verification_pending_screen.dart';
import 'welcome_screen.dart';
import 'otp_verification_screen.dart';


class CreateProfileScreen extends ConsumerStatefulWidget {
  const CreateProfileScreen({super.key});

  @override
  ConsumerState<CreateProfileScreen> createState() => _CreateProfileScreenState();
}

class _CreateProfileScreenState extends ConsumerState<CreateProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  final TextEditingController _nameController = TextEditingController();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _phoneController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  final TextEditingController _inviteController = TextEditingController();

  bool _isPhoneMode = true; 
  bool _isInviteValid = false;
  String? _inviteFeedback;
  bool _isLoading = false;
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
    final validateUrl = '${ApiConfig.apiUrl}/validate-invite/$inviteCode';

    try {
      final response = await _dio.get(validateUrl, options: Options(headers: ApiConfig.headers));
      if (response.statusCode == 200 && mounted) {
        setState(() {
          _isInviteValid = true;
          _inviteFeedback = "Valid code";
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
    final validateUrl = '${ApiConfig.apiUrl}/validate-invite/$inviteCode';
    try {
      final response = await _dio.get(validateUrl, options: Options(headers: ApiConfig.headers));
      return response.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  @override
  void dispose() {
    _inviteController.removeListener(_onInviteChanged);
    _nameController.dispose();
    _emailController.dispose();
    _phoneController.dispose();
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

  Future<void> _showQRScanner() async {
    final result = await showDialog<String>(
      context: context,
      builder: (context) => Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          elevation: 0,
          leading: IconButton(icon: const Icon(Icons.close, color: Colors.white), onPressed: () => Navigator.pop(context)),
          title: const Text('Scan Invite QR', style: TextStyle(color: Colors.white)),
        ),
        body: MobileScanner(
          onDetect: (capture) {
            for (final barcode in capture.barcodes) {
              if (barcode.rawValue != null) {
                Navigator.pop(context, barcode.rawValue);
                break;
              }
            }
          },
        ),
      ),
    );
    if (result != null) _inviteController.text = result;
  }

  Widget _buildAuthToggle() {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 20),
      decoration: BoxDecoration(color: Colors.white.withOpacity(0.05), borderRadius: BorderRadius.circular(15)),
      child: Row(
        children: [
          Expanded(child: _toggleButton('Phone', _isPhoneMode, () => setState(() => _isPhoneMode = true))),
          Expanded(child: _toggleButton('Email', !_isPhoneMode, () => setState(() => _isPhoneMode = false))),
        ],
      ),
    );
  }

  Widget _toggleButton(String text, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          color: active ? AppColors.primaryGold : Colors.transparent,
          borderRadius: BorderRadius.circular(15),
        ),
        child: Text(text, textAlign: TextAlign.center, style: TextStyle(color: active ? Colors.black : Colors.white, fontWeight: FontWeight.bold)),
      ),
    );
  }

  Future<void> _submitForm() async {
    if (!_formKey.currentState!.validate()) {
      _showError('Please fill all fields');
      return;
    }
    setState(() => _isLoading = true);
    try {
      String rawCode = _inviteController.text.trim().replaceAll(' ', '');
      if (rawCode.startsWith('EPIC-')) rawCode = rawCode.substring(5);
      final inviteCode = "EPIC-$rawCode";

      // Step 1: Verify invite code BEFORE touching Firebase
      final isValid = await _checkInviteCodeValid(inviteCode);
      if (!isValid) {
        _showError('Invalid invite code. Please check and try again.');
        if (mounted) setState(() => _isInviteValid = false);
        return;
      }

      // Step 2: Invite code is valid — proceed with Firebase auth
      if (_isPhoneMode) {
        await FirebaseAuth.instance.verifyPhoneNumber(
          phoneNumber: _phoneController.text.trim(),
          verificationCompleted: (cred) => _completeRegistration(cred, inviteCode),
          verificationFailed: (e) => _showError(e.message ?? "Phone error"),
          codeSent: (vid, _) => Navigator.push(context, MaterialPageRoute(builder: (_) => OtpVerificationScreen(
            phone: _phoneController.text.trim(),
            verificationId: vid,
            onVerified: () => _completeRegistrationAfterManualOtp(inviteCode),
          ))),
          codeAutoRetrievalTimeout: (_) {},
        );
      } else {
        final cred = await FirebaseAuth.instance.createUserWithEmailAndPassword(
          email: _emailController.text.trim(),
          password: _passwordController.text.trim(),
        );
        await _completeRegistration(cred, inviteCode);
      }
    } catch (e) {
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
      email: _isPhoneMode ? null : _emailController.text.trim(),
      phoneNumber: _isPhoneMode ? _phoneController.text.trim() : null,
      profilePicture: base64Image,
      sessionId: sessionId,
    );

    try {
      await _dio.post('${ApiConfig.apiUrl}/sync-user', data: {
        "firebase_id": user.uid,
        "display_name": model.displayName,
        "email": model.email,
        "phone_number": model.phoneNumber,
        "invite_code": inviteCode,
        "profile_picture": base64Image,
        "session_id": sessionId,
      }, options: Options(headers: ApiConfig.headers));

      ref.read(userProvider.notifier).setUser(model);
      if (mounted) Navigator.pushReplacement(context, MaterialPageRoute(builder: (_) => const DashboardScreen()));
    } catch (e) {
      _showError("Sync failed: $e");
    }
  }

  Future<void> _completeRegistrationAfterManualOtp(String inviteCode) async {
     final user = FirebaseAuth.instance.currentUser;
     if (user != null) await _completeRegistration(null, inviteCode);
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
                        _buildAuthToggle(),
                        const SizedBox(height: 20),
                        _buildFieldLabel('DISPLAY NAME'),
                        _buildTextField(_nameController, 'Your Name', Icons.person_outline),
                        const SizedBox(height: 20),
                        if (_isPhoneMode) ...[
                          _buildFieldLabel('MOBILE NUMBER'),
                          _buildTextField(_phoneController, '+91XXXXXXXXXX', Icons.phone_android_outlined, type: TextInputType.phone),
                        ] else ...[
                          _buildFieldLabel('EMAIL'),
                          _buildTextField(_emailController, 'your@email.com', Icons.mail_outline, type: TextInputType.emailAddress),
                          const SizedBox(height: 20),
                          _buildFieldLabel('PASSWORD'),
                          _buildTextField(_passwordController, '******', Icons.lock_outline, obscure: true),
                        ],
                        const SizedBox(height: 20),
                        _buildFieldLabel('INVITE CODE'),
                        _buildTextField(_inviteController, 'XXXXXX', Icons.vpn_key_outlined, prefix: 'EPIC-'),
                        const SizedBox(height: 40),
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
        CircleAvatar(radius: 60, backgroundImage: _profileImage != null ? FileImage(_profileImage!) : null, child: _profileImage == null ? const Icon(Icons.person, size: 50) : null),
        Positioned(bottom: 0, right: 0, child: CircleAvatar(backgroundColor: AppColors.primaryGold, child: IconButton(icon: const Icon(Icons.camera_alt, size: 20, color: Colors.black), onPressed: _pickImage))),
      ],
    ),
  );

  Widget _buildTextField(TextEditingController controller, String hint, IconData icon, {bool obscure = false, TextInputType type = TextInputType.text, String? prefix}) {
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
    prefixIcon: prefix != null ? Row(mainAxisSize: MainAxisSize.min, children: [const SizedBox(width: 12), Icon(icon), const SizedBox(width: 8), Text(prefix), const SizedBox(width: 4)]) : Icon(icon),
    filled: true,
    fillColor: Colors.white.withOpacity(0.05),
    border: OutlineInputBorder(borderRadius: BorderRadius.circular(15)),
  );

  Widget _buildSubmitButton() => GestureDetector(
    onTap: _isLoading ? null : _submitForm,
    child: Container(
      width: double.infinity, height: 60,
      decoration: BoxDecoration(color: AppColors.primaryGold, borderRadius: BorderRadius.circular(15)),
      alignment: Alignment.center,
      child: _isLoading ? const CircularProgressIndicator() : const Text('GET STARTED', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
    ),
  );

  Widget _buildFieldLabel(String l) => Align(alignment: Alignment.centerLeft, child: Padding(padding: const EdgeInsets.only(bottom: 8, left: 4), child: Text(l, style: const TextStyle(color: AppColors.primaryGold, fontSize: 10, fontWeight: FontWeight.bold))));
}
