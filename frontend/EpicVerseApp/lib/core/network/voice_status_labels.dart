/// Turns the voice connection's internal status words into sentences a player can act on.
/// Returns null for a status that should not replace what the screen is already showing.
String? voiceStatusLabel(String raw) {
  switch (raw) {
    case 'Connecting...':
      return 'Connecting...';
    case 'Connected':
      return 'Ready';
    case 'Disconnected':
      return 'Tap the mic to start';
    case 'Reconnecting...':
      return 'Connection lost. Reconnecting...';
    case 'Reconnect failed':
      return "Couldn't reconnect. Tap the mic to try again.";
    case 'Verification required':
      return 'Please verify your identity to use voice.';
    case 'Email verification required':
      return 'Please verify your email to use voice.';
    case 'Logged out: Active elsewhere':
      return 'Signed out: your account is open on another device.';
    default:
      return null;
  }
}
