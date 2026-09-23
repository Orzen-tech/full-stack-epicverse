#!/bin/sh
# Xcode Cloud post-clone step for the Flutter iOS app.
#
# Xcode Cloud only checks out the repo, so the Flutter-generated files
# (Flutter/Generated.xcconfig) and CocoaPods support files are missing.
# This script installs Flutter, generates them, and runs `pod install`.
#
# Runner/GoogleService-Info.plist is untracked in git (see SECURITY.md), so it
# is rebuilt from Xcode Cloud environment variables (mark as Secret). Values
# come from the same-named keys in the Firebase-downloaded plist:
#   FIREBASE_API_KEY            - API_KEY
#   FIREBASE_GOOGLE_APP_ID      - GOOGLE_APP_ID
#   FIREBASE_CLIENT_ID          - CLIENT_ID
#   FIREBASE_ANDROID_CLIENT_ID  - ANDROID_CLIENT_ID
#   FIREBASE_PROJECT_ID         - PROJECT_ID
# (Xcode Cloud rejects long values, so the whole file can't go in one variable.)

set -e

FLUTTER_VERSION="3.41.6"

# ci_scripts lives in ios/, so the Flutter project root is two levels up.
IOS_DIR="$CI_PRIMARY_REPOSITORY_PATH/frontend/EpicVerseApp/ios"
APP_DIR="$CI_PRIMARY_REPOSITORY_PATH/frontend/EpicVerseApp"

echo "[1/4] Installing Flutter $FLUTTER_VERSION..."
git clone https://github.com/flutter/flutter.git --depth 1 -b "$FLUTTER_VERSION" "$HOME/flutter"
export PATH="$PATH:$HOME/flutter/bin"
flutter --version
flutter precache --ios

echo "[2/4] Restoring GoogleService-Info.plist..."
for var in FIREBASE_API_KEY FIREBASE_GOOGLE_APP_ID FIREBASE_CLIENT_ID FIREBASE_ANDROID_CLIENT_ID FIREBASE_PROJECT_ID; do
  if [ -z "$(printenv "$var")" ]; then
    echo "error: $var is not set in the Xcode Cloud workflow environment."
    exit 1
  fi
done
# GOOGLE_APP_ID is "1:<sender id>:ios:<hash>"; REVERSED_CLIENT_ID is CLIENT_ID
# with its dot-separated parts reversed.
GCM_SENDER_ID="$(echo "$FIREBASE_GOOGLE_APP_ID" | cut -d: -f2)"
REVERSED_CLIENT_ID="$(echo "$FIREBASE_CLIENT_ID" | awk -F. '{for (i = NF; i > 1; i--) printf "%s.", $i; print $1}')"

cat > "$IOS_DIR/Runner/GoogleService-Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>CLIENT_ID</key>
	<string>$FIREBASE_CLIENT_ID</string>
	<key>REVERSED_CLIENT_ID</key>
	<string>$REVERSED_CLIENT_ID</string>
	<key>ANDROID_CLIENT_ID</key>
	<string>$FIREBASE_ANDROID_CLIENT_ID</string>
	<key>API_KEY</key>
	<string>$FIREBASE_API_KEY</string>
	<key>GCM_SENDER_ID</key>
	<string>$GCM_SENDER_ID</string>
	<key>PLIST_VERSION</key>
	<string>1</string>
	<key>BUNDLE_ID</key>
	<string>com.kriyora.epicverse</string>
	<key>PROJECT_ID</key>
	<string>$FIREBASE_PROJECT_ID</string>
	<key>STORAGE_BUCKET</key>
	<string>$FIREBASE_PROJECT_ID.firebasestorage.app</string>
	<key>IS_ADS_ENABLED</key>
	<false></false>
	<key>IS_ANALYTICS_ENABLED</key>
	<false></false>
	<key>IS_APPINVITE_ENABLED</key>
	<true></true>
	<key>IS_GCM_ENABLED</key>
	<true></true>
	<key>IS_SIGNIN_ENABLED</key>
	<true></true>
	<key>GOOGLE_APP_ID</key>
	<string>$FIREBASE_GOOGLE_APP_ID</string>
</dict>
</plist>
EOF
plutil -lint "$IOS_DIR/Runner/GoogleService-Info.plist"

echo "[3/4] Generating Flutter iOS config..."
cd "$APP_DIR"
flutter pub get
# Writes Flutter/Generated.xcconfig with the same obfuscation settings as
# scripts/build_production_ipa.sh.
flutter build ios --config-only --release --obfuscate --split-debug-info=build/symbols

echo "[4/4] Installing CocoaPods dependencies..."
export HOMEBREW_NO_AUTO_UPDATE=1
cd "$IOS_DIR"
pod install

exit 0
