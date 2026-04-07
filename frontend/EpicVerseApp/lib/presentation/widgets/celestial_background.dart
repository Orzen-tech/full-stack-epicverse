import 'dart:math' as math;
import 'package:flutter/material.dart';
import '../../core/constants/app_colors.dart';

class CelestialBackground extends StatelessWidget {
  final Widget child;

  const CelestialBackground({super.key, required this.child});

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        // Deep Purple Base with subtle reddish-brown tint at the top
        Container(
          decoration: const BoxDecoration(
            gradient: LinearGradient(
              colors: [
                Color(0xFF371454), // Exact match for the new logo background
                Color(0xFF1B072D), 
                Color(0xFF130421), 
              ],
              begin: Alignment.topCenter,
              end: Alignment.bottomCenter,
            ),
          ),
        ),

        // Sacred Geometry and Nebula
        Positioned.fill(
          child: CustomPaint(
            painter: _CelestialPainter(),
          ),
        ),

        child,
      ],
    );
  }
}

class _CelestialPainter extends CustomPainter {
  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height * 0.18);
    
    // 2. Sacred Geometry Lines
    final linePaint = Paint()
      ..color = const Color(0xFFC5A358).withOpacity(0.12)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.6;

    // Horizontal thin lines across the screen (one passing through orb)
    canvas.drawLine(Offset(0, center.dy), Offset(size.width, center.dy), linePaint);
    canvas.drawLine(Offset(0, center.dy + 120), Offset(size.width, center.dy + 120), linePaint);

    // Concentric Geometry
    // Outer circle
    canvas.drawCircle(center, 140, linePaint);
    
    // Middle dotted/point circle
    final pointCirclePaint = Paint()
      ..color = const Color(0xFFC5A358).withOpacity(0.3)
      ..style = PaintingStyle.fill;
    
    for (int i = 0; i < 4; i++) {
      double angle = i * math.pi / 2; // North, East, South, West points
      canvas.drawCircle(
        Offset(center.dx + 110 * math.cos(angle), center.dy + 110 * math.sin(angle)),
        2.5,
        pointCirclePaint
      );
    }
    
    // Inner thin circle
    canvas.drawCircle(center, 80, linePaint);

    // 3. Large Intersecting Circles (Sacred Geometry style)
    final largeCirclePaint = Paint()
      ..color = const Color(0xFFFFFFFF).withOpacity(0.03)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 0.5;
    
    // Upper and lower large circles
    canvas.drawCircle(center + const Offset(0, -60), 180, largeCirclePaint);
    canvas.drawCircle(center + const Offset(0, 180), 220, largeCirclePaint);

    // 4. Removed Central Glowing Orb as requested
    
    // 5. Very faint background pattern (starry noise)
    final rand = math.Random(42);
    final starPaint = Paint()..color = Colors.white.withOpacity(0.1);
    for (int i = 0; i < 50; i++) {
      canvas.drawCircle(
        Offset(rand.nextDouble() * size.width, rand.nextDouble() * size.height),
        rand.nextDouble() * 1.5,
        starPaint
      );
    }
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}
