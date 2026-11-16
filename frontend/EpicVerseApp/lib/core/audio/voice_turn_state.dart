/// What the voice screen should do at the moment the microphone stops.
enum MicStopOutcome {
  /// The backend never told us anything and speech was heard: start "working on it" and wait.
  startThinking,

  /// The backend already said the user stopped talking and a reply is on its way: keep waiting.
  keepThinking,

  /// The backend already answered (or chose silence) and nothing new was said since: just be ready.
  readyNext,

  /// Nothing speech-like was heard.
  noSpeech,

  /// The mic delivered only digital silence (muted, or taken by another app).
  micSilent,
}

/// Tracks what the BACKEND has told us during one voice turn, so the screen never shows a
/// "working on it" sequence for a turn the backend has already finished.
///
/// The backend listens for the end of speech by itself (server VAD) and answers while the mic is
/// still open, so "mic stopped" and "turn finished" are different moments.
class VoiceTurnState {
  bool _serverHeardSpeech = false;
  bool _awaitingReply = false;
  bool _respondedSinceSpeech = false;

  /// The backend's voice detection fired at least once this turn.
  bool get serverHeardSpeech => _serverHeardSpeech;

  /// The backend said the user stopped talking and has not answered yet.
  bool get awaitingReply => _awaitingReply;

  void reset() {
    _serverHeardSpeech = false;
    _awaitingReply = false;
    _respondedSinceSpeech = false;
  }

  void onSpeechStarted() {
    _serverHeardSpeech = true;
    _awaitingReply = false;
    _respondedSinceSpeech = false;
  }

  void onSpeechStopped() {
    _serverHeardSpeech = true;
    _awaitingReply = true;
    _respondedSinceSpeech = false;
  }

  /// A reply finished (response.done), the AI chose silence, or the AI started speaking.
  void onResponseHandled() {
    _awaitingReply = false;
    _respondedSinceSpeech = true;
  }

  /// [micLooksDead]: the mic delivered nothing but digital silence (muted / taken by another app).
  ///
  /// "Did the user speak?" is decided ONLY by the backend's voice detection (speech_started /
  /// speech_stopped events). A loudness check on the phone was tried and rejected: some phones
  /// boost room noise (automatic gain) to speech-like levels, which hid the "didn't hear anything"
  /// message on a silent turn.
  MicStopOutcome onMicStopped({required bool micLooksDead}) {
    if (!_serverHeardSpeech) return micLooksDead ? MicStopOutcome.micSilent : MicStopOutcome.noSpeech;
    if (_awaitingReply) return MicStopOutcome.keepThinking;
    if (_respondedSinceSpeech) return MicStopOutcome.readyNext;
    return MicStopOutcome.startThinking;
  }
}
