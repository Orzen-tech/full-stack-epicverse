#!/bin/bash
# EpicVerse - Production iOS Build Script with Binary Obfuscation (OWASP MASVS / MASTG Compliance)
#
# This script compiles the release iOS IPA with:
# 1. Dart symbol obfuscation (--obfuscate)
# 2. Debug symbol splitting (--split-debug-info) to prevent binary reverse engineering.

set -e

echo "=================================================="
echo " Building EpicVerse iOS Production Archive (IPA) "
echo "=================================================="

# Ensure script is run from EpicVerseApp directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="$( dirname "$SCRIPT_DIR" )"
cd "$PROJECT_DIR"

# Clean previous build artifacts
echo "[1/3] Cleaning previous build artifacts..."
flutter clean
flutter pub get

# Build release IPA with obfuscation
echo "[2/3] Compiling release IPA with obfuscation..."
mkdir -p build/symbols
flutter build ipa --release --obfuscate --split-debug-info=build/symbols

echo "=================================================="
echo " Build Succeeded! "
echo " Symbols saved to: build/symbols "
echo " IPA output path:  build/ios/archive/Runner.xcarchive "
echo " Note: Apple App Store Connect applies FairPlay DRM "
echo "       encryption (cryptid=1) upon upload.          "
echo "=================================================="
