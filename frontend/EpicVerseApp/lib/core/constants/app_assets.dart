import 'package:flutter/material.dart';

class AppIcons {
  // EpicVerse Theme Icons
  static const IconData companion = Icons.auto_awesome_rounded;
  static const IconData profile = Icons.person_outline_rounded;
  static const IconData language = Icons.language_rounded;
  static const IconData status = Icons.sensors_rounded;
  static const IconData explore = Icons.public_rounded;
  static const IconData system = Icons.settings_input_component_rounded;
  static const IconData notification = Icons.notifications_none_rounded;
  static const IconData arc = Icons.hub_outlined;
  static const IconData exit = Icons.logout_rounded;
  static const IconData close = Icons.close_rounded;
  static const IconData settings = Icons.settings_outlined;
}

class AppGradients {
  static const LinearGradient epicGold = LinearGradient(
    colors: [
      Color(0xFF86632B), // Deep Gold
      Color(0xFFC5A358), // Medium Gold
      Color(0xFFF3D081), // Highlight Gold
      Color(0xFFC5A358), // Medium Gold
      Color(0xFF86632B), // Deep Gold
    ],
    stops: [0.0, 0.25, 0.5, 0.75, 1.0],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );

  static const LinearGradient deepPurple = LinearGradient(
    colors: [Color(0xFF1A0B2E), Color(0xFF0B0415)],
    begin: Alignment.topCenter,
    end: Alignment.bottomCenter,
  );

  static LinearGradient glassHighlight = LinearGradient(
    colors: [Color(0x1AFFFFFF), Colors.transparent],
    begin: Alignment.topLeft,
    end: Alignment.bottomRight,
  );
}
