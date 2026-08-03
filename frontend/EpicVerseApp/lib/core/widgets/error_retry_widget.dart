import 'package:flutter/material.dart';

class ErrorRetryWidget extends StatelessWidget {
  final String message;
  final VoidCallback onRetry;
  final String retryText;
  final IconData icon;

  const ErrorRetryWidget({
    super.key,
    this.message = "Couldn't load data.",
    required this.onRetry,
    this.retryText = "Retry",
    this.icon = Icons.error_outline_rounded,
  });

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24.0),
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: const Color(0xFF1B0C2D),
                shape: BoxShape.circle,
                border: Border.all(color: const Color(0xFFFF4C4C).withValues(alpha: 0.3), width: 1.5),
              ),
              child: Icon(
                icon,
                color: const Color(0xFFFF4C4C),
                size: 36,
              ),
            ),
            const SizedBox(height: 16),
            Text(
              message,
              textAlign: TextAlign.center,
              style: const TextStyle(
                color: Colors.white,
                fontSize: 16,
                fontWeight: FontWeight.w500,
              ),
            ),
            const SizedBox(height: 20),
            ElevatedButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh_rounded, size: 18),
              label: Text(retryText),
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF321650),
                foregroundColor: Colors.white,
                padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                  side: const BorderSide(color: Color(0xFFD4AF37), width: 1),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
