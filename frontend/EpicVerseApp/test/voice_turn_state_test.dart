import 'package:epicverse/core/audio/voice_turn_state.dart';
import 'package:flutter_test/flutter_test.dart';

MicStopOutcome _stop(VoiceTurnState t, {bool dead = false}) => t.onMicStopped(micLooksDead: dead);

void main() {
  test('nothing said (the backend never detected speech): "I did not hear anything"', () {
    expect(_stop(VoiceTurnState()), MicStopOutcome.noSpeech);
  });

  test('nothing said and the mic is dead: mic message', () {
    expect(_stop(VoiceTurnState(), dead: true), MicStopOutcome.micSilent);
  });

  test('a dead-mic reading never hides real speech the backend detected', () {
    final t = VoiceTurnState()..onSpeechStarted();
    expect(_stop(t, dead: true), isNot(MicStopOutcome.micSilent));
  });

  test('a normal answered turn: when the mic stops, no "working on it" sequence', () {
    final t = VoiceTurnState()
      ..onSpeechStarted()
      ..onSpeechStopped()
      ..onResponseHandled();
    expect(_stop(t), MicStopOutcome.readyNext);
  });

  test('"next" (the AI stays silent): mic stop does NOT start the false "taking longer" sequence', () {
    final t = VoiceTurnState()
      ..onSpeechStarted()
      ..onSpeechStopped()
      ..onResponseHandled(); // stay_silent counts as handled
    expect(_stop(t), MicStopOutcome.readyNext);
  });

  test('speech detected, the user stopped first, nothing else arrived yet: start waiting', () {
    final t = VoiceTurnState()..onSpeechStarted();
    expect(_stop(t), MicStopOutcome.startThinking);
  });

  test('backend said the user stopped, reply not here yet: keep waiting', () {
    final t = VoiceTurnState()
      ..onSpeechStarted()
      ..onSpeechStopped();
    expect(t.awaitingReply, isTrue);
    expect(_stop(t), MicStopOutcome.keepThinking);
  });

  test('new speech after a reply: waiting again, not "ready"', () {
    final t = VoiceTurnState()
      ..onSpeechStarted()
      ..onSpeechStopped()
      ..onResponseHandled()
      ..onSpeechStarted();
    expect(_stop(t), MicStopOutcome.startThinking);
  });

  test('speech_stopped while a reply is pending, then the reply arrives: ready', () {
    final t = VoiceTurnState()
      ..onSpeechStarted()
      ..onSpeechStopped()
      ..onResponseHandled();
    expect(t.awaitingReply, isFalse);
    expect(_stop(t), MicStopOutcome.readyNext);
  });

  test('reset starts a clean turn', () {
    final t = VoiceTurnState()
      ..onSpeechStarted()
      ..onSpeechStopped();
    t.reset();
    expect(t.serverHeardSpeech, isFalse);
    expect(t.awaitingReply, isFalse);
    expect(_stop(t), MicStopOutcome.noSpeech);
  });
}
