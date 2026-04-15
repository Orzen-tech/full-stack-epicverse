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

  bool _isLoading = false;
  final Dio _dio = Dio();
  File? _profileImage;
  final ImagePicker _picker = ImagePicker();

  @override
  void dispose() {
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

  Future<void> _showQRScanner() async {
    final result = await showDialog<String>(
      context: context,
      builder: (context) => Scaffold(
        backgroundColor: Colors.black,
        appBar: AppBar(
          backgroundColor: Colors.transparent,
          elevation: 0,
          leading: IconButton(
            icon: const Icon(Icons.close, color: Colors.white),
            onPressed: () => Navigator.pop(context),
          ),
          title: const Text('Scan Invite QR', style: TextStyle(color: Colors.white)),
        ),
        body: MobileScanner(
          onDetect: (capture) {
            final List<Barcode> barcodes = capture.barcodes;
            for (final barcode in barcodes) {
              if (barcode.rawValue != null) {
                Navigator.pop(context, barcode.rawValue);
                break;
              }
            }
          },
        ),
      ),
    );

    if (result != null) {
      _inviteController.text = result;
    }
  }

  Future<void> _submitForm() async {
    if (!_formKey.currentState!.validate()) {
      _showError('Please complete all required fields correctly.');
      return;
    }

    setState(() => _isLoading = true);

    try {
      // 1. Pre-verify Invite Code with Backend
      final inviteCode = _inviteController.text.trim();
      final validateUrl = '${ApiConfig.apiUrl}/validate-invite/$inviteCode';
      
      try {
        final validateResponse = await _dio.get(
          validateUrl,
          options: Options(headers: ApiConfig.headers),
        );
        if (validateResponse.statusCode != 200) {
          throw Exception(validateResponse.data['detail'] ?? "Invalid invite code");
        }
      } on DioException catch (de) {
        throw Exception(de.response?.data['detail'] ?? "Invite code verification failed");
      }

      // 2. Register with Firebase Auth
      final UserCredential credential = await FirebaseAuth.instance.createUserWithEmailAndPassword(
        email: _emailController.text.trim(),
        password: _passwordController.text.trim(),
      );

      final firebaseUser = credential.user;
      if (firebaseUser == null) throw Exception("Firebase registration failed");

      // Update Firebase Profile Display Name
      await firebaseUser.updateDisplayName(_nameController.text.trim());

      String? base64Image;
      if (_profileImage != null) {
        final bytes = await _profileImage!.readAsBytes();
        base64Image = base64Encode(bytes);
      }

      final sessionId = await SessionManager.getSessionId();
      final newUser = UserModel(
        id: firebaseUser.uid,
        displayName: _nameController.text.trim(),
        email: _emailController.text.trim(),
        primaryLanguage: 'English', // Defaulting to English since we swapped the UI
        preferredLanguages: ['English'],
        profilePicture: base64Image,
        sessionId: sessionId,
      );

      // 3. Sync with Backend SQL Database (Includes Invite Code Consumption)
      try {
        final backendUrl = '${ApiConfig.apiUrl}/sync-user';
        await _dio.post(
          backendUrl,
          data: {
            "firebase_id": firebaseUser.uid,
            "display_name": newUser.displayName,
            "email": newUser.email,
            "primary_language": newUser.primaryLanguage,
            "invite_code": inviteCode,
            "profile_picture": newUser.profilePicture,
            "session_id": sessionId,
          },
          options: Options(headers: ApiConfig.headers),
        );
      } catch (e) {
        // [FAIL-SAFE] If Postgres sync fails, delete the Firebase account so the state isn't broken
        await firebaseUser.delete();
        throw Exception("Database synchronization failed. Please try again or use a different invite code. Details: $e");
      }

      // 4. Update local state and Navigate
      ref.read(userProvider.notifier).setUser(newUser);

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const DashboardScreen()),
      );
    } on FirebaseAuthException catch (e) {
      _showError(e.message ?? "Authentication failed");
    } catch (e) {
      _showError(e.toString().replaceAll('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text(message),
        backgroundColor: AppColors.error.withOpacity(0.8),
        behavior: SnackBarBehavior.floating,
      ),
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
                  onPressed: () => Navigator.pop(context),
                ),
                title: const Text(
                  'Create Companion Profile',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
                ),
              ),
              Expanded(
                child: SingleChildScrollView(
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 16),
                  child: Form(
                    key: _formKey,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        Center(
                          child: Stack(
                            children: [
                              Container(
                                decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  border: Border.all(color: AppColors.primaryGold.withOpacity(0.5), width: 2),
                                  boxShadow: [
                                    BoxShadow(
                                      color: Colors.black.withOpacity(0.3),
                                      blurRadius: 15,
                                      spreadRadius: 2,
                                    ),
                                  ],
                                ),
                                child: CircleAvatar(
                                  radius: 60,
                                  backgroundColor: AppColors.glassBackground,
                                  backgroundImage: _profileImage != null ? FileImage(_profileImage!) : null,
                                  child: _profileImage == null
                                      ? Icon(Icons.person_outline, size: 50, color: AppColors.primaryGold.withOpacity(0.5))
                                      : null,
                                ),
                              ),
                              Positioned(
                                bottom: 0,
                                right: 0,
                                child: GestureDetector(
                                  onTap: _pickImage,
                                  child: Container(
                                    padding: const EdgeInsets.all(8),
                                    decoration: BoxDecoration(
                                      color: AppColors.primaryGold,
                                      shape: BoxShape.circle,
                                      border: Border.all(color: Colors.white, width: 2),
                                    ),
                                    child: const Icon(Icons.camera_alt, size: 20, color: Colors.black),
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 32),
                        
                        _buildFieldLabel('DISPLAY NAME'),
                        TextFormField(
                          controller: _nameController,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildGlassInputDecoration(
                            hintText: 'Enter your name',
                            icon: Icons.person_outline,
                          ),
                          validator: (value) => (value == null || value.trim().isEmpty) ? 'Name is required' : null,
                        ),
                        
                        const SizedBox(height: 24),
                        _buildFieldLabel('EMAIL'),
                        TextFormField(
                          controller: _emailController,
                          style: const TextStyle(color: Colors.white),
                          keyboardType: TextInputType.emailAddress,
                          decoration: _buildGlassInputDecoration(
                            hintText: 'your@email.com',
                            icon: Icons.mail_outline,
                          ),
                          validator: (value) {
                            if (value == null || value.trim().isEmpty) return 'Email is required';
                            if (!RegExp(r'^[\w-\.]+@([\w-]+\.)+[\w-]{2,4}$').hasMatch(value.trim())) return 'Invalid email';
                            return null;
                          },
                        ),
                        
                        const SizedBox(height: 24),
                        _buildFieldLabel('PASSWORD'),
                        TextFormField(
                          controller: _passwordController,
                          obscureText: true,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildGlassInputDecoration(
                            hintText: 'Minimum 6 characters',
                            icon: Icons.lock_outline,
                          ),
                          validator: (value) => (value == null || value.length < 6) ? 'Min 6 characters' : null,
                        ),
                        
                        const SizedBox(height: 24),
                        _buildFieldLabel('UNIQUE INVITE CODE'),
                        TextFormField(
                          controller: _inviteController,
                          style: const TextStyle(color: Colors.white, fontWeight: FontWeight.bold, letterSpacing: 2),
                          decoration: _buildGlassInputDecoration(
                            hintText: 'Enter code or scan QR',
                            icon: Icons.vpn_key_outlined,
                            suffixIcon: IconButton(
                              icon: const Icon(Icons.qr_code_scanner, color: AppColors.primaryGold),
                              onPressed: _showQRScanner,
                            ),
                          ),
                          validator: (value) => (value == null || value.trim().isEmpty) ? 'Invite code required' : null,
                        ),
                        
                        const SizedBox(height: 48),
                        GestureDetector(
                          onTap: _isLoading ? null : _submitForm,
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
                              border: Border.all(color: Colors.white.withOpacity(0.1)),
                              boxShadow: [
                                BoxShadow(
                                  color: Colors.black.withOpacity(0.3),
                                  blurRadius: 15,
                                  offset: const Offset(0, 8),
                                ),
                              ],
                            ),
                            alignment: Alignment.center,
                            child: _isLoading 
                              ? const CircularProgressIndicator(color: Colors.white)
                              : const Text(
                                  'Create Companion Profile',
                                  style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
                                ),
                          ),
                        ),
                        const SizedBox(height: 24),
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

  InputDecoration _buildGlassInputDecoration({required String hintText, required IconData icon, Widget? suffixIcon}) {
    return InputDecoration(
      hintText: hintText,
      hintStyle: const TextStyle(color: AppColors.textMuted, fontSize: 13),
      prefixIcon: Icon(icon, color: AppColors.primaryGold.withOpacity(0.7), size: 20),
      suffixIcon: suffixIcon,
      filled: true,
      fillColor: AppColors.glassBackground,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: AppColors.glassBorder)),
      enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: AppColors.glassBorder)),
      focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: BorderSide(color: AppColors.primaryGold.withOpacity(0.5), width: 1.5)),
      errorBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.error, width: 1)),
      errorStyle: const TextStyle(color: AppColors.error, fontSize: 12),
    );
  }

  Widget _buildFieldLabel(String label) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10.0, left: 4),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Text(
          label,
          style: const TextStyle(color: AppColors.primaryGold, fontSize: 10, fontWeight: FontWeight.w800, letterSpacing: 1.5),
        ),
      ),
    );
  }
}
