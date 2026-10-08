import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:dio/dio.dart';
import 'package:image_picker/image_picker.dart';
import 'dart:async';
import 'dart:convert';
import 'dart:io';
import '../../providers/user_provider.dart';
import '../../models/user_model.dart';
import '../../core/network/api_client.dart';
import '../../core/network/session_manager.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'dashboard_screen.dart';
import 'legal_content_screen.dart';
import '../../core/errors/app_exception.dart';
import '../../core/errors/error_handler.dart';
import '../../core/security/email_verification_flow.dart';
import '../widgets/security_alert_dialog.dart';
import '../../core/utils/password_validator.dart';
import '../widgets/password_requirements.dart';

class CreateProfileScreen extends ConsumerStatefulWidget {
  // True only when login has already signed in the Firebase user, completed
  // the server OTP for that user's email, and found no backend profile.
  final bool emailAlreadyVerified;

  // F-09 H1: the one-time proof /auth/verify-otp returned for this email.
  // Memory only: it is handed over through the constructor (never stored,
  // logged or put in a URL) and dropped as soon as it has been presented.
  final String? emailVerificationProof;

  const CreateProfileScreen({
    super.key,
    this.emailAlreadyVerified = false,
    this.emailVerificationProof,
  });

  @override
  ConsumerState<CreateProfileScreen> createState() => _CreateProfileScreenState();
}

class _CreateProfileScreenState extends ConsumerState<CreateProfileScreen> {
  final _formKey = GlobalKey<FormState>();
  final TextEditingController _nameController = TextEditingController();
  final TextEditingController _emailController = TextEditingController();
  final TextEditingController _passwordController = TextEditingController();
  final TextEditingController _confirmPasswordController = TextEditingController();
  final TextEditingController _inviteController = TextEditingController();

  bool _obscurePassword = true;
  bool _obscureConfirmPassword = true;
  bool _isInviteValid = false;
  String? _inviteFeedback;
  bool _isLoading = false;
  bool _acceptedPrivacy = false;
  bool _acceptedTerms = false;
  File? _profileImage;
  final ImagePicker _picker = ImagePicker();

  // Email inline verification
  bool _emailVerified = false;
  // One-time proof for the verified email (F-09 H1). Memory only; cleared when
  // the email changes, when the screen is disposed and once it has been used.
  String? _emailProof;
  bool _isVerifyingEmail = false;
  bool _showOtpRow = false;
  String? _emailOtpError;
  int _resendCooldown = 0;
  Timer? _cooldownTimer;
  final FocusNode _emailFocusNode = FocusNode();
  final List<TextEditingController> _otpControllers =
      List.generate(6, (_) => TextEditingController());
  final List<FocusNode> _otpFocusNodes =
      List.generate(6, (_) => FocusNode());

  bool get _isRecovery => widget.emailAlreadyVerified;

