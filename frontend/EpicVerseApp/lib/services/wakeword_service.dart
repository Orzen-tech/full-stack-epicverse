import 'dart:async';
import 'dart:typed_data';
import 'package:flutter/services.dart';
import 'package:onnxruntime/onnxruntime.dart';
import 'package:record/record.dart';

/// WakewordService listens from the microphone and detects the "Hey Epic" wakeword.
/// It chains 3 ONNX models:
///   1. melspectrogram.onnx  — raw audio → frequency map
///   2. embedding_model.onnx — frequency map → voice features
///   3. hey_epic_single.onnx — voice features → detection score
class WakewordService {
  // Model sessions
  OrtSession? _melSession;
  OrtSession? _embedSession;
  OrtSession? _wakewordSession;

  // Audio recorder
  final AudioRecorder _recorder = AudioRecorder();

  // Detection threshold — score > 0.5 means "Hey Epic" is detected
  final double threshold;

  // Callback that fires when "Hey Epic" is heard
  final void Function() onWakewordDetected;

  bool _isRunning = false;

  static const int _sampleRate = 16000;    // 16 kHz
  static const int _chunkSize = 1280;       // 80ms chunks
  static const int _melFrameSize = 32;      // mel feature size expected by embedding model

  WakewordService({
    required this.onWakewordDetected,
    this.threshold = 0.5,
  });

  /// Load the 3 ONNX models from assets into memory.
  Future<void> initialize() async {
    OrtEnv.instance.init();

    _melSession = await _loadModel('assets/wakeword/melspectrogram.onnx');
    _embedSession = await _loadModel('assets/wakeword/embedding_model.onnx');
    _wakewordSession = await _loadModel('assets/wakeword/hey_epic_single.onnx');
  }

  Future<OrtSession> _loadModel(String assetPath) async {
    final bytes = await rootBundle.load(assetPath);
    final modelData = bytes.buffer.asUint8List();
    final opts = OrtSessionOptions()
      ..setInterOpNumThreads(1)
      ..setIntraOpNumThreads(1);
    return OrtSession.fromBuffer(modelData, opts);
  }

  /// Start listening from the microphone for the "Hey Epic" wakeword.
  Future<void> startListening() async {
    if (_isRunning) return;
    if (_melSession == null) await initialize();

    _isRunning = true;

    // Start recording — we stream 16kHz mono PCM audio
    final stream = await _recorder.startStream(
      const RecordConfig(
        encoder: AudioEncoder.pcm16bits,
        sampleRate: _sampleRate,
        numChannels: 1,
      ),
    );

    // Buffer to accumulate audio chunks
    List<int> audioBuffer = [];

    stream.listen((data) async {
      if (!_isRunning) return;

      // Accumulate bytes
      audioBuffer.addAll(data);

      // Process in _chunkSize-sample (2 bytes each) chunks
      while (audioBuffer.length >= _chunkSize * 2) {
        final chunk = audioBuffer.sublist(0, _chunkSize * 2);
        audioBuffer = audioBuffer.sublist(_chunkSize * 2);

        // Convert raw bytes to int16 samples
        final int16Data = Int16List.view(Uint8List.fromList(chunk).buffer);
        // Normalize to float32 in range [-1.0, 1.0]
        final float32Data = Float32List.fromList(
          int16Data.map((s) => s / 32768.0).toList(),
        );

        final score = await _runPipeline(float32Data);
        if (score >= threshold) {
          onWakewordDetected();
        }
      }
    });
  }

  /// Stop listening.
  Future<void> stopListening() async {
    _isRunning = false;
    await _recorder.stop();
  }

  /// Dispose all resources.
  Future<void> dispose() async {
    await stopListening();
    _melSession?.release();
    _embedSession?.release();
    _wakewordSession?.release();
    OrtEnv.instance.release();
  }

  // Buffer to store melspectrogram frames for "Memory"
  final List<List<double>> _melBuffer = [];
  static const int _requiredMelFrames = 76;

  /// Runs the audio chunk through the full 3-model pipeline.
  Future<double> _runPipeline(Float32List audioChunk) async {
    try {
      // --- Stage 1: Melspectrogram ---
      final melInput = OrtValueTensor.createTensorWithDataList(
        audioChunk,
        [1, audioChunk.length],
      );
      final melIn = _melSession!.inputNames;
      final melOutput = await _melSession!.runAsync(
        OrtRunOptions(),
        {melIn.first: melInput},
      );
      
      // The melspec output is [1, N, 32]
      final result = melOutput?.first?.value as List<List<List<double>>>?;
      final newFrames = result?.first ?? [];
      
      melInput.release();
      melOutput?.forEach((e) => e?.release());

      // --- THE SECRET FORMULA: Match the training data scaling ---
      for (var frame in newFrames) {
        final scaledFrame = frame.map((val) => (val / 10.0) + 2.0).toList();
        _melBuffer.add(scaledFrame);
      }

      // Keep only what we need (sliding window)
      if (_melBuffer.length > 200) {
        _melBuffer.removeRange(0, _melBuffer.length - 100);
      }

      // We need at least 76 frames for a valid "Hey Epic" detection
      if (_melBuffer.length < _requiredMelFrames) return 0.0;

      // Take the last 76 frames (current window)
      final window = _melBuffer.sublist(_melBuffer.length - _requiredMelFrames);
      final flatWindow = Float32List.fromList(window.expand((x) => x).toList());

      // --- Stage 2: Embedding ---
      // Expected shape [1, 76, 32, 1]
      final embedInput = OrtValueTensor.createTensorWithDataList(
        flatWindow,
        [1, _requiredMelFrames, _melFrameSize, 1],
      );
      final embedIn = _embedSession!.inputNames;
      final embedOutput = await _embedSession!.runAsync(
        OrtRunOptions(),
        {embedIn.first: embedInput},
      );
      final embedData = (embedOutput?.first?.value as List<List<double>>?)
              ?.first
              .map((e) => e.toDouble())
              .toList() ??
          [];
      embedInput.release();
      embedOutput?.forEach((e) => e?.release());

      if (embedData.isEmpty) return 0.0;

      // --- Stage 3: Wakeword Detection ---
      // Support both 96 and 128 dim models inferred from data length
      final expectedDim = embedData.length >= 128 ? 128 : 96;
      final wakeInputData = Float32List.fromList(
          embedData.take(expectedDim).map((e) => e.toDouble()).toList());
          
      final wakeInput = OrtValueTensor.createTensorWithDataList(
        wakeInputData,
        [1, expectedDim],
      );
      final wakeIn = _wakewordSession!.inputNames;
      final wakeOutput = await _wakewordSession!.runAsync(
        OrtRunOptions(),
        {wakeIn.first: wakeInput},
      );
      final score = (wakeOutput?.first?.value as List<List<double>>?)
              ?.first.first ??
          0.0;
      wakeInput.release();
      wakeOutput?.forEach((e) => e?.release());

      return score;
    } catch (e) {
      return 0.0;
    }
  }
}
