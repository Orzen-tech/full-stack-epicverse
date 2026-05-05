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
  late AnimationController _waveController;
  late AnimationController _speechVibrationController;
  late Animation<double> _vibrationAnimation;
  Timer? _talkingTimeout;
  bool _isTalking = false; // Tracks if AI is speaking
  double _currentVolume = 0.0;
  DateTime _audioFinishTime = DateTime.now();

  // Service Subscriptions
  StreamSubscription<bool>? _connSub;
  StreamSubscription<String>? _statusSub;
  StreamSubscription<dynamic>? _messageSub;

  bool _isRecording = false;
  late bool _isConnected;
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

    // Initialize Pulse Wave Effect (Ripples)
    _waveController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 8000), // Much slower idle breathing
    )..repeat();
    
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
      
      try {
        if (message is String) {
          final data = jsonDecode(message);
          
          if (data['type'] == 'response.done') {
            debugPrint('[EpicVerse][AI] response.done → LLM finished speaking');
            _stopTalking(); 
          } else if (data['type'] == 'error') {
            debugPrint('[EpicVerse][AI] error event: ${data['message'] ?? data}');
            _stopTalking();
            _handleSystemError(data);
          } else if (data['type'] == 'response.audio_transcript.delta' || data['type'] == 'transcript') {
            debugPrint('[EpicVerse][AI] transcript delta: ${data['delta'] ?? data['text']}');
          } else if (data['type'] == 'input_audio_buffer.speech_started') {
            debugPrint('[EpicVerse][STT] speech started detected by backend');
          } else if (data['type'] == 'input_audio_buffer.speech_stopped') {
            debugPrint('[EpicVerse][STT] speech stopped detected by backend');
          } else if (data['type'] == 'conversation.item.input_audio_transcription.completed') {
            debugPrint('[EpicVerse][STT] heard: ${data['transcript']}');
          }
          _handleIncomingMessage(message);
        } else if (message is Uint8List) {
          // --- Start Handler (Immediate) ---
          if (!_isTalking) {
            _startTalking();
          }
          
          // Feed high-fidelity PCM stream
          FlutterPcmSound.feed(PcmArrayInt16(bytes: message.buffer.asByteData()));
          
          // Live RMS volume -> wave height
          _updateVolumeFromPcm(message);

          // Auto-stop when audio buffer physically finishes
          // 24000 Hz * 1 channel * 2 bytes/sample = 48000 bytes/sec = 48 bytes/ms
          final int expectedDurationMs = (message.lengthInBytes / 48.0).round();
          
          if (_audioFinishTime.isBefore(DateTime.now())) {
            _audioFinishTime = DateTime.now().add(Duration(milliseconds: expectedDurationMs));
          } else {
            _audioFinishTime = _audioFinishTime.add(Duration(milliseconds: expectedDurationMs));
          }

          final int timeUntilFinish = _audioFinishTime.difference(DateTime.now()).inMilliseconds;

          _talkingTimeout?.cancel();
          _talkingTimeout = Timer(Duration(milliseconds: timeUntilFinish + 250), () {
            if (mounted && _isTalking) {
              _stopTalking();
            }
          });
        }
      } catch (e) {
        debugPrint('Error in message listener: $e');
        _stopTalking();
      }
    });

    _audioPlayer.onPlayerStateChanged.listen((state) {
      if (mounted) {
        if (state == PlayerState.playing) {
           _startTalking();
        } else if (state == PlayerState.completed || state == PlayerState.stopped) {
           _stopTalking();
        }
      }
    });

    _audioPlayer.onPlayerComplete.listen((_) => _stopTalking());
    _postInitHandshake();
  }

  // --- Animation Handlers (Production-Ready) ---
  void _startTalking() {
    if (!mounted || _isTalking) return;
    debugPrint('[EpicVerse][AI] AI started talking');
    _waveController.duration = const Duration(milliseconds: 800); // Fast!
    _waveController.repeat();
    setState(() {
      _isTalking = true;
      _speechVibrationController.repeat(reverse: true);
    });
  }

  void _stopTalking() {
    if (!mounted || !_isTalking) return;
    debugPrint('[EpicVerse][AI] AI stopped talking');
    _waveController.duration = const Duration(milliseconds: 4000); // Slow idle
    _waveController.repeat();
    setState(() {
      _isTalking = false;
      _currentVolume = 0.0;
      _statusText = "Ready";
      _talkingTimeout?.cancel();
      _speechVibrationController.stop();
      _speechVibrationController.reset();
    });

    // --- NEXT SENTENCE AUTO-PLAY ---
    _isPlayerBusy = false;
    _processAudioQueue();
  }

  void _updateVolumeFromPcm(Uint8List message) {
    if (message.isEmpty) return;
    
    // Interpret bytes as Int16 (2 bytes per sample)
    final byteData = message.buffer.asByteData();
    double sum = 0;
    int count = message.length ~/ 2;
    
    for (int i = 0; i < count; i++) {
       final sample = byteData.getInt16(i * 2, Endian.little);
       sum += sample * sample;
    }
    
    double rms = math.sqrt(sum / count);
    // Normalized volume (thresholded for EpicVerse acoustics)
    double normalized = (rms / 4500.0).clamp(0.0, 1.0);
    
    // Fast attack, smooth decay for visual fluidity
    if (mounted) {
      setState(() {
        if (normalized > _currentVolume) {
          _currentVolume = (_currentVolume * 0.2) + (normalized * 0.8);
        } else {
          _currentVolume = (_currentVolume * 0.8) + (normalized * 0.2);
        }
      });
    }
  }

  void _handleSystemError(dynamic data) {
    if (data['code'] == 'SESSION_KICKED' || data['code'] == 'SESSION_INVALID') {
       _showKickedDialog(data['message'] ?? "Logged in elsewhere.");
    }
  }

  // --- Core Lifecycle Handlers ---
  void _postInitHandshake() {
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await _initWakeWord(); 
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
        }
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
                  if (context.mounted) Navigator.pop(context);
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
    debugPrint('[EpicVerse][MIC] Mic tapped → _startVoiceTurn(timeout=${timeoutSeconds}s)');
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
    
    final hasPerm = await _audioRecorder.hasPermission();
    debugPrint('[EpicVerse][MIC] hasPermission=$hasPerm');
    if (hasPerm && mounted) {
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
      
      debugPrint('[EpicVerse][MIC] Recorder started 24kHz PCM16 mono');
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


  Future<void> _stopRecording() async {
    if (!_isRecording) return;
    debugPrint('[EpicVerse][MIC] _stopRecording');
    
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
    
    debugPrint('[EpicVerse][MIC] Sent {"type":"end"} → awaiting LLM');
    webSocketService.sendMessage('{"type": "end"}');
    
    // Optional: Keep connection alive until AI finishes speaking for smoother UX
    // We will call disconnect() in onPlayerComplete if we want a full lazy cycle.
    
    // Do NOT resume listening here. Both Android OS lock and Backend queue 
    // need time. Wake word will auto-resume in onPlayerComplete OR Error handler.
  }


  @override
  void dispose() {
    _speech.cancel();
    _micSubscription?.cancel();
    _audioRecorder.dispose();
    _audioPlayer.dispose();
    wakeWordService.dispose();
    
    // Cancel service subscriptions
    _connSub?.cancel();
    _statusSub?.cancel();
    _messageSub?.cancel();
    _waveController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      resizeToAvoidBottomInset: true,
      body: NetworkBackground(
        child: SizedBox.expand(
          child: Stack(
            children: [
              // --- Premium 3D Character Layer ---
              Positioned.fill(
                child: Stack(
                children: [
                   // Layer 1: Cinematic Cosmic Background (Floating Particles)
                   const Positioned.fill(
                     child: _CosmicParticlesBackground(),
                   ),

                   // Layer 2: Main Pattern Background (Filigree)
                   Positioned.fill(
                     child: Image.asset(
                        'assets/images/cute_companion.webp',
                        fit: BoxFit.cover,
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
                           // Main EpicVerse Logo (Centered)
                           Image.asset(
                             'assets/images/epicverse_companion_logo.webp',
                             width: 280,
                             fit: BoxFit.contain,
                           ),
                           // Lottie Mouth Animation (Overlay)
                           // Only active when AI is talking
                           Lottie.asset(
                             'assets/animations/companion_talking.json',
                             animate: _isTalking,
                             repeat: true,
                             width: 250, // Match logo scale
                             fit: BoxFit.contain,
                             errorBuilder: (context, error, stackTrace) => const SizedBox(), 
                           ),
                         ],
                       ),
                     ),
                   ),

                   // Layer 4: Premium Circular Audio Visualizer (Always Visible, Pulsing on Speech)
                   Center(
                     child: AnimatedBuilder(
                       animation: _waveController,
                       builder: (context, child) {
                         return RepaintBoundary(
                           child: CustomPaint(
                             size: const Size(300, 300),
                             painter: _CircularVisualizerPainter(
                               animationValue: _waveController.value,
                               isTalking: _isTalking,
                               volume: _currentVolume,
                             ),
                           ),
                         );
                       },
                     ),
                   ),

                   // Layer 5: Main Character Logo with "Popup" Scaling Effect
                   Center(
                     child: AnimatedScale(
                       scale: _isTalking ? 1.0 + (_currentVolume * 0.05) : 1.0,
                       duration: const Duration(milliseconds: 150),
                       curve: Curves.easeOutCubic,
                       child: TweenAnimationBuilder<double>(
                         tween: Tween<double>(begin: 1.0, end: _isTalking ? 1.05 : 1.0),
                         duration: const Duration(milliseconds: 300),
                         curve: Curves.elasticOut,
                         builder: (context, scale, child) {
                           return Transform.scale(
                             scale: scale + (0.01 * math.sin(_waveController.value * 2 * math.pi)), // Subtle micro-bounce
                             child: child,
                           );
                         },
                         child: Stack(
                           alignment: Alignment.center,
                           children: [
                             // Main EpicVerse Logo (Centered)
                             Image.asset(
                               'assets/images/epicverse_companion_logo.webp',
                               width: 280,
                               fit: BoxFit.contain,
                             ),
                             // Lottie Mouth Animation (Overlay)
                             Lottie.asset(
                               'assets/animations/companion_talking.json',
                               animate: _isTalking,
                               repeat: true,
                               width: 250, 
                               fit: BoxFit.contain,
                               errorBuilder: (context, error, stackTrace) => const SizedBox(), 
                             ),
                           ],
                         ),
                       ),
                     ),
                   ),
                ],
              ),
            ),
            
            // Character visualization base border (Static)
            Center(
              child: Container(
                width: 350,
                height: 350,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  border: Border.all(
                    color: AppColors.primaryGold.withValues(alpha: 0.02), 
                    width: 1,
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
                         const Spacer(),
                         IconButton(icon: const Icon(AppIcons.close, color: AppColors.textMuted, size: 20), onPressed: () => Navigator.pop(context)),
                      ],
                    ),
                  ),
                  
                  const Spacer(flex: 2),
                  
                  const SizedBox(height: 16),
                  
                  const Text(
                    '',
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
                          color: _isConnected ? Colors.greenAccent : AppColors.primaryGold.withValues(alpha: 0.3),
                          boxShadow: [
                            BoxShadow(
                              color: (_isConnected ? Colors.greenAccent : AppColors.primaryGold).withValues(alpha: 0.3),
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
                                                  AppColors.primaryGold.withValues(alpha: 0.5),
                                                  AppColors.primaryGold.withValues(alpha: 0.0),
                                                ],
                                                radius: 0.8,
                                              )
                                            : LinearGradient(
                                                begin: Alignment.topLeft,
                                                end: Alignment.bottomRight,
                                                colors: [
                                                  AppColors.primaryGold.withValues(alpha: 0.15),
                                                  AppColors.primaryGold.withValues(alpha: 0.05),
                                                ],
                                              ),
                                          border: Border.all(
                                            color: AppColors.primaryGold.withValues(alpha: 0.4),
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
                                          color: AppColors.primaryGold.withValues(alpha: 0.5),
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
      ..color = const Color(0xFFC5A358).withValues(alpha: 0.12) // Gold dust
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

class _CircularVisualizerPainter extends CustomPainter {
  final double animationValue;
  final bool isTalking;
  final double volume;

  _CircularVisualizerPainter({
    required this.animationValue, 
    required this.isTalking,
    required this.volume,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final center = Offset(size.width / 2, size.height / 2);
    final radius = (size.width / 2) - 30; // Leave room for rays
    
    const int barCount = 140; 
    final random = math.Random(123); // Consistent noise

    for (int i = 0; i < barCount; i++) {
      final double angle = (i * 2 * math.pi) / barCount;
      final double staticNoise = random.nextDouble();
      
      double barHeight;
      if (isTalking) {
        // Talking: bars react to frequency simulation + real volume
        final double intensity = i % 7 == 0 ? 1.2 : 0.6; // Variable spike intensity
        final double baseExpansion = 15;
        final double simulatedVolume = 0.4 + 0.6 * math.sin(animationValue * 2 * math.pi + i * 0.4).abs();
        final double activeVol = math.max(volume, simulatedVolume); // never silent while talking
        
        barHeight = 10 + baseExpansion + (intensity * 18) + (staticNoise * 25 * activeVol);
      } else {
        // Idle: gentle slow pulse
        final double idle = 0.5 + 0.5 * math.sin(animationValue * 2 * math.pi);
        barHeight = 6 + (staticNoise * 6) + (idle * 12);
      }
      
      final startOffset = Offset(
        center.dx + radius * math.cos(angle),
        center.dy + radius * math.sin(angle),
      );
      final endOffset = Offset(
        center.dx + (radius + barHeight) * math.cos(angle),
        center.dy + (radius + barHeight) * math.sin(angle),
      );

      final linePaint = Paint()
        ..color = AppColors.primaryGold.withValues(alpha: isTalking ? (0.8 + 0.2 * volume).clamp(0.8, 1.0) : 0.7)
        ..strokeWidth = 2.8
        ..strokeCap = StrokeCap.round;

      canvas.drawLine(startOffset, endOffset, linePaint);
    }
  }

  @override
  bool shouldRepaint(_CircularVisualizerPainter oldDelegate) => true;
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