  @override
  void initState() {
    super.initState();
    if (_isRecovery) {
      // Set before the email listener is attached so it isn't reset.
      _emailController.text = FirebaseAuth.instance.currentUser?.email ?? '';
      // Login only gets here after the server accepted this email's OTP. The
      // proof is optional (a pre-H1 backend returns none): the server, not
      // this flag, decides, and a missing/expired proof falls back to the
      // authenticated verification flow before the Dashboard opens.
      _emailProof = widget.emailVerificationProof;
      _emailVerified = _emailController.text.isNotEmpty;
    }
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
      final response = await apiClient.get('/validate-invite/$inviteCode');
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
      final response = await apiClient.get('/validate-invite/$inviteCode');
      return response.statusCode == 200 && response.data['valid'] == true;
    } catch (_) {
      return false;
    }
  }

  @override
  void dispose() {
    _emailProof = null;
    _cooldownTimer?.cancel();
    _inviteController.removeListener(_onInviteChanged);
    _emailController.removeListener(_onEmailChanged);
    _nameController.dispose();
    _emailController.dispose();
    _passwordController.dispose();
    _confirmPasswordController.dispose();
    _inviteController.dispose();
    for (final c in _otpControllers) c.dispose();
    for (final n in _otpFocusNodes) n.dispose();
    _emailFocusNode.dispose();
    super.dispose();
  }

  void _startCooldown() {
    setState(() => _resendCooldown = 60);
    _cooldownTimer?.cancel();
    _cooldownTimer = Timer.periodic(const Duration(seconds: 1), (t) {
      if (!mounted) { t.cancel(); return; }
      setState(() {
        _resendCooldown--;
        if (_resendCooldown <= 0) t.cancel();
      });
    });
  }

  void _onEmailChanged() {
    // A proof is valid only for the exact email it was issued for.
    _emailProof = null;
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
      await apiClient.post(
        '/auth/send-email-otp',
        data: FormData.fromMap({'identifier': email}),
      );
      setState(() => _showOtpRow = true);
      _startCooldown();
    } on AppException catch (e) {
      if (e.statusCode == 429) {
        final original = e.originalException;
        final retryAfterStr = original is DioException
            ? original.response?.headers.value('retry-after')
            : null;
        final retrySeconds = int.tryParse(retryAfterStr ?? '') ?? 600;
        final retryMins = (retrySeconds / 60).ceil();
        setState(() => _resendCooldown = retrySeconds);
        _cooldownTimer?.cancel();
        _cooldownTimer = Timer.periodic(const Duration(seconds: 1), (t) {
          if (!mounted) { t.cancel(); return; }
          setState(() {
            _resendCooldown--;
            if (_resendCooldown <= 0) t.cancel();
          });
        });
        _showError('Too many attempts. Please wait $retryMins minute${retryMins == 1 ? '' : 's'} before trying again.');
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
      final result = await EmailOtp.verify(_emailController.text.trim(), otp);
      if (result.error != null) {
        setState(() => _emailOtpError = 'Invalid or expired code. Try again.');
        for (final c in _otpControllers) c.clear();
        _otpFocusNodes[0].requestFocus();
        return;
      }
      setState(() {
        _emailVerified = true;
        _showOtpRow = false;
        // Held in memory only. Null from a pre-H1 backend, which verifies
        // server-side instead.
        _emailProof = result.proof;
      });
      for (final c in _otpControllers) c.clear();
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
    if (_isLoading) return; // a signup (or its device check) is already running
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
      // Finding #4: re-check the device before creating the account (fresh check;
      // only a confirmed compromise blocks). Inside the try so `finally` always
      // clears the loading state, whether this returns, continues or throws.
      if (await blockIfCompromised(context)) return;

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

      final email = _emailController.text.trim();
      final signedIn = FirebaseAuth.instance.currentUser;

      // Step 2a: Firebase account already signed in for this email (login
      // recovery, or an earlier signup that never reached /sync-user).
      if (signedIn != null &&
          (signedIn.email ?? '').toLowerCase() == email.toLowerCase()) {
        debugPrint('[EpicVerse][REG] Step 2: existing signed-in Firebase user');
        await _completeExistingFirebaseUser(inviteCode);
        return;
      }

      // Step 2b: Create Firebase account
      debugPrint('[EpicVerse][REG] Step 2: createUserWithEmailAndPassword');
      try {
        final cred = await FirebaseAuth.instance.createUserWithEmailAndPassword(
          email: email,
          password: _passwordController.text.trim(),
        );
        debugPrint('[EpicVerse][REG] Firebase user created successfully');
        await _completeRegistration(cred, inviteCode);
        return;
      } on FirebaseAuthException catch (e) {
        if (e.code != 'email-already-in-use') rethrow;
      }

      // Step 2c: Firebase account exists — prove ownership with Firebase
      // itself (the password goes only to Firebase, never our backend).
      debugPrint('[EpicVerse][REG] email-already-in-use → Firebase sign-in');
      try {
        await FirebaseAuth.instance.signInWithEmailAndPassword(
          email: email,
          password: _passwordController.text.trim(),
        );
      } on FirebaseAuthException catch (e) {
        debugPrint('[EpicVerse][REG] recovery sign-in failed code=${e.code}');
        setState(() => _emailVerified = false);
        _showError('This email is already registered. Please log in instead.');
        return;
      }
      await _completeExistingFirebaseUser(inviteCode);
    } on FirebaseAuthException catch (e) {
      debugPrint('[EpicVerse][REG] FirebaseAuthException code=${e.code}');
      _showError(e.message ?? 'Registration failed.');
    } catch (e) {
      debugPrint('[EpicVerse][REG] Registration error: ${e.runtimeType}');
      _showError(e.toString());
    } finally {
      if (mounted) setState(() => _isLoading = false);
    }
  }

  /// Existing Firebase user: create the backend profile only if it is
  /// missing. An account that already has one must use normal login, so
  /// /sync-user is never called for it from this screen.
  Future<void> _completeExistingFirebaseUser(String inviteCode) async {
    final user = FirebaseAuth.instance.currentUser;
    if (user == null) return;
    try {
      await apiClient.get('/user/${user.uid}');
      debugPrint('[EpicVerse][REG] Profile already exists → sign out');
      await FirebaseAuth.instance.signOut();
      _showError('This email is already registered. Please log in instead.');
      return;
    } on AppException catch (e) {
      if (e.statusCode != 404) {
        debugPrint('[EpicVerse][REG] Profile check failed status=${e.statusCode}');
        _showError('Could not reach the server. Please try again.');
        return;
      }
    }
    debugPrint('[EpicVerse][REG] Profile missing → completing registration');
    await _completeRegistration(null, inviteCode);
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
      // Sync user — consumes (marks used) the invite code.
      debugPrint('[EpicVerse][REG] Step 3: POST /sync-user');
      await apiClient.post('/sync-user', data: {
        "firebase_id": user.uid,
        "display_name": model.displayName,
        "email": model.email,
        "phone_number": null,
        "invite_code": inviteCode,
        "profile_picture": base64Image,
        "session_id": sessionId,
      });
      debugPrint('[EpicVerse][REG] /sync-user OK');

      // F-09 H1: present the one-time proof. The server takes UID and email
      // from the Firebase token only. The proof is dropped here whether or
      // not it worked: it is single-use and must not outlive this call.
      final proof = _emailProof;
      _emailProof = null;
      var verified = false;
      try {
        await apiClient.post(
          '/auth/mark-verified',
          data: proof == null ? null : FormData.fromMap({'proof': proof}),
        );
        verified = true;
        debugPrint('[EpicVerse][REG] mark-verified OK');
      } catch (e) {
        // Never print the exception: the request carried the proof.
        debugPrint('[EpicVerse][REG] mark-verified failed: ${e.runtimeType}');
      }
      if (!verified) {
        // No proof (or it expired / was already used): verify through the
        // authenticated flow BEFORE the Dashboard can open.
        if (!mounted) return;
        verified = await EmailVerificationFlow.run(Navigator.of(context));
      }
      if (!verified) {
        debugPrint('[EpicVerse][REG] email not verified → sign out');
        await FirebaseAuth.instance.signOut();
        _showError('Your account was created, but your email is not verified yet. '
            'Please log in to finish verifying it.');
        return;
      }

      ref.read(userProvider.notifier).setUser(model);
      // Profile is now confirmed to exist in the backend — safe to persist
      // login state (mirrors the same guard added to LoginScreen).
      final prefs = await SharedPreferences.getInstance();
      await prefs.setBool('isLoggedIn', true);
      if (mounted) {
        Navigator.of(context).pushAndRemoveUntil(
          MaterialPageRoute(builder: (_) => const DashboardScreen()),
          (route) => false,
        );
      }
    } catch (e, st) {
      debugPrint('[EpicVerse][REG] _completeRegistration error: ${e.runtimeType}');
      if (mounted) {
        ErrorHandler.handleError(
          e,
          stackTrace: st,
          context: context,
          screenName: 'CreateProfileScreen',
        );
      }
    }
  }

  void _showError(String message) {
    if (!mounted) return;
    ErrorHandler.showErrorSnackBar(message, context: context);
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
                        if (!_isRecovery) ...[
                        const SizedBox(height: 20),
                        _buildFieldLabel('PASSWORD'),
                        TextFormField(
                          controller: _passwordController,
                          obscureText: _obscurePassword,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildDecoration('••••••••', Icons.lock_outline).copyWith(
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscurePassword ? Icons.visibility_off : Icons.visibility,
                                color: Colors.white54,
                              ),
                              onPressed: () => setState(() => _obscurePassword = !_obscurePassword),
                            ),
                          ),
                          // Live-refresh the requirements checklist and the
                          // submit button as the user types.
                          onChanged: (_) => setState(() {}),
                          validator: (v) {
                            final result = PasswordValidator.validate(v);
                            if (result != null) return result;
                            return null;
                          },
                        ),
                        PasswordRequirementsChecklist(
                          password: _passwordController.text,
                        ),
                        const SizedBox(height: 20),
                        _buildFieldLabel('CONFIRM PASSWORD'),
                        TextFormField(
                          controller: _confirmPasswordController,
                          obscureText: _obscureConfirmPassword,
                          style: const TextStyle(color: Colors.white),
                          decoration: _buildDecoration('••••••••', Icons.lock_outline).copyWith(
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscureConfirmPassword ? Icons.visibility_off : Icons.visibility,
                                color: Colors.white54,
                              ),
                              onPressed: () => setState(() => _obscureConfirmPassword = !_obscureConfirmPassword),
                            ),
                          ),
                          onChanged: (_) => setState(() {}),
                          validator: (v) {
                            if (v == null || v.isEmpty) return 'Required';
                            if (v != _passwordController.text) return 'Passwords do not match';
                            return null;
                          },
                        ),
                        ],
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

  Widget _buildSubmitButton() {
    // Gate the button on the password policy (client-side UX only — the
    // existing form validators and Firebase still enforce the policy on
    // submit). Other required fields keep their own in-flow checks in
    // _submitForm.
    final password = _passwordController.text;
    // Recovery mode has no password fields: the Firebase user is already
    // signed in and no account is created.
    final passwordReady = _isRecovery ||
        (PasswordValidator.meetsPolicy(password) &&
            password == _confirmPasswordController.text);
    final enabled = !_isLoading && passwordReady;

    return GestureDetector(
      onTap: enabled ? _submitForm : null,
      child: Container(
        width: double.infinity,
        height: 60,
        decoration: BoxDecoration(
          color: enabled
              ? AppColors.primaryGold
              : AppColors.primaryGold.withValues(alpha: 0.4),
          borderRadius: BorderRadius.circular(15),
        ),
        alignment: Alignment.center,
        child: _isLoading
            ? const CircularProgressIndicator(color: Colors.black)
            : const Text('GET STARTED', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
      ),
    );
  }

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
      focusNode: _emailFocusNode,
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
                : Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 10),
                    child: GestureDetector(
                      onTap: _sendEmailOtp,
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                        decoration: BoxDecoration(
                          color: AppColors.primaryGold,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: const Text('VERIFY', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold, fontSize: 12)),
                      ),
                    ),
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
        const SizedBox(height: 10),
        Center(
          child: _resendCooldown > 0
              ? RichText(
                  text: TextSpan(
                    style: const TextStyle(fontSize: 12),
                    children: [
                      const TextSpan(
                        text: 'Resend code in ',
                        style: TextStyle(color: AppColors.textMuted),
                      ),
                      TextSpan(
                        text: '${(_resendCooldown ~/ 60).toString().padLeft(2, '0')}:${(_resendCooldown % 60).toString().padLeft(2, '0')}',
                        style: const TextStyle(
                          color: AppColors.primaryGold,
                          fontWeight: FontWeight.bold,
                          fontFeatures: [FontFeature.tabularFigures()],
                        ),
                      ),
                    ],
                  ),
                )
              : GestureDetector(
                  onTap: _isVerifyingEmail ? null : _sendEmailOtp,
                  child: const Text(
                    'Resend code',
                    style: TextStyle(
                      color: AppColors.primaryGold,
                      fontSize: 12,
                      decoration: TextDecoration.underline,
                    ),
                  ),
                ),
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
