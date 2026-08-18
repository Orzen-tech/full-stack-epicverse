# EpicVerse Mobile Application (Flutter)

EpicVerse is an AI-powered voice companion mobile application built with Flutter.

---

## Security & Production Distribution Guidelines

### OWASP MASVS / MASTG Binary Protection Compliance

1. **FairPlay DRM Encryption (`cryptid = 1`):**
   - Official release builds distributed via Apple App Store / TestFlight are automatically FairPlay-encrypted (`cryptid = 1`) by Apple's backend upon upload.
   - Development or local Ad-Hoc builds (`cryptid = 0`) are for internal testing only.

2. **Dart Code Obfuscation & Symbol Stripping:**
   To protect proprietary application logic against reverse engineering, all production release builds must be compiled with Dart symbol obfuscation:
   ```bash
   # Automated build script
   ./scripts/build_production_ipa.sh

   # Manual command
   flutter build ipa --release --obfuscate --split-debug-info=build/symbols
   ```

---

## Development Setup

```bash
# Install dependencies
flutter pub get

# Run application locally
flutter run
```

