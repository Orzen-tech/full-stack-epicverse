import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import '../../core/network/api_config.dart';

class LegalContentScreen extends StatefulWidget {
  final String title;
  final String endpoint;

  const LegalContentScreen({
    super.key,
    required this.title,
    required this.endpoint,
  });

  @override
  State<LegalContentScreen> createState() => _LegalContentScreenState();
}

class _LegalContentScreenState extends State<LegalContentScreen> {
  final Dio _dio = Dio();
  String _content = '';
  bool _isLoading = true;
  String? _error;

  @override
  void initState() {
    super.initState();
    _fetchContent();
  }

  Future<void> _fetchContent() async {
    try {
      final response = await _dio.get(
        '${ApiConfig.apiUrl}${widget.endpoint}',
        options: Options(headers: ApiConfig.headers),
      );
      if (response.statusCode == 200 && response.data is Map) {
        setState(() {
          _content = response.data['content'] ?? 'Content not available';
          _isLoading = false;
        });
      } else {
        setState(() {
          _error = 'Failed to load content';
          _isLoading = false;
        });
      }
    } catch (e) {
      debugPrint('Error fetching legal content: $e');
      // Fallback content
      setState(() {
        _content = _getFallbackContent();
        _isLoading = false;
      });
    }
  }

  String _getFallbackContent() {
    if (widget.endpoint.contains('privacy')) {
      return '''EpicVerse Privacy Policy

1. Data Collection
We collect your email address, display name, profile picture, and app usage data to provide and improve our services.

2. Audio Data
Voice recordings are processed in real-time using OpenAI's Realtime API. Audio is not stored permanently on our servers.

3. Data Usage
Your data is used solely for:
- Account management and authentication
- AI-powered voice interactions
- Service improvements and debugging

4. Third-Party Services
We use the following services:
- Firebase (Authentication, Database)
- OpenAI (AI processing)
- Google Cloud (Hosting, Storage)
- SendGrid (Email delivery)

5. Data Retention
- Active accounts: Data retained while account is active
- Deleted accounts: 30-day grace period, then permanent deletion
- Analytics: Anonymized after 90 days

6. Your Rights
- Access: Request a copy of your data
- Deletion: Delete your account from Settings anytime
- Correction: Update your name in Settings

7. Security
We use industry-standard encryption and secure authentication. No passwords are stored in plain text.

8. Changes
We may update this policy. Continued use after changes constitutes acceptance.

9. Contact
For privacy questions: support@kriyora.com

Last updated: May 2026''';
    } else if (widget.endpoint.contains('ai-disclosure')) {
      return '''AI Usage Disclosure

EpicVerse uses OpenAI's Realtime API to provide AI conversations and voice interactions.

Data Shared
• Voice recordings
• Message transcripts
• User prompts

Purpose
This data is securely sent to OpenAI to generate AI responses in real time.

Data is processed in real-time and not stored by EpicVerse or OpenAI.

EpicVerse does not sell personal data. Data is only used to provide AI functionality.
''';
    } else {
      return '''EpicVerse Terms of Service

1. Acceptance
By accessing or using EpicVerse, you agree to be bound by these Terms. If you disagree, do not use the service.

2. Eligibility
- You must be 13 years or older
- You must have a valid invite code to register
- You may not use the service if prohibited by applicable law

3. Account Requirements
- Provide accurate information
- Maintain account security
- One account per person

4. Acceptable Use
You agree NOT to:
- Use the service for illegal purposes
- Attempt to bypass security measures
- Abuse AI features or generate harmful content
- Share invite codes publicly
- Reverse engineer the application

5. Content & Intellectual Property
- You retain rights to content you create
- You grant us license to process content for service operation
- We retain rights to the EpicVerse brand and software

6. Service Modifications
We may modify or discontinue the service at any time with reasonable notice.

7. Termination
- You may delete your account anytime via Settings
- We may suspend accounts for Terms violations
- Deleted accounts have a 30-day grace period for recovery

8. Disclaimer of Warranty
The service is provided "as is" without warranties of any kind.

9. Limitation of Liability
We are not liable for indirect, incidental, or consequential damages.

10. Governing Law
These Terms are governed by applicable local laws.

11. Changes to Terms
We may update these Terms. Continued use after changes constitutes acceptance.

12. Contact
For questions: support@kriyora.com

Last updated: May 2026''';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.white,
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back, color: Colors.black),
          onPressed: () => Navigator.pop(context),
        ),
        title: Text(
          widget.title,
          style: const TextStyle(
            color: Colors.black,
            fontSize: 20,
            fontWeight: FontWeight.bold,
          ),
        ),
        centerTitle: true,
      ),
      body: _isLoading
          ? const Center(
              child: CircularProgressIndicator(color: Colors.black),
            )
          : _error != null
              ? Center(
                  child: Text(
                    _error!,
                    style: const TextStyle(color: Colors.black),
                  ),
                )
              : SingleChildScrollView(
                  padding: const EdgeInsets.all(24),
                  child: Text(
                    _content,
                    style: const TextStyle(
                      color: Colors.black,
                      fontSize: 16,
                      height: 1.6,
                    ),
                  ),
                ),
    );
  }
}
