import 'package:flutter/material.dart';

import '../../core/security/device_integrity.dart';

/// The non-dismissible "Security Alert" shown when the device is confirmed
/// rooted, jailbroken or instrumented (Finding #4). Same content as before.
void showSecurityAlertDialog(BuildContext context) {
  showDialog(
    context: context,
    barrierDismissible: false,
    builder: (context) => PopScope(
      canPop: false,
      child: AlertDialog(
        backgroundColor: const Color(0xFF1B0C2D),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Color(0xFFFF4C4C), width: 1.5),
        ),
        title: const Row(
          children: [
            Icon(Icons.security, color: Color(0xFFFF4C4C), size: 24),
            SizedBox(width: 10),
            Text(
              'Security Alert',
              style: TextStyle(
                color: Color(0xFFFF4C4C),
                fontWeight: FontWeight.bold,
                fontSize: 18,
              ),
            ),
          ],
        ),
        content: const Text(
          'This device appears to be rooted or jailbroken.\n\n'
          'EpicVerse cannot run on rooted or jailbroken devices to protect '
          'your account security and sensitive data.',
          style: TextStyle(color: Colors.white70, fontSize: 14, height: 1.5),
        ),
      ),
    ),
  );
}

/// Runs the integrity check and, only for a confirmed compromise, shows the
/// Security Alert. Returns true when the caller must stop. `unknown` keeps
/// normal access.
Future<bool> blockIfCompromised(BuildContext context) async {
  IntegrityResult result;
  try {
    result = await DeviceIntegrity.check();
  } catch (_) {
    return false; // unexpected failure behaves like "unknown": normal access
  }
  if (result.state != IntegrityState.compromised) return false;
  if (context.mounted) showSecurityAlertDialog(context);
  return true;
}
