import 'package:flutter/material.dart';
import '../../core/constants/app_colors.dart';

class NetworkBackground extends StatelessWidget {
  final Widget child;

  const NetworkBackground({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        // Base Gradient
        Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              colors: [Color(0xFF1A0B2E), Color(0xFF0B0415)],
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
            ),
          ),
        ),
        
        // Network Pattern Layer
        Opacity(
          opacity: 0.15,
          child: Image.asset(
            'assets/images/network_bg.png',
            fit: BoxFit.cover,
            width: double.infinity,
            height: double.infinity,
            errorBuilder: (context, error, stackTrace) => const SizedBox(), // Fallback if image not yet ready
          ),
        ),
        
        // Subtle ambient glows
        Positioned(
          top: -100,
          right: -50,
          child: Container(
            width: 300,
            height: 300,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: AppColors.primaryGold.withValues(alpha: 0.05),
            ),
          ),
        ),
        
        child,
      ],
    );
  }
}
