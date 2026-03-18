#!/usr/bin/env bash
# Creates a macOS .app bundle that launches CodeFission in Chrome's --app mode.
# The app shows "CodeFission" in the Dock and Cmd+Tab instead of "Google Chrome".
#
# Usage: bash scripts/make_macos_app.sh [port]
#   Output: ./CodeFission.app
set -e

PORT="${1:-19440}"
APP_NAME="CodeFission"
APP_DIR="${APP_NAME}.app"
CONTENTS="${APP_DIR}/Contents"
MACOS="${CONTENTS}/MacOS"
RESOURCES="${CONTENTS}/Resources"

# Find a Chromium-based browser
CHROME=""
for candidate in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser" \
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
    "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
    if [ -f "$candidate" ]; then
        CHROME="$candidate"
        break
    fi
done

if [ -z "$CHROME" ]; then
    echo "Error: No Chromium-based browser found." >&2
    exit 1
fi

echo "Using browser: $CHROME"

rm -rf "$APP_DIR"
mkdir -p "$MACOS" "$RESOURCES"

# Convert icon to .icns if we have one and sips is available
ICON_SRC="$(cd "$(dirname "$0")/.." && pwd)/assets/icon-fission-burst.png"
if [ -f "$ICON_SRC" ] && command -v sips &>/dev/null && command -v iconutil &>/dev/null; then
    ICONSET=$(mktemp -d)/CodeFission.iconset
    mkdir -p "$ICONSET"
    for size in 16 32 64 128 256 512; do
        sips -z $size $size "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}.png" &>/dev/null
        double=$((size * 2))
        if [ $double -le 1024 ]; then
            sips -z $double $double "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" &>/dev/null
        fi
    done
    iconutil -c icns "$ICONSET" -o "$RESOURCES/AppIcon.icns" 2>/dev/null && \
        ICON_CLAUSE="<key>CFBundleIconFile</key><string>AppIcon</string>" || \
        ICON_CLAUSE=""
    rm -rf "$(dirname "$ICONSET")"
else
    ICON_CLAUSE=""
fi

# Info.plist
cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>com.codefission.app</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launch</string>
    ${ICON_CLAUSE}
</dict>
</plist>
PLIST

# Launcher script
cat > "$MACOS/launch" <<LAUNCHER
#!/usr/bin/env bash
exec "$CHROME" --app="http://localhost:${PORT}" --user-data-dir="\$HOME/.codefission/chrome-profile"
LAUNCHER
chmod +x "$MACOS/launch"

echo "Created ${APP_DIR}"
echo "  Double-click it or run: open ${APP_DIR}"
echo "  (Make sure the CodeFission server is running first)"
