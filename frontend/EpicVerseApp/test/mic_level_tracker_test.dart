import 'dart:math' as math;
import 'dart:typed_data';

import 'package:epicverse/core/audio/mic_level_tracker.dart';
import 'package:flutter_test/flutter_test.dart';

/// One chunk of 16-bit little-endian mono PCM: a sine of the given amplitude (0..32767).
Uint8List _chunk(int amplitude, {int samples = 2048}) {
  final bytes = ByteData(samples * 2);
  for (var i = 0; i < samples; i++) {
    final v = (amplitude * math.sin(2 * math.pi * 220 * i / 24000)).round();
    bytes.setInt16(i * 2, v, Endian.little);
  }
  return bytes.buffer.asUint8List();
}

void main() {
  test('speech-level audio is recognised after a couple of loud chunks', () {
    final t = MicLevelTracker();
    for (var i = 0; i < 6; i++) {
      t.addPcm16(_chunk(4000));
    }
    expect(t.heardSpeech, isTrue);
    expect(t.micLooksDead, isFalse);
  });

  test('quiet room noise is neither speech nor a dead mic', () {
    final t = MicLevelTracker();
    for (var i = 0; i < 20; i++) {
      t.addPcm16(_chunk(80)); // rms ~ 57, well under the speech level, well over digital silence
    }
    expect(t.heardSpeech, isFalse);
    expect(t.micLooksDead, isFalse);
  });

  test('pure digital silence over enough chunks means the mic is muted or taken', () {
    final t = MicLevelTracker();
    for (var i = 0; i < 10; i++) {
      t.addPcm16(Uint8List(4096));
    }
    expect(t.heardSpeech, isFalse);
    expect(t.micLooksDead, isTrue);
  });

  test('a very short tap is not reported as a dead mic', () {
    final t = MicLevelTracker();
    t.addPcm16(Uint8List(4096));
    t.addPcm16(Uint8List(4096));
    expect(t.micLooksDead, isFalse);
  });

  test('one loud bump is not speech, two are', () {
    final t = MicLevelTracker();
    t.addPcm16(_chunk(6000));
    expect(t.heardSpeech, isFalse);
    t.addPcm16(_chunk(6000));
    expect(t.heardSpeech, isTrue);
  });

  test('quiet speech just above the threshold still counts', () {
    final t = MicLevelTracker();
    for (var i = 0; i < 4; i++) {
      t.addPcm16(_chunk(700)); // rms ~ 495
    }
    expect(t.heardSpeech, isTrue);
  });

  test('empty or odd-length data is ignored without crashing', () {
    final t = MicLevelTracker();
    t.addPcm16(Uint8List(0));
    t.addPcm16(Uint8List(1));
    expect(t.chunks, 0);
    t.addPcm16(Uint8List.fromList([0, 0, 1])); // 1 whole sample + a stray byte
    expect(t.chunks, 1);
  });

  test('reset starts a fresh turn', () {
    final t = MicLevelTracker();
    for (var i = 0; i < 5; i++) {
      t.addPcm16(_chunk(4000));
    }
    expect(t.heardSpeech, isTrue);
    t.reset();
    expect(t.heardSpeech, isFalse);
    expect(t.chunks, 0);
  });
}
