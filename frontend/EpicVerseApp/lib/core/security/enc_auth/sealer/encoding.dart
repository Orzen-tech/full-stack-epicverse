import 'dart:convert';
import 'dart:typed_data';

import 'validation.dart';

/// base64url without padding (the SDK codec, padding removed), as envelope.py `b64e`.
String b64uEncode(List<int> bytes) => base64Url.encode(bytes).replaceAll('=', '');

/// The associated data of AES-GCM: "EVENC", the version byte, then kid, operation, nonce and decimal timestamp, each as a
/// 2-byte big-endian length followed by ASCII bytes (envelope.py `_aad`). Inputs must already be validated (ASCII).
Uint8List buildAad(String kid, String operation, String nonce, int ts) {
  final out = BytesBuilder();
  out.add(ascii.encode('EVENC'));
  out.addByte(envelopeVersion);
  for (final part in [kid, operation, nonce, ts.toString()]) {
    final raw = ascii.encode(part);
    out.addByte(raw.length >> 8);
    out.addByte(raw.length & 0xff);
    out.add(raw);
  }
  return out.toBytes();
}

String _jsonAscii(String s) {
  final out = StringBuffer('"');
  for (final unit in s.codeUnits) {
    switch (unit) {
      case 0x22:
        out.write(r'\"');
      case 0x5c:
        out.write(r'\\');
      case 0x08:
        out.write(r'\b');
      case 0x09:
        out.write(r'\t');
      case 0x0a:
        out.write(r'\n');
      case 0x0c:
        out.write(r'\f');
      case 0x0d:
        out.write(r'\r');
      default:
        if (unit < 0x20 || unit > 0x7e) {
          out.write('\\u${unit.toRadixString(16).padLeft(4, '0')}');
        } else {
          out.writeCharCode(unit);
        }
    }
  }
  out.write('"');
  return out.toString();
}

/// The plaintext exactly as Python's `json.dumps(fields, separators=(",", ":"), sort_keys=True)` (ensure_ascii) produces it, so
/// both sides apply the same 2048-byte limit and the bytes can be compared in tests.
Uint8List buildPlaintext(Map<String, String> fields) {
  final keys = fields.keys.toList()..sort();
  final out = StringBuffer('{');
  for (var i = 0; i < keys.length; i++) {
    if (i > 0) out.write(',');
    out.write('${_jsonAscii(keys[i])}:${_jsonAscii(fields[keys[i]]!)}');
  }
  out.write('}');
  return Uint8List.fromList(ascii.encode(out.toString()));
}
