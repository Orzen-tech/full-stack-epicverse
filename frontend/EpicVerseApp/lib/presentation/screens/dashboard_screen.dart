import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import '../../providers/user_provider.dart';
import 'mode_selection_screen.dart';
import 'dart:convert';
import 'dart:math' as math;
import 'settings_screen.dart';

class DashboardScreen extends ConsumerStatefulWidget {
  const DashboardScreen({super.key});

  @override
  ConsumerState<DashboardScreen> createState() => _DashboardScreenState();
}

class _DashboardScreenState extends ConsumerState<DashboardScreen> with TickerProviderStateMixin {
  late AnimationController _entryController;
  late AnimationController _floatController;
  late AnimationController _glowController;

  late Animation<double> _scaleAnimation;
  late Animation<double> _fadeAnimation;
  late Animation<Offset> _slideAnimation;
  late Animation<double> _floatAnimation;
  late Animation<double> _glowAnimation;

  @override
  void initState() {
    super.initState();

    _entryController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 2000),
    );

    _scaleAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _entryController,
        curve: const Interval(0.3, 1.0, curve: Curves.elasticOut),
      ),
    );

    _fadeAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(
        parent: _entryController,
        curve: const Interval(0.0, 0.4, curve: Curves.easeIn),
      ),
    );

    _slideAnimation = Tween<Offset>(begin: const Offset(0, 0.2), end: Offset.zero).animate(
      CurvedAnimation(
        parent: _entryController,
        curve: const Interval(0.2, 0.8, curve: Curves.easeOutCubic),
      ),
    );

    _floatController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 4),
    )..repeat(reverse: true);

    _floatAnimation = Tween<double>(begin: -10, end: 10).animate(
      CurvedAnimation(
        parent: _floatController,
        curve: Curves.easeInOutSine,
      ),
    );

    _glowController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 3),
    )..repeat(reverse: true);

    _glowAnimation = Tween<double>(begin: 1.0, end: 1.4).animate(
      CurvedAnimation(
        parent: _glowController,
        curve: Curves.easeInOut,
      ),
    );

    _entryController.forward();
  }

  @override
  void dispose() {
    _entryController.dispose();
    _floatController.dispose();
    _glowController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final user = ref.watch(userProvider);
    final String displayName = user?.displayName ?? 'Explorer';
    debugPrint("Dashboard Build: User=${user?.id}, PhotoPresent=${user?.profilePicture != null}");

    return Scaffold(
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              FadeTransition(
                opacity: _fadeAnimation,
                child: SlideTransition(
                  position: _slideAnimation,
                  child: _buildAppBar(context, ref, displayName),
                ),
              ),
              Expanded(
                child: Center(
                  child: AnimatedBuilder(
                    animation: Listenable.merge([_floatAnimation, _glowAnimation]),
                    builder: (context, child) {
                      return Transform.translate(
                        offset: Offset(0, _floatAnimation.value),
                        child: ScaleTransition(
                          scale: _scaleAnimation,
                          child: FadeTransition(
                            opacity: _fadeAnimation,
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                GestureDetector(
                                  onTap: () {
                                    Navigator.of(context).push(
                                      MaterialPageRoute(builder: (_) => const ModeSelectionScreen()),
                                    );
                                  },
                                  child: Container(
                                    width: 220,
                                    height: 220,
                                    decoration: BoxDecoration(
                                      shape: BoxShape.circle,
                                      gradient: const LinearGradient(
                                        colors: [Color(0xFF432571), Color(0xFF2C1349)],
                                        begin: Alignment.topLeft,
                                        end: Alignment.bottomRight,
                                      ),
                                      border: Border.all(
                                        color: AppColors.primaryGold.withValues(alpha: 0.6 * _glowAnimation.value.clamp(0.0, 1.0)),
                                        width: 3,
                                      ),
                                      boxShadow: [
                                        BoxShadow(
                                          color: Colors.black.withValues(alpha: 0.7),
                                          blurRadius: 30,
                                          spreadRadius: 10,
                                          offset: const Offset(0, 15),
                                        ),
                                        BoxShadow(
                                          color: AppColors.primaryGold.withValues(alpha: 0.25 * _glowAnimation.value),
                                          blurRadius: 50 * _glowAnimation.value,
                                          spreadRadius: 2,
                                        ),
                                      ],
                                    ),
                                    padding: const EdgeInsets.all(25),
                                    child: Center(
                                      child: Image.asset(
                                        'assets/images/button_png.webp',
                                        width: 170,
                                        fit: BoxFit.contain,
                                        errorBuilder: (context, error, stackTrace) {
                                          return const Icon(Icons.play_circle_fill, color: AppColors.primaryGold, size: 80);
                                        },
                                      ),
                                    ),
                                  ),
                                ),
                                const SizedBox(height: 6),
                                SizedBox(
                                  width: 300,
                                  height: 52,
                                  child: CustomPaint(
                                    painter: _ArcTextPainter(),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      );
                    },
                  ),
                ),
              ),
              const SizedBox(height: 60),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildAppBar(BuildContext context, WidgetRef ref, String name) {
    final user = ref.watch(userProvider);

    return Padding(
      padding: const EdgeInsets.all(24.0),
      child: Row(
        children: [
          Hero(
            tag: 'user_avatar',
            child: Container(
              width: 48,
              height: 48,
              decoration: BoxDecoration(
                color: Colors.white.withValues(alpha: 0.1),
                shape: BoxShape.circle,
                border: Border.all(color: Colors.white.withValues(alpha: 0.4), width: 1.5),
                image: user?.profilePicture != null
                    ? DecorationImage(
                        image: MemoryImage(base64Decode(user!.profilePicture!)),
                        fit: BoxFit.cover,
                      )
                    : null,
              ),
              child: user?.profilePicture == null
                  ? const Icon(Icons.person_outline_rounded, color: Colors.white, size: 26)
                  : null,
            ),
          ),
          const SizedBox(width: 16),
          Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Welcome back', style: TextStyle(color: AppColors.textMuted, fontSize: 11, letterSpacing: 0.5)),
              Text(name, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 19, color: AppColors.textPrimary)),
            ],
          ),
          const Spacer(),
          IconButton(
            onPressed: () {
              Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              );
            },
            icon: const Icon(Icons.settings_rounded, color: AppColors.textPrimary),
            tooltip: 'Settings',
          ),
        ],
      ),
    );
  }
}

class _ArcTextPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    const String text = 'TAP EPICVERSE';
    const double radius = 118.0;
    const double fontSize = 14.0;
    const double letterSpacing = 5.5;

    final double centerX = size.width / 2;
    const double centerY = -102.0;

    final List<String> chars = text.split('');
    final List<double> charWidths = [];

    for (final ch in chars) {
      final tp = TextPainter(
        text: TextSpan(
          text: ch,
          style: const TextStyle(fontSize: fontSize, fontWeight: FontWeight.w700),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      charWidths.add(tp.width);
    }

    final double totalWidth = charWidths.fold(0.0, (a, b) => a + b) +
        letterSpacing * (chars.length - 1);
    final double totalAngle = totalWidth / radius;

    // Start from right side of arc and decrease angle → left-to-right on screen
    double currentAngle = math.pi / 2 + totalAngle / 2;

    for (int i = 0; i < chars.length; i++) {
      final double charWidth = charWidths[i];
      final double charAngle = currentAngle - charWidth / 2 / radius;

      canvas.save();

      final double x = centerX + radius * math.cos(charAngle);
      final double y = centerY + radius * math.sin(charAngle);

      canvas.translate(x, y);
      canvas.rotate(charAngle - math.pi / 2);

      final tp = TextPainter(
        text: TextSpan(
          text: chars[i],
          style: const TextStyle(
            color: Color(0xFFD4AF37),
            fontSize: fontSize,
            fontWeight: FontWeight.w700,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();

      tp.paint(canvas, Offset(-charWidth / 2, -tp.height / 2));

      canvas.restore();

      currentAngle -= (charWidth + letterSpacing) / radius;
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
