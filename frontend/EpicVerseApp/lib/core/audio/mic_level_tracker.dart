import 'dart:typed_data';

/// Watches the raw microphone stream (16-bit little-endian mono PCM) during one voice turn and
/// answers two questions the screen needs: "did anything that sounds like speech arrive?" and
/// "does the mic look dead (muted, or taken by another app)?".
///
/// It only chooses which MESSAGE to show. It never blocks audio from reaching the backend, so a
/// quiet speaker the thresholds miss still gets an answer.
class MicLevelTracker {
  /// RMS (out of 32768) a chunk needs to count as "sound louder than a quiet room".
  static const int speechRms = 350;

  /// A stream whose loudest sample never exceeds this is digital silence, not a quiet room.
  static const int silencePeak = 2;

  /// Loud chunks needed before we call it speech (one click or bump is not speech).
  static const int minLoudChunks = 2;

  /// Chunks that must have arrived before "dead mic" can be claimed (a very short tap is not it).
  static const int minChunksForDeadMic = 5;

  int _chunks = 0;
  int _loudChunks = 0;
  int _peak = 0;

  int get chunks => _chunks;

  /// Number of chunks so far that were loud enough to count as sound.
  int get loudChunks => _loudChunks;

  void reset() {
    _chunks = 0;
    _loudChunks = 0;
    _peak = 0;
  }

  void addPcm16(Uint8List bytes) {
    final samples = bytes.lengthInBytes ~/ 2;
    if (samples == 0) return;
    final view = ByteData.sublistView(bytes);
    var sumSquares = 0.0;
    var chunkPeak = 0;
    for (var i = 0; i < samples; i++) {
      final v = view.getInt16(i * 2, Endian.little);
      final a = v < 0 ? -v : v;
      if (a > chunkPeak) chunkPeak = a;
      sumSquares += v * v;
    }
    _chunks++;
    if (chunkPeak > _peak) _peak = chunkPeak;
    final rms = _sqrt(sumSquares / samples);
    if (rms >= speechRms) _loudChunks++;
  }

  /// True once enough speech-level sound has been heard this turn.
  bool get heardSpeech => _loudChunks >= minLoudChunks;

  /// True when the mic delivered audio but it was pure silence the whole time
  /// (muted, privacy switch, or another app holding the microphone).
  bool get micLooksDead => _chunks >= minChunksForDeadMic && _peak <= silencePeak;

  static double _sqrt(double x) {
    if (x <= 0) return 0;
    var g = x / 2 + 1;
    for (var i = 0; i < 25; i++) {
      g = 0.5 * (g + x / g);
    }
    return g;
  }
}
