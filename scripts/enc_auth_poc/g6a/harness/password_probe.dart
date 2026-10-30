// TEST PROBE: runs the REAL, unmodified PasswordValidator from the Flutter app on a list of passwords and prints the validator's
// verdict for each (its message, or null). Reads one JSON array of strings from stdin. Dummy strings only; no network, no files
// written. The validator file is imported by relative path and is never changed.
import 'dart:convert';
import 'dart:io';

import '../../../../frontend/EpicVerseApp/lib/core/utils/password_validator.dart';

void main() {
  final input = jsonDecode(stdin.readLineSync(encoding: utf8)!) as List<dynamic>;
  final out = input.map((value) => PasswordValidator.validate(value as String)).toList();
  stdout.writeln(jsonEncode(out));
}
