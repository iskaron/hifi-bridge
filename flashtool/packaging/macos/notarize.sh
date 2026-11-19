#!/bin/sh
# macOS: sign, notarise and staple the .app built by PyInstaller ON A MAC (docs/FLASH_TOOL.md §7). PREPARED ONLY - never executed in this
# repository (no macOS runtime; G3 F-6). The owner supplies the Developer ID identity and a notarytool keychain profile; neither is part
# of this repository.
#     pyinstaller --noconfirm flashtool/packaging/hifi-bridge-flash.spec      -> dist/Hi-Fi Bridge Flash.app
#     python3 flashtool/packaging/collect_licenses.py "dist/Hi-Fi Bridge Flash.app/Contents/MacOS/licenses"
#     IDENTITY="Developer ID Application: <owner>" PROFILE=<notarytool-profile> sh flashtool/packaging/macos/notarize.sh
set -eu
APP="dist/Hi-Fi Bridge Flash.app"
: "${IDENTITY:?set IDENTITY to the Developer ID Application identity of the owner}"
: "${PROFILE:?set PROFILE to a notarytool keychain profile}"
codesign --force --deep --options runtime --timestamp --sign "$IDENTITY" "$APP"
ditto -c -k --keepParent "$APP" dist/hifi-bridge-flash-macos.zip
xcrun notarytool submit dist/hifi-bridge-flash-macos.zip --keychain-profile "$PROFILE" --wait
xcrun stapler staple "$APP"
spctl --assess --type execute --verbose "$APP"
