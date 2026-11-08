#!/bin/zsh
# Builds app/build/Sightline.app: the Swift shell plus a copy of the Python package, the page and the helpers.
#   app/build.sh            build (ad-hoc signed: fine on your own Mac)
#   app/build.sh --zip      also make dist/Sightline.zip for a GitHub release
# Signing for other people's Macs needs an Apple Developer ID: set SIGN_ID="Developer ID Application: …"
# and the build signs with it (hardened runtime); notarize the zip with `xcrun notarytool` afterwards.
set -e
cd "$(dirname "$0")/.."
ROOT=$PWD
APP="app/build/Sightline.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

helpers/build.sh
swift app/make_icon.swift "$APP/Contents/Resources" >/dev/null
cp app/Info.plist "$APP/Contents/Info.plist"
# Universal (Apple Silicon + Intel), macOS 13 and later.
for arch in arm64 x86_64; do
  swiftc -O -target $arch-apple-macos13.0 app/main.swift -o "app/build/Sightline-$arch" -framework Cocoa -framework WebKit \
    -framework ServiceManagement -framework UserNotifications
done
lipo -create app/build/Sightline-arm64 app/build/Sightline-x86_64 -output "$APP/Contents/MacOS/Sightline"
rm app/build/Sightline-arm64 app/build/Sightline-x86_64

# The panel's own files. No __pycache__, no tests.
rsync -a --exclude '__pycache__' --exclude '*.pyc' sightline "$APP/Contents/Resources/"
rsync -a web "$APP/Contents/Resources/"
mkdir -p "$APP/Contents/Resources/helpers"
rsync -a helpers/build "$APP/Contents/Resources/helpers/"
rsync -a scripts "$APP/Contents/Resources/"

if [[ -n "$SIGN_ID" ]]; then
  codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP"/Contents/Resources/helpers/build/*
  codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP"
else
  codesign --force --sign - "$APP"/Contents/Resources/helpers/build/*
  codesign --force --sign - "$APP"
fi
echo "built $ROOT/$APP"

if [[ "$1" == "--zip" ]]; then
  mkdir -p dist
  rm -f dist/Sightline.zip
  ditto -c -k --keepParent "$APP" dist/Sightline.zip
  echo "zipped $ROOT/dist/Sightline.zip"
fi
