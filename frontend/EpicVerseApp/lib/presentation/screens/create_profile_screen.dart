import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../../core/constants/app_assets.dart';
import '../widgets/network_background.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import 'package:image_picker/image_picker.dart';
import 'dart:convert';
import 'dart:io';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import 'dashboard_screen.dart';
import '../../core/network/api_config.dart';

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

  final List<String> _languages = [
    'English', 'Hindi', 'Tamil', 'Telugu', 'Bengali', 'Marathi', 'Gujarati', 
    'Kannada', 'Malayalam', 'Sindhi', 'Punjabi', 'Sanskrit', 'Odia', 'Assamese'
  ];
  String? _primaryLanguage;
  bool _isLoading = false;
  final Dio _dio = Dio();
  File? _profileImage;
  final ImagePicker _picker = ImagePicker();

  @override
  void dispose() {
    _nameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _pickImage() async {
    final XFile? image = await _picker.pickImage(source: ImageSource.gallery, imageQuality: 50);
    if (image != null) {
      setState(() => _profileImage = File(image.path));
    }
  }

  Future<void> _submitForm() async {
    final bool isFormValid = _formKey.currentState!.validate();
    final bool isLanguageValid = _primaryLanguage != null;

    if (!isFormValid || !isLanguageValid) {
      String errorMessage = 'Please complete all required fields correctly.';
      if (isFormValid && !isLanguageValid) {
        errorMessage = 'Please select your primary gameplay language.';
      }

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(errorMessage),
          backgroundColor: AppColors.error.withOpacity(0.8),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }

    setState(() => _isLoading = true);

    try {
      // 1. Register with Firebase Auth
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

      final newUser = UserModel(
        id: firebaseUser.uid,
        displayName: _nameController.text.trim(),
        email: _emailController.text.trim(),
        primaryLanguage: _primaryLanguage!,
        preferredLanguages: [_primaryLanguage!],
        profilePicture: base64Image,
      );

      // 2. Sync with Backend SQL Database
      final backendUrl = '${ApiConfig.apiUrl}/sync-user';
      await _dio.post(
        backendUrl,
        data: {
          "firebase_id": firebaseUser.uid,
          "display_name": newUser.displayName,
          "email": newUser.email,
          "primary_language": newUser.primaryLanguage,
          "profile_picture": newUser.profilePicture,
        },
        options: Options(headers: ApiConfig.headers),
      );

      // 3. Update local state and Navigate
      ref.read(userProvider.notifier).setUser(newUser);

      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const DashboardScreen()),
      );
    } on FirebaseAuthException catch (e) {
      String error = e.message ?? "Authentication failed";
      _showError(error);
    } catch (e) {
      _showError("System Error: $e");
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
                        Align(
                          alignment: Alignment.centerLeft,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              const Text(
                                'Your identity in the EpicVerse', 
                                style: TextStyle(color: AppColors.textSecondary, fontSize: 14),
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 32),
                        const SizedBox(height: 32),
                        
                        _buildFieldLabel('DISPLAY NAME'),
                        TextFormField(
                          controller: _nameController,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildGlassInputDecoration(
                            hintText: 'Enter your name',
                            icon: Icons.person_outline,
                          ),
                          validator: (value) {
                            if (value == null || value.trim().isEmpty) return 'Name is required';
                            if (value.trim().length < 2) return 'Minimum 2 characters';
                            return null;
                          },
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
                            if (!RegExp(r'^[\w-\.]+@([\w-]+\.)+[\w-]{2,4}$').hasMatch(value.trim())) {
                              return 'Enter a valid email address';
                            }
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
                          validator: (value) {
                            if (value == null || value.isEmpty) return 'Password is required';
                            if (value.length < 6) return 'Minimum 6 characters';
                            return null;
                          },
                        ),
                        
                        const SizedBox(height: 24),
                        _buildFieldLabel('PRIMARY GAMEPLAY LANGUAGE'),
                        _buildLanguageDropdown(),
                        
                        const SizedBox(height: 48),
                        GestureDetector(
                          onTap: _submitForm,
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
                                  style: TextStyle(
                                    color: Colors.white,
                                    fontSize: 16,
                                    fontWeight: FontWeight.bold,
                                  ),
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

  InputDecoration _buildGlassInputDecoration({required String hintText, required IconData icon}) {
    return InputDecoration(
      hintText: hintText,
      hintStyle: const TextStyle(color: AppColors.textMuted, fontSize: 14),
      prefixIcon: Icon(icon, color: AppColors.primaryGold.withOpacity(0.7), size: 20),
      filled: true,
      fillColor: AppColors.glassBackground,
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 18),
      border: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: AppColors.glassBorder),
      ),
      enabledBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: AppColors.glassBorder),
      ),
      focusedBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: BorderSide(color: AppColors.primaryGold.withOpacity(0.5), width: 1.5),
      ),
      errorBorder: OutlineInputBorder(
        borderRadius: BorderRadius.circular(12),
        borderSide: const BorderSide(color: AppColors.error, width: 1),
      ),
      errorStyle: const TextStyle(color: AppColors.error, fontSize: 12),
    );
  }

  Widget _buildFieldLabel(String label) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 10.0, left: 4),
      child: Text(
        label,
        style: const TextStyle(
          color: AppColors.primaryGold, 
          fontSize: 11, 
          fontWeight: FontWeight.w800, 
          letterSpacing: 1.5,
        ),
      ),
    );
  }

  Widget _buildLanguageDropdown() {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      decoration: BoxDecoration(
        color: AppColors.glassBackground,
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: AppColors.glassBorder),
      ),
      child: DropdownButtonHideUnderline(
        child: DropdownButton<String>(
          value: _primaryLanguage,
          hint: const Text(
            'Select Language',
            style: TextStyle(color: AppColors.textMuted, fontSize: 15),
          ),
          isExpanded: true,
          dropdownColor: AppColors.surface,
          iconEnabledColor: AppColors.primaryGold,
          style: const TextStyle(color: Colors.white, fontSize: 15),
          items: _languages.map((lang) => DropdownMenuItem(
            value: lang, 
            child: Text(lang)
          )).toList(),
          onChanged: (value) {
            if (value != null) {
              setState(() => _primaryLanguage = value);
            }
          },
        ),
      ),
    );
  }
}
