import 'dart:async';
import 'dart:math' as math;
import 'dart:convert';
import 'dart:ui' as ui;
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:speech_to_text/speech_to_text.dart' as stt;
import 'package:record/record.dart';
import 'package:audioplayers/audioplayers.dart';
import 'package:flutter_pcm_sound/flutter_pcm_sound.dart';
import 'package:lottie/lottie.dart';
import '../../core/constants/app_colors.dart';
import '../../core/constants/app_assets.dart';
import '../widgets/network_background.dart';
import '../../core/network/websocket_service.dart';
import '../../services/wake_word_service.dart';
import '../../core/network/api_config.dart';
import 'login_screen.dart';

class CompanionReadyScreen extends StatefulWidget {
  final String gameMode;
  const CompanionReadyScreen({super.key, required this.gameMode});

  @override
  State<CompanionReadyScreen> createState() => _CompanionReadyScreenState();
}

class _CompanionReadyScreenState extends State<CompanionReadyScreen> with TickerProviderStateMixin {
  late stt.SpeechToText _speech;
  bool _isListeningWakeWord = false;
  late String _statusText;
  
  final AudioRecorder _audioRecorder = AudioRecorder();
  final AudioPlayer _audioPlayer = AudioPlayer();
  StreamSubscription<Uint8List>? _micSubscription;
  late AnimationController _speechVibrationController;
  late Animation<double> _vibrationAnimation;
  bool _isTalking = false; // Tracks if AI is speaking
  
  // Service Subscriptions
  StreamSubscription<bool>? _connSub;
  StreamSubscription<String>? _statusSub;
  StreamSubscription<dynamic>? _messageSub;

  bool _isRecording = false;
  late bool _isConnected;
  final TextEditingController _textController = TextEditingController();
  Timer? _recordingTimer;

