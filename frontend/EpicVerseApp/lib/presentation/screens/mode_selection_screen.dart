import 'package:flutter/material.dart';
import '../../core/constants/app_colors.dart';
import '../../core/constants/app_assets.dart';
import '../widgets/network_background.dart';
import 'companion_ready_screen.dart';
import '../../core/network/websocket_service.dart';

class ModeSelectionScreen extends StatelessWidget {
  const ModeSelectionScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: NetworkBackground(
        child: SafeArea(
          child: Column(
            children: [
              _buildHeader(context),
              Expanded(
                child: SingleChildScrollView(
                  physics: const BouncingScrollPhysics(),
                  padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.center,
                    children: [
                      const SizedBox(height: 20),
                      Text(
                        'CHOOSE YOUR JOURNEY',
                        style: TextStyle(
                          color: AppColors.primaryGold.withOpacity(0.9),
                          fontSize: 26,
                          fontWeight: FontWeight.w800,
                          letterSpacing: 2.5,
                        ),
                      ),
                      const SizedBox(height: 40),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 20),
                        child: _buildModeCard(
                          context,
                          1,
                          'Origin Arc (Balakanda)',
                          'assets/images/mode_1_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 1');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 1')),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 20),
                        child: _buildModeCard(
                          context,
                          2,
                          'CrownShift (Ayodhya Kanda)',
                          'assets/images/mode_2_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 2');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 2')),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 20),
                        child: _buildModeCard(
                          context,
                          3,
                          'WildRun (AranyaKanda)',
                          'assets/images/mode_3_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 3');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 3')),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 20),
                        child: _buildModeCard(
                          context,
                          4,
                          'GlowLine (Kishkindha Kanda)',
                          'assets/images/mode_4_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 4');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 4')),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 20),
                        child: _buildModeCard(
                          context,
                          5,
                          'LankaLeap (SundaraKanda)',
                          'assets/images/mode_5_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 5');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 5')),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 20),
                        child: _buildModeCard(
                          context,
                          6,
                          'WarRoom (Yuddha Kanda)',
                          'assets/images/mode_6_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 6');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 6')),
                            );
                          },
                        ),
                      ),
                      Padding(
                        padding: const EdgeInsets.only(bottom: 40),
                        child: _buildModeCard(
                          context,
                          7,
                          'Afterlight (Uttara Kanda)',
                          'assets/images/mode_7_bg.png',
                          isUnlocked: true,
                          onTap: () {
                            webSocketService.updateMode('Mode 7');
                            Navigator.push(
                              context,
                              MaterialPageRoute(builder: (_) => const CompanionReadyScreen(gameMode: 'Mode 7')),
                            );
                          },
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 20),
      child: Stack(
        alignment: Alignment.center,
        children: [
          Align(
            alignment: Alignment.centerLeft,
            child: IconButton(
              icon: const Icon(Icons.arrow_back_ios_new_rounded, color: Colors.white, size: 22),
              onPressed: () => Navigator.pop(context),
            ),
          ),
          const Text(
            'MODE SELECTION',
            style: TextStyle(
              color: Colors.white,
              fontSize: 14,
              fontWeight: FontWeight.w700,
              letterSpacing: 4,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildModeCard(
    BuildContext context,
    int number,
    String title,
    String imagePath, {
    bool isUnlocked = false,
    VoidCallback? onTap,
  }) {
    return GestureDetector(
      onTap: isUnlocked ? onTap : null,
      child: Container(
        height: 180,
        width: double.infinity,
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(24),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withOpacity(0.4),
              blurRadius: 20,
              offset: const Offset(0, 10),
            ),
          ],
        ),
        child: ClipRRect(
          borderRadius: BorderRadius.circular(24),
          clipBehavior: Clip.antiAliasWithSaveLayer,
          child: Stack(
            children: [
              // Background Image
              Positioned.fill(
                child: Image.asset(
                  imagePath,
                  fit: BoxFit.cover,
                  color: isUnlocked ? null : Colors.black.withOpacity(0.6),
                  colorBlendMode: isUnlocked ? null : BlendMode.darken,
                ),
              ),
              
              // Gradient Overlay
              Positioned.fill(
                child: Container(
                  decoration: BoxDecoration(
                    gradient: LinearGradient(
                      begin: Alignment.bottomCenter,
                      end: Alignment.topCenter,
                      colors: [
                        Colors.black.withOpacity(0.9),
                        Colors.black.withOpacity(0.2),
                        Colors.transparent,
                      ],
                      stops: const [0.0, 0.5, 1.0],
                    ),
                  ),
                ),
              ),

              // Content anchored to bottom
              Positioned(
                bottom: 24,
                left: 24,
                right: 24,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.center,
                      children: [
                        // Small Journey Number
                        Container(
                          width: 28,
                          height: 28,
                          decoration: BoxDecoration(
                            shape: BoxShape.circle,
                            color: isUnlocked ? AppColors.primaryGold : Colors.white.withOpacity(0.1),
                            border: Border.all(
                              color: isUnlocked ? AppColors.accentGold : Colors.white.withOpacity(0.2),
                              width: 1,
                            ),
                          ),
                          child: Center(
                            child: Text(
                              '$number',
                              style: TextStyle(
                                color: isUnlocked ? Colors.black : Colors.white.withOpacity(0.4),
                                fontSize: 13,
                                fontWeight: FontWeight.bold,
                              ),
                            ),
                          ),
                        ),
                        const SizedBox(width: 12),
                        // Journey Title
                        Expanded(
                          child: Text(
                            title,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: TextStyle(
                              color: isUnlocked ? Colors.white : Colors.white.withOpacity(0.4),
                              fontSize: 22,
                              fontWeight: FontWeight.w800,
                              letterSpacing: 0.5,
                              shadows: [
                                Shadow(
                                  color: Colors.black.withOpacity(0.8),
                                  blurRadius: 10,
                                  offset: const Offset(0, 2),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ],
                    ),
                    if (!isUnlocked) ...[
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Icon(Icons.lock_rounded, color: Colors.white.withOpacity(0.4), size: 16),
                          const SizedBox(width: 6),
                          Text(
                            'UNLOCKED AT LEVEL ${number * 5}',
                            style: TextStyle(
                              color: Colors.white.withOpacity(0.4),
                              fontSize: 12,
                              fontWeight: FontWeight.w600,
                              letterSpacing: 1.2,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ],
                ),
              ),

              // Ripple effect overlay for interaction
              if (isUnlocked)
                Positioned.fill(
                  child: Material(
                    color: Colors.transparent,
                    child: InkWell(
                      onTap: onTap,
                      splashColor: AppColors.primaryGold.withOpacity(0.1),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }
}
