import 'package:flutter/material.dart';
import '../../core/constants/app_colors.dart';

class EpicVerseLogo extends StatelessWidget {
  final double fontSize;
  final bool showUnderline;

  const EpicVerseLogo({
    super.key,
    this.fontSize = 42,
    this.showUnderline = true,
  });

  @override
  Widget build(BuildContext context) {
    // Vibrant yellow-gold gradient matching the template
    const goldGradient = LinearGradient(
      colors: [
        Color(0xFF8B5E21), // Deep bronze base
        Color(0xFFE1B152), // Gold mid
        Color(0xFFFEF4B4), // Bright highlight
        Color(0xFFFDE28A), // Warm gold
        Color(0xFF8B5E21), // Deep bronze top
      ],
      stops: [0.0, 0.3, 0.5, 0.7, 1.0],
      begin: Alignment.bottomCenter,
      end: Alignment.topCenter,
    );

    final List<Shadow> logoShadows = [
      Shadow(
        color: Colors.black.withOpacity(0.6),
        offset: const Offset(0, 3),
        blurRadius: 5,
      ),
    ];

    return Container(
      constraints: const BoxConstraints(maxWidth: 500),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          FittedBox(
            fit: BoxFit.scaleDown,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                _buildSizedTextBlock(context, 'E', 'PIC', goldGradient, fontSize, logoShadows, largeFirst: true),
                const SizedBox(width: 2), // Tighten spacing
                _buildCentralV(context, goldGradient, fontSize * 1.62, logoShadows),
                const SizedBox(width: 2), // Tighten spacing
                _buildSizedTextBlock(context, 'ERS', 'E', goldGradient, fontSize, logoShadows, largeFirst: false),
              ],
            ),
          ),
          
          if (showUnderline)
            Padding(
              padding: const EdgeInsets.only(top: 0, bottom: 8),
              child: CustomPaint(
                size: Size(fontSize * 7.8, 32),
                painter: _OrnateDividerPainter(const Color(0xFFE1B152)),
              ),
            ),
          
          ShaderMask(
            shaderCallback: (bounds) => goldGradient.createShader(bounds),
            child: Text(
              'BY KRIYORA',
              style: TextStyle(
                color: Colors.white,
                fontSize: fontSize * 0.26,
                letterSpacing: 10,
                fontWeight: FontWeight.w800,
                shadows: logoShadows,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSizedTextBlock(
    BuildContext context, 
    String part1, 
    String part2, 
    Gradient gradient, 
    double baseSize, 
    List<Shadow> shadows, 
    {required bool largeFirst}
  ) {
    final largeSize = baseSize * 1.12;
    final smallSize = baseSize * 0.85;

    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        // Tapered horizontal top-bar
        CustomPaint(
          size: Size(baseSize * 2.3, 3),
          painter: _TaperedBarPainter(gradient),
        ),
        const SizedBox(height: 6),
        ShaderMask(
          shaderCallback: (bounds) => gradient.createShader(bounds),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(
                part1,
                style: Theme.of(context).textTheme.displayLarge?.copyWith(
                  fontSize: largeFirst ? largeSize : smallSize,
                  fontWeight: FontWeight.w900,
                  letterSpacing: -1.2, // Tighter
                  color: Colors.white,
                  shadows: shadows,
                ),
              ),
              Text(
                part2,
                style: Theme.of(context).textTheme.displayLarge?.copyWith(
                  fontSize: largeFirst ? smallSize : largeSize,
                  fontWeight: FontWeight.w900,
                  letterSpacing: -1.2, // Tighter
                  color: Colors.white,
                  shadows: shadows,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildCentralV(BuildContext context, Gradient gradient, double size, List<Shadow> shadows) {
    return ShaderMask(
      shaderCallback: (bounds) => gradient.createShader(bounds),
      child: Text(
        'V',
        style: Theme.of(context).textTheme.displayLarge?.copyWith(
          fontSize: size,
          fontWeight: FontWeight.w900,
          color: Colors.white,
          height: 0.82,
          shadows: shadows,
        ),
      ),
    );
  }
}

class _TaperedBarPainter extends CustomPainter {
  final Gradient gradient;
  _TaperedBarPainter(this.gradient);

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..shader = gradient.createShader(Offset.zero & size)
      ..style = PaintingStyle.fill;

    final path = Path();
    path.moveTo(0, size.height * 0.5);
    path.lineTo(size.width * 0.5, 0);
    path.lineTo(size.width, size.height * 0.5);
    path.lineTo(size.width * 0.5, size.height);
    path.close();
    
    // Slightly more elegant taper
    final refinedPath = Path()
      ..moveTo(0, size.height * 0.5)
      ..quadraticBezierTo(size.width * 0.5, -size.height * 0.2, size.width, size.height * 0.5)
      ..quadraticBezierTo(size.width * 0.5, size.height * 1.2, 0, size.height * 0.5);

    canvas.drawPath(refinedPath, paint);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}

class _OrnateDividerPainter extends CustomPainter {
  final Color color;
  _OrnateDividerPainter(this.color);

  @override
  void paint(Canvas canvas, Size size) {
    final mainPaint = Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.4
      ..strokeCap = StrokeCap.round;

    final accentPaint = Paint()
      ..color = color.withOpacity(0.7)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1.0;

    final w = size.width;
    final h = size.height;
    
    // 1. The graceful "Wing" curve meeting in the center point
    final path = Path();
    // Center point at the top
    const centerX = 0.5;
    const centerY = 0.15;
    
    // Left Wing
    path.moveTo(w * centerX, h * centerY);
    path.quadraticBezierTo(w * 0.35, h * 0.35, w * 0.15, h * 0.35);
    path.quadraticBezierTo(w * 0.05, h * 0.35, 0, h * 0.6);
    
    // Right Wing
    path.moveTo(w * centerX, h * centerY);
    path.quadraticBezierTo(w * 0.65, h * 0.35, w * 0.85, h * 0.35);
    path.quadraticBezierTo(w * 0.95, h * 0.35, w, h * 0.6);
    
    canvas.drawPath(path, mainPaint);

    // 2. Swirls inside the wings
    final leftScroll = Path()
      ..moveTo(w * 0.1, h * 0.6)
      ..cubicTo(w * 0.15, h * 0.9, w * 0.25, h * 0.6, w * 0.2, h * 0.5);
    
    final rightScroll = Path()
      ..moveTo(w * 0.9, h * 0.6)
      ..cubicTo(w * 0.85, h * 0.9, w * 0.75, h * 0.6, w * 0.8, h * 0.5);

    canvas.drawPath(leftScroll, accentPaint);
    canvas.drawPath(rightScroll, accentPaint);

    // 3. Central decorative point detail
    canvas.drawCircle(Offset(w * centerX, h * centerY), 3, Paint()..color = color);
  }

  @override
  bool shouldRepaint(covariant CustomPainter oldDelegate) => false;
}