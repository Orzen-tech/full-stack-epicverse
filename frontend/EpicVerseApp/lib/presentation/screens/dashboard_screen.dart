import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../core/constants/app_colors.dart';
import '../widgets/network_background.dart';
import '../../providers/user_provider.dart';
import 'mode_selection_screen.dart';
import 'dart:convert';
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
      duration: const Duration(milliseconds: 2200),
    )..repeat(reverse: true);

    _glowAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
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
                                Center(
                                  child: Image.asset(
                                    'assets/images/enter_header.webp',
                                    width: 360,
                                    fit: BoxFit.contain,
                                    errorBuilder: (context, error, stackTrace) {
                                      return const SizedBox.shrink();
                                    },
                                  ),
                                ),
                                Stack(
                                  alignment: Alignment.center,
                                  children: [
                                    // Sun rays CustomPaint
                                    SizedBox(
                                      width: 600,
                                      height: 240,
                                      child: CustomPaint(
                                        painter: SunRaysPainter(
                                          animationValue: _glowAnimation.value,
                                        ),
                                      ),
                                    ),
                                    // Main button
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
                                            color: AppColors.primaryGold.withValues(alpha: 0.25 + 0.20 * _glowAnimation.value),
                                            width: 3.0,
                                          ),
                                          boxShadow: [
                                            BoxShadow(
                                              color: Colors.black.withValues(alpha: 0.7),
                                              blurRadius: 30,
                                              spreadRadius: 10,
                                              offset: const Offset(0, 15),
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
                                  ],
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

class _Ray {
  final double angle;
  final double length;
  final double halfSpan;
  const _Ray({required this.angle, required this.length, required this.halfSpan});
}

class SunRaysPainter extends CustomPainter {
  final double animationValue;

  static const double _btnRadius = 110.0;

  // Three layers: wide bloom beams, medium rays, fine hair streaks
  static final List<_Ray> _wideRays  = _gen(10, 0.05, 0.13, 30, 40, seed: 1);
  static final List<_Ray> _midRays   = _gen(22, 0.01, 0.05, 20, 35, seed: 2);
  static final List<_Ray> _hairRays  = _gen(40, 0.003, 0.012, 12, 28, seed: 3);

  SunRaysPainter({required this.animationValue});

  static List<_Ray> _gen(int n, double spanMin, double spanMax,
      double lenBase, double lenVar, {required int seed}) {
    final rng = math.Random(seed);
    return List.generate(n, (i) {
      // Heavy jitter so rays cluster and gap naturally
      final angle = (i / n) * 2 * math.pi + (rng.nextDouble() - 0.5) * 0.80;
      // Exponential-ish length variation: some very short, a few very long
      final t = rng.nextDouble();
      final length = lenBase + (t * t) * lenVar;
      // Span varies a lot for messy feel
      final halfSpan = spanMin + rng.nextDouble() * rng.nextDouble() * (spanMax - spanMin);
      return _Ray(angle: angle, length: length, halfSpan: halfSpan);
    });
  }

  // Draws one wedge sub-pass with a given span multiplier and alpha factor
  void _drawWedge(Canvas canvas, Offset center, double totalLen,
      double angle, double halfSpan, double edgeFrac, double peakFrac,
      double fadeFrac, double alphaFactor, Color nearColor, Color farColor) {
    final lA  = angle - halfSpan;
    final rA  = angle + halfSpan;
    final lPt = Offset(center.dx + totalLen * math.cos(lA),
                       center.dy + totalLen * math.sin(lA));
    final rPt = Offset(center.dx + totalLen * math.cos(rA),
                       center.dy + totalLen * math.sin(rA));

    canvas.drawPath(
      Path()
        ..moveTo(center.dx, center.dy)
        ..lineTo(lPt.dx, lPt.dy)
        ..lineTo(rPt.dx, rPt.dy)
        ..close(),
      Paint()
        ..shader = RadialGradient(
          colors: [
            Colors.transparent,
            Colors.transparent,
            nearColor.withValues(alpha: alphaFactor),
            farColor.withValues(alpha: alphaFactor * 0.40),
            Colors.transparent,
          ],
          stops: [0.0, edgeFrac, peakFrac, fadeFrac, 1.0],
        ).createShader(Rect.fromCircle(center: center, radius: totalLen)),
    );
  }

  void _drawLayer(Canvas canvas, Offset center, List<_Ray> rays,
      double extScale, double peakAlpha, Color nearColor, Color farColor) {
    for (final ray in rays) {
      final totalLen = _btnRadius + ray.length * extScale;
      final edgeFrac = (_btnRadius / totalLen).clamp(0.0, 0.90);
      final peakFrac = (edgeFrac + 0.05).clamp(0.0, 1.0);
      final fadeFrac = (edgeFrac + 0.55).clamp(0.0, 1.0);

      // Core beam — full alpha, exact span
      _drawWedge(canvas, center, totalLen, ray.angle, ray.halfSpan,
          edgeFrac, peakFrac, fadeFrac, peakAlpha, nearColor, farColor);

      // Soft inner feather — 1.4× span, 45% alpha (creates gradient edge)
      _drawWedge(canvas, center, totalLen, ray.angle, ray.halfSpan * 1.4,
          edgeFrac, peakFrac, fadeFrac, peakAlpha * 0.45, nearColor, farColor);

      // Wide outer feather — 2.0× span, 18% alpha (soft bleed at sides)
      _drawWedge(canvas, center, totalLen, ray.angle, ray.halfSpan * 2.0,
          edgeFrac, peakFrac, fadeFrac, peakAlpha * 0.18, nearColor, farColor);
    }
  }

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final b   = 0.38 + 0.62 * animationValue;  // brightness 0.38 → 1.0
    final ext = 0.48 + 0.12 * animationValue;  // length     48%  → 60%

    // Layer 1 — wide soft bloom beams (deep amber base)
    _drawLayer(canvas, center, _wideRays, ext * 0.85,
        0.18 * b, const Color(0xFFFFCC00), const Color(0xFFFF8800));

    // Layer 2 — medium golden rays
    _drawLayer(canvas, center, _midRays, ext,
        0.22 * b, const Color(0xFFFFE040), const Color(0xFFFFAA00));

    // Layer 3 — fine bright hair streaks
    _drawLayer(canvas, center, _hairRays, ext * 1.15,
        0.28 * b, const Color(0xFFFFF176), const Color(0xFFFFCC00));

    // Volumetric corona ring at the button edge — 5 passes for intense brightness
    for (int i = 0; i < 5; i++) {
      final r = _btnRadius + 1.0 + i * 6.0;
      canvas.drawCircle(center, r, Paint()
        ..style = PaintingStyle.stroke
        ..strokeWidth = 10.0 - i * 1.2
        ..maskFilter = MaskFilter.blur(BlurStyle.normal, 4.0 + i * 5.0)
        ..color = const Color(0xFFFFEE00).withValues(alpha: (0.45 - i * 0.07).clamp(0.0, 1.0) * b));
    }

    // Extra tight hard rim at the circle edge
    canvas.drawCircle(center, _btnRadius, Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.5
      ..color = const Color(0xFFFFFFAA).withValues(alpha: 0.35 * b));

    // Soft wide outer bloom
    canvas.drawCircle(center, _btnRadius + 18, Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 28
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 18)
      ..color = const Color(0xFFFFCC00).withValues(alpha: 0.18 * b));

  }

  @override
  bool shouldRepaint(SunRaysPainter old) => old.animationValue != animationValue;
}
