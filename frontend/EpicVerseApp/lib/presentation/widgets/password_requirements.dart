import 'package:flutter/material.dart';

import '../../core/constants/app_colors.dart';
import '../../core/utils/password_validator.dart';

/// Shows every password requirement at once, below the password field.
///
/// Each rule updates independently and immediately as [password] changes:
/// before the user types anything every rule is shown in a neutral
/// "incomplete" state; once typing starts, satisfied rules switch to a
/// green check while unsatisfied rules simply stay incomplete (no red
/// error spam). The rules and their pass/fail logic come straight from
/// [PasswordValidator] so this list can never disagree with what is
/// actually enforced on submit.
class PasswordRequirementsChecklist extends StatelessWidget {
  const PasswordRequirementsChecklist({super.key, required this.password});

  final String password;

  @override
  Widget build(BuildContext context) {
    final bool started = password.isNotEmpty;
    final requirements = PasswordValidator.requirementsFor(password);

    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: Colors.white.withValues(alpha: 0.08)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Password must contain:',
            style: TextStyle(
              color: AppColors.textSecondary,
              fontSize: 12,
              fontWeight: FontWeight.w600,
              letterSpacing: 0.3,
            ),
          ),
          const SizedBox(height: 8),
          for (final requirement in requirements)
            _RequirementRow(
              label: requirement.label,
              satisfied: requirement.satisfied,
              started: started,
            ),
        ],
      ),
    );
  }
}

class _RequirementRow extends StatelessWidget {
  const _RequirementRow({
    required this.label,
    required this.satisfied,
    required this.started,
  });

  final String label;
  final bool satisfied;
  final bool started;

  @override
  Widget build(BuildContext context) {
    // Neutral until the user starts typing; then each row is independent.
    final bool done = started && satisfied;
    final Color color = done ? AppColors.profileActive : AppColors.textMuted;
    final IconData icon = done
        ? Icons.check_circle_rounded
        : (started
            ? Icons.radio_button_unchecked
            : Icons.circle_outlined);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, size: 16, color: color),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              label,
              style: TextStyle(
                color: done ? AppColors.textPrimary : AppColors.textSecondary,
                fontSize: 12.5,
                height: 1.3,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
