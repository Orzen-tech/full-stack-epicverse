import 'dart:async';
import 'package:flutter/material.dart';
import '../services/network_service.dart';

class NetworkBannerWrapper extends StatefulWidget {
  final Widget child;

  const NetworkBannerWrapper({
    super.key,
    required this.child,
  });

  @override
  State<NetworkBannerWrapper> createState() => _NetworkBannerWrapperState();
}

class _NetworkBannerWrapperState extends State<NetworkBannerWrapper> {
  StreamSubscription<NetworkStatus>? _subscription;
  NetworkStatus _status = NetworkStatus.connected;
  bool _showRestoredBanner = false;
  Timer? _restoredTimer;

  @override
  void initState() {
    super.initState();
    networkService.initialize();
    _status = networkService.currentStatus;

    _subscription = networkService.statusStream.listen((newStatus) {
      if (!mounted) return;

      if (_status != NetworkStatus.connected && newStatus == NetworkStatus.connected) {
        // Was offline or poor, now restored -> show "Restored" briefly
        setState(() {
          _status = newStatus;
          _showRestoredBanner = true;
        });

        _restoredTimer?.cancel();
        _restoredTimer = Timer(const Duration(seconds: 2), () {
          if (mounted) {
            setState(() {
              _showRestoredBanner = false;
            });
          }
        });
      } else {
        setState(() {
          _status = newStatus;
          _showRestoredBanner = false;
        });
      }
    });
  }

  @override
  void dispose() {
    _subscription?.cancel();
    _restoredTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final showBanner = _status != NetworkStatus.connected || _showRestoredBanner;

    return Stack(
      children: [
        widget.child,
        if (showBanner)
          Positioned(
            top: 0,
            left: 0,
            right: 0,
            child: SafeArea(
              bottom: false,
              child: Material(
                color: Colors.transparent,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 300),
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  decoration: BoxDecoration(
                    color: _getBannerColor(),
                    boxShadow: const [
                      BoxShadow(
                        color: Colors.black26,
                        blurRadius: 4,
                        offset: Offset(0, 2),
                      ),
                    ],
                  ),
                  child: Row(
                    children: [
                      Icon(
                        _getBannerIcon(),
                        color: Colors.white,
                        size: 20,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              _getBannerTitle(),
                              style: const TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.bold,
                                fontSize: 13,
                              ),
                            ),
                            Text(
                              _getBannerSubtitle(),
                              style: const TextStyle(
                                color: Colors.white70,
                                fontSize: 11,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),
      ],
    );
  }

  Color _getBannerColor() {
    if (_showRestoredBanner) return const Color(0xFF2E7D32); // Green
    switch (_status) {
      case NetworkStatus.noInternet:
        return const Color(0xFFD32F2F); // Red
      case NetworkStatus.poorNetwork:
        return const Color(0xFFED6C02); // Orange / Amber
      case NetworkStatus.connected:
        return const Color(0xFF2E7D32);
    }
  }

  IconData _getBannerIcon() {
    if (_showRestoredBanner) return Icons.wifi_rounded;
    switch (_status) {
      case NetworkStatus.noInternet:
        return Icons.wifi_off_rounded;
      case NetworkStatus.poorNetwork:
        return Icons.network_check_rounded;
      case NetworkStatus.connected:
        return Icons.wifi_rounded;
    }
  }

  String _getBannerTitle() {
    if (_showRestoredBanner) return 'Connection Restored';
    switch (_status) {
      case NetworkStatus.noInternet:
        return '⚠ No Internet Connection';
      case NetworkStatus.poorNetwork:
        return '⚠ Poor Network';
      case NetworkStatus.connected:
        return 'Connection Restored';
    }
  }

  String _getBannerSubtitle() {
    if (_showRestoredBanner) return 'Internet connection restored.';
    switch (_status) {
      case NetworkStatus.noInternet:
        return 'Please check your internet connection.';
      case NetworkStatus.poorNetwork:
        return 'Your connection is slow. Some requests may take longer.';
      case NetworkStatus.connected:
        return 'Internet connection restored.';
    }
  }
}