  @override
  void initState() {
    super.initState();
    _speech = stt.SpeechToText();

    // Initialize Speech Vibration (Mouth Action)
    _speechVibrationController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 120),
    );
    _vibrationAnimation = Tween<double>(begin: 0.0, end: 1.0).animate(
      CurvedAnimation(parent: _speechVibrationController, curve: Curves.easeInOutSine),
    );
    
    // Initialize Professional PCM Sound Engine (24kHz Mono Int16)
    _initPcmSound();
    
    // Lazy Handshake: Connection is now triggered ONLY by Mic Click
    _statusText = "Tap mic to start";
    _isConnected = false;
    _isListeningWakeWord = false;

    // Listen to global service updates
    // Manual Start Only: Wake Word and Active Listening removed from Auto-Sync
    _connSub = webSocketService.connectionState.listen((connected) {
      if (mounted) {
        setState(() => _isConnected = connected);
        if (connected) {
           _statusText = "Ready";
        }
      }
    });

    _statusSub = webSocketService.statusTextStream.listen((status) {
      if (mounted) setState(() => _statusText = status);
    });

    _messageSub = webSocketService.messages.listen((message) {
      if (!mounted) return;
      _handleIncomingMessage(message);
    });

    _audioPlayer.onPlayerStateChanged.listen((state) {
      if (mounted) {
        final isNowTalking = (state == PlayerState.playing);
        setState(() => _isTalking = isNowTalking);
        if (isNowTalking) {
           _speechVibrationController.repeat(reverse: true);
        } else {
           _speechVibrationController.stop();
           _speechVibrationController.reset();
           
           // If stopping because it finished, this shouldn't be needed here 
           // but we'll use onPlayerComplete for queue progression
        }
      }
    });

    _audioPlayer.onPlayerComplete.listen((_) {
      if (mounted) {
        setState(() {
          _isTalking = false;
          _statusText = "Ready";
          _speechVibrationController.stop();
          _speechVibrationController.reset();
        });
        
        // Lazy Handshake Cycle: Disconnect after AI finishes speaking to save resources
        // webSocketService.disconnect(); // TEMPORARILY DISABLED TO ENSURE KEEP-ALIVE
        
        // --- NEXT SENTENCE AUTO-PLAY ---
        _isPlayerBusy = false;
        _processAudioQueue();
      }
    });

    // --- AUTO-SYNC CONNECTION ON LOAD ---
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_isConnected) {
         _startVoiceTurn(); // Triggers lazy handshake with correct widget.gameMode
      }
    });
  }

  // Audio Playback Queue to prevent skipping/cutting sentences 👂🎧
  final List<Uint8List> _audioQueue = [];
  bool _isPlayerBusy = false;

  void _handleIncomingMessage(dynamic message) {
    try {
      if (message is String) {
        final data = jsonDecode(message);
        if (data['type'] == 'mode_change' && data['newMode'] != null) {
          debugPrint("Mode switched by AI to: ${data['newMode']}");
          webSocketService.updateMode(data['newMode']);
          setState(() => _statusText = "Switched to ${data['newMode']}!");
          return;
        }
        
        if (data['status'] != null) {
           if (_isRecording) _stopRecording(); 
           setState(() => _statusText = "${data['status']}: ${data['message'] ?? ""}");
        } else if (data['type'] == 'error' && (data['code'] == 'SESSION_KICKED' || data['code'] == 'SESSION_INVALID')) {
           _showKickedDialog(data['message'] ?? "Logged in elsewhere.");
        } else if (data['type'] == 'response.done') {
           // AI Finished Speaking - Clear visual state
           if (mounted) {
              setState(() {
                _isTalking = false;
                _speechVibrationController.stop();
                _speechVibrationController.reset();
              });
           }
        }
      } else if (message is Uint8List) {
        // [REALTIME FIX] Feed high-fidelity PCM stream directly to hardware
        // Convert Uint8List to ByteData for PcmArrayInt16
        FlutterPcmSound.feed(PcmArrayInt16(bytes: message.buffer.asByteData()));
        
        // Mouth Action trigger
        if (!_isTalking) {
           setState(() => _isTalking = true);
           _speechVibrationController.repeat(reverse: true);
        }
        
        // Reset talking state after a small silence if needed, 
        // but for now, we rely on the backend 'response.done'
      }
    } catch (e) {
      debugPrint('Error parsing message: $e');
    }
  }

  void _showKickedDialog(String message) {
    if (!mounted) return;
    
    showDialog(
      context: context,
      barrierDismissible: false,
      builder: (context) => WillPopScope(
        onWillPop: () async => false,
        child: AlertDialog(
          backgroundColor: AppColors.surfaceElevated,
          title: const Text('Session Ended', style: TextStyle(color: AppColors.primaryGold)),
          content: Text(message, style: const TextStyle(color: AppColors.textPrimary)),
          actions: [
            TextButton(
              onPressed: () {
                Navigator.of(context).popUntil((route) => route.isFirst);
                Navigator.of(context).pushReplacement(
                  MaterialPageRoute(builder: (_) => const LoginScreen()),
                );
              },
              child: const Text('OK', style: TextStyle(color: AppColors.primaryGold)),
            ),
          ],
        ),
      ),
    );
  }


  Future<void> _initPcmSound() async {
    try {
      await FlutterPcmSound.setup(sampleRate: 24000, channelCount: 1);
      // Removed FlutterPcmSound.play() as it is not supported in this version 
      // and playback starts automatically on feed in the Android implementation.
    } catch (e) {
      debugPrint('Error setting up PCM sound: $e');
    }
  }

  Future<void> _processAudioQueue() async {
    // Legacy logic - Keep for backward compat or remove if unused 🧘
  }

  Future<void> _initWakeWord() async {
    debugPrint("CompanionScreen: Initializing wake word...");
    await wakeWordService.init(
      onWakeWordDetected: () {
        if (!_isRecording && mounted) {
          _startVoiceTurn();
        }
      },
    );
    debugPrint("CompanionScreen: Starting wake word listener...");
    await wakeWordService.startListening();
    if (mounted) {
      setState(() {
        _isListeningWakeWord = true;
      });
    }
  }

  void _showWakeWordSettings() {
    showDialog(
      context: context,
      builder: (context) => AlertDialog(
        backgroundColor: AppColors.surfaceElevated,
        title: const Text('Wake Word Settings', style: TextStyle(color: AppColors.primaryGold)),
        content: SizedBox(
          width: double.maxFinite,
          child: ListView.builder(
            shrinkWrap: true,
            itemCount: BuiltInKeyword.values.length,
            itemBuilder: (context, index) {
              final keyword = BuiltInKeyword.values[index];
              return ListTile(
                title: Text(keyword.name, style: const TextStyle(color: AppColors.textPrimary)),
                trailing: wakeWordService.currentKeyword == keyword 
                  ? const Icon(Icons.check_circle, color: AppColors.primaryGold) 
                  : null,
                onTap: () async {
                  await wakeWordService.changeKeyword(keyword);
                  if (mounted) Navigator.pop(context);
                  setState(() {});
                },
              );
            },
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Close', style: TextStyle(color: AppColors.textMuted)),
          ),
        ],
      ),
    );
  }

  Future<void> _startVoiceTurn([int timeoutSeconds = 5]) async {
    // 1. Establish Lazy Handshake if not already connected
    if (!_isConnected || !webSocketService.isConnected) {
      if (mounted) setState(() => _statusText = "Authenticating...");
      try {
        debugPrint("CompanionReady: Connecting to: ${ApiConfig.baseUrl}");
        debugPrint("CompanionReady: Starting Handshake for ${widget.gameMode}...");
        await webSocketService.connect(game_mode: widget.gameMode)
            .timeout(const Duration(seconds: 10));
        debugPrint("CompanionReady: Handshake SUCCESSFUL");
      } catch (e) {
        debugPrint("CompanionReady: Handshake FAILED: $e");
        if (mounted) setState(() => _statusText = "Connection Failed: $e\n(Is Ngrok Online?)");
        return;
      }
    }

    // 2. Guarantee backend enters voice mode 
    webSocketService.sendMessage(jsonEncode({"type": "stop_wakeword"}));
    
    if (await _audioRecorder.hasPermission() && mounted) {
      setState(() {
        _isRecording = true;
        _isListeningWakeWord = false;
        // If not explicitly "Listening for follow-up...", keep it general
        if (_statusText != "Listening for follow-up...") {
            _statusText = "Listening...";
        }
      });
      
      debugPrint("--- VOICE TURN STARTED ($timeoutSeconds s) ---");
      
      // OPTIMIZATION: Unified 16kHz PCM for low-latency ngrok relay
      final stream = await _audioRecorder.startStream(const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        numChannels: 1,
        sampleRate: 24000,
        bitRate: 384000, // 24 * 1000 * 16 / 1
      ));
      
      _micSubscription = stream.listen((data) {
        // [AUDIT] Binary relay certified
        webSocketService.sendMessage(data);
      });
      
      // Auto-stop recording after N seconds to get the response
      _recordingTimer?.cancel();
      _recordingTimer = Timer(Duration(seconds: timeoutSeconds), () {
        if (_isRecording) {
          debugPrint("--- AUTO-STOPPING VOICE TURN ---");
          _stopRecording();
        }
      });
    }
  }

  void _sendTextQuery() {
    final text = _textController.text.trim();
    if (text.isEmpty) return;
    if (!_isConnected) {
      setState(() => _statusText = "Wait... not connected.");
      return;
    }
    
    webSocketService.sendMessage(jsonEncode({
      "type": "text_query",
      "text": text
    }));
    
    setState(() {
      _textController.clear();
      FocusScope.of(context).unfocus();
    });
  }

  Future<void> _stopRecording() async {
    if (!_isRecording) return;
    
    // Stop recording first to flush the stream
    _recordingTimer?.cancel();
    await _audioRecorder.stop();
    // Small delay to ensure last chunks are processed
    await Future.delayed(const Duration(milliseconds: 200));
    await _micSubscription?.cancel();
    _micSubscription = null;
    
    if (!mounted) return;
    setState(() {
      _isRecording = false;
    });
    
    webSocketService.sendMessage('{"type": "end"}');
    
    // Optional: Keep connection alive until AI finishes speaking for smoother UX
    // We will call disconnect() in onPlayerComplete if we want a full lazy cycle.
    
    // Do NOT resume listening here. Both Android OS lock and Backend queue 
    // need time. Wake word will auto-resume in onPlayerComplete OR Error handler.
  }

  Future<void> _playAudioBytes(Uint8List bytes) async {
    // AudioPlayer handles bytes automatically; using MP3 results in faster loading
    await _audioPlayer.play(BytesSource(bytes));
  }

  @override
  void dispose() {
    _speech.cancel();
    _micSubscription?.cancel();
    _audioRecorder.dispose();
    _audioPlayer.dispose();
    wakeWordService.dispose();
    _textController.dispose();
    
    // Cancel service subscriptions
    _connSub?.cancel();
    _statusSub?.cancel();
    _messageSub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      resizeToAvoidBottomInset: true,
      body: NetworkBackground(
        child: Stack(
          children: [
            // --- Premium 3D Character Layer ---
            Positioned.fill(
              child: Stack(
                alignment: Alignment.center,
                children: [
                   // Layer 1: Cinematic Cosmic Background (Floating Particles)
                   const Positioned.fill(
                     child: _CosmicParticlesBackground(),
                   ),

                   // Layer 2: Blurred Deep Background (Motion Base)
                   Positioned.fill(
                     child: Opacity(
                       opacity: 0.3,
                       child: ImageFiltered(
                         imageFilter: ui.ImageFilter.blur(sigmaX: 8, sigmaY: 8),
                         child: Image.asset(
                            'assets/images/cute_companion.png',
                            fit: BoxFit.cover,
                         ),
                       ),
                     ),
                   ),
                   
                   // Layer 3: Main Character with Advanced "Video-Style" Animation
                   Positioned.fill(
                     child: TweenAnimationBuilder<double>(
                       tween: Tween<double>(begin: 0.0, end: _isTalking ? 1.0 : 0.0), 
                       duration: const Duration(milliseconds: 600),
                       curve: Curves.easeInOutExpo,
                       builder: (context, talkValue, child) {
                         return AnimatedBuilder(
                           animation: _speechVibrationController,
                           builder: (context, vibChild) {
                             // 1. Slow Breathing Sync (Base Life)
                             final breathing = 0.005 * (vibChild != null ? 1.0 : 0.5); // Always breathing
                             
                             // 2. High Frequency Mouth Vibration (Speech)
                             return Transform(
                               alignment: Alignment.center,
                               transform: Matrix4.identity()
                                 ..setEntry(3, 2, 0.001) // 3D Perspective
                                 ..rotateX(0.002 * math.cos(DateTime.now().millisecondsSinceEpoch / 2000))
                                 ..rotateY(0.01 * math.sin(DateTime.now().millisecondsSinceEpoch / 3000))
                                 ..scale(1.0 + breathing),
                               child: vibChild,
                             );
                           },
                           child: child,
                         );
                       },
                       child: Stack(
                         alignment: Alignment.center,
                         children: [
                           // Base Character Image (Fallback/Background)
                           Image.asset(
                             'assets/images/cute_companion.png',
                             fit: BoxFit.cover,
                           ),
                           // Lottie Mouth Animation (Overlay)
                           // Only active when AI is talking
                           Lottie.asset(
                             'assets/animations/companion_talking.json',
                             animate: _isTalking,
                             repeat: true,
                             fit: BoxFit.cover,
                             errorBuilder: (context, error, stackTrace) => const SizedBox(), // Hide if file missing
                           ),
                         ],
                       ),
                     ),
                   ),

                   // Layer 4: Divine Aura Glow
                   if (_isTalking)
                     Center(
                       child: Container(
                         width: 450,
                         height: 450,
                         decoration: BoxDecoration(
                           shape: BoxShape.circle,
                           boxShadow: [
                             BoxShadow(
                               color: AppColors.primaryGold.withOpacity(0.08),
                               blurRadius: 120,
                               spreadRadius: 30,
                             )
                           ],
                         ),
                       ),
                     ),
                ],
              ),
            ),
            
            // Character visualization base border
            Center(
              child: Container(
                width: 350,
                height: 350,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: AppColors.primaryGold.withOpacity(0.05), 
                    width: 1,
                  ),
                ),
                child: Center(
                  child: Container(
                    width: 250,
                    height: 250,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      border: Border.all(
                        color: AppColors.primaryGold.withOpacity(0.1), 
                        width: 2,
                      ),
                    ),
                  ),
                ),
              ),
            ),

            SafeArea(
              child: Column(
                children: [
                   Padding(
                    padding: const EdgeInsets.all(24.0),
                    child: Row(
                      children: [
                         const Icon(AppIcons.companion, color: AppColors.primaryGold, size: 24),
                         const Spacer(),
                         IconButton(icon: const Icon(AppIcons.close, color: AppColors.textMuted, size: 20), onPressed: () => Navigator.pop(context)),
                      ],
                    ),
                  ),
                  
                  const Spacer(flex: 2),
                  
                  const SizedBox(height: 16),
                  
                  const Text(
                    'COMPANION READY',
                    style: TextStyle(
                      color: AppColors.primaryGold, 
                      fontSize: 18, 
                      letterSpacing: 4, 
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                  
                  const SizedBox(height: 12),
                  
                  // Connection Indicator Row
                  Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: _isConnected ? Colors.greenAccent : AppColors.primaryGold.withOpacity(0.3),
                          boxShadow: [
                            BoxShadow(
                              color: (_isConnected ? Colors.greenAccent : AppColors.primaryGold).withOpacity(0.3),
                              blurRadius: 4,
                              spreadRadius: 2,
                            )
                          ],
                        ),
                      ),
                      const SizedBox(width: 8),
                      Flexible(
                        child: Text(
                          _statusText,
                          textAlign: TextAlign.center,
                          style: const TextStyle(color: AppColors.textSecondary, fontSize: 13),
                        ),
                      ),
                    ],
                  ),
                  
                  const Spacer(),

                    // Repositioned Mic Button with Pentagon Background
                    Padding(
                      padding: const EdgeInsets.only(bottom: 40.0, right: 30.0),
                      child: Align(
                        alignment: Alignment.bottomRight,
                        child: GestureDetector(
                          onTap: () => _isRecording ? _stopRecording() : _startVoiceTurn(10),
                          onLongPressStart: (_) => _startVoiceTurn(60),
                          onLongPressEnd: (_) => _stopRecording(),
                          child: TweenAnimationBuilder<double>(
                            tween: Tween<double>(begin: 1.0, end: _isRecording ? 1.4 : 1.0),
                            duration: const Duration(milliseconds: 300),
                            curve: Curves.elasticOut,
                            builder: (context, scale, child) {
                              return Transform.scale(
                                scale: scale,
                                child: Stack(
                                  alignment: Alignment.center,
                                  children: [
                                    // Pentagon Background Component
                                    ClipPath(
                                      clipper: PentagonClipper(),
                                      child: AnimatedContainer(
                                        duration: const Duration(milliseconds: 300),
                                        width: 80,
                                        height: 80,
                                        decoration: BoxDecoration(
                                          gradient: _isRecording 
                                            ? RadialGradient(
                                                colors: [
                                                  AppColors.primaryGold.withOpacity(0.5),
                                                  AppColors.primaryGold.withOpacity(0.0),
                                                ],
                                                radius: 0.8,
                                              )
                                            : LinearGradient(
                                                begin: Alignment.topLeft,
                                                end: Alignment.bottomRight,
                                                colors: [
                                                  AppColors.primaryGold.withOpacity(0.15),
                                                  AppColors.primaryGold.withOpacity(0.05),
                                                ],
                                              ),
                                          border: Border.all(
                                            color: AppColors.primaryGold.withOpacity(0.4),
                                            width: 1,
                                          ),
                                        ),
                                      ),
                                    ),
                                    
                                    // Mic Icon
                                    Icon(
                                      _isRecording ? Icons.mic_rounded : Icons.mic_none_rounded,
                                      color: AppColors.primaryGold,
                                      size: 32,
                                      shadows: [
                                        Shadow(
                                          color: AppColors.primaryGold.withOpacity(0.5),
                                          blurRadius: _isRecording ? 20 : 10,
                                        )
                                      ],
                                    ),
                                  ],
                                ),
                              );
                            },
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

// --- Cinematic Video Background Components ---

class _CosmicParticlesBackground extends StatefulWidget {
  const _CosmicParticlesBackground();

  @override
  State<_CosmicParticlesBackground> createState() => _CosmicParticlesBackgroundState();
}

class _CosmicParticlesBackgroundState extends State<_CosmicParticlesBackground> with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 10),
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _controller,
      builder: (context, child) {
        return CustomPaint(
          painter: _CosmicPainter(_controller.value),
        );
      },
    );
  }
}

class _CosmicPainter extends CustomPainter {
  final double animationValue;
  final List<Offset> _points = List.generate(40, (i) {
    var random = math.Random(i);
    return Offset(random.nextDouble(), random.nextDouble());
  });

  _CosmicPainter(this.animationValue);

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = const Color(0xFFC5A358).withOpacity(0.12) // Gold dust
      ..strokeCap = StrokeCap.round;

    for (int i = 0; i < _points.length; i++) {
      final p = _points[i];
      // Slow vertical drift
      double dy = (p.dy - (animationValue * 0.1)) % 1.0;
      if (dy < 0) dy += 1.0;
      
      // Horizontal wave
      double dx = p.dx + 0.015 * math.sin(animationValue * 2 * math.pi + (i * 1.5));
      
      final pos = Offset(dx * size.width, dy * size.height);
      final radius = 1.0 + (i % 3).toDouble();
      
      canvas.drawCircle(pos, radius, paint);
    }
  }

  @override
  bool shouldRepaint(_CosmicPainter oldDelegate) => true;
}

class PentagonClipper extends CustomClipper<Path> {
  @override
  Path getClip(Size size) {
    Path path = Path();
    double radius = size.width / 2;
    double centerX = size.width / 2;
    double centerY = size.height / 2;
    double angle = (2 * math.pi) / 5;
    
    // Start at top (adjust by -pi/2 to make it point upwards)
    path.moveTo(centerX + radius * math.cos(-math.pi / 2),
                centerY + radius * math.sin(-math.pi / 2));
    
    for (int i = 1; i <= 5; i++) {
      path.lineTo(centerX + radius * math.cos(-math.pi / 2 + i * angle),
                  centerY + radius * math.sin(-math.pi / 2 + i * angle));
    }
    path.close();
    return path;
  }

  @override
  bool shouldReclip(CustomClipper<Path> oldClipper) => false;
}
