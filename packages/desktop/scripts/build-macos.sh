#!/usr/bin/env bash
# build-macos.sh — produce an unsigned Nexus.app + Nexus.dmg on macOS.
#
# What this does
# ==============
#  1. `dotnet publish` the UI project for both osx-arm64 and osx-x64,
#     self-contained so users don't need to install .NET.
#  2. lipo the two binaries into a universal binary so one .app runs
#     on Apple Silicon and Intel Macs.
#  3. Wrap the output in a real .app bundle (Info.plist + Contents/MacOS
#     + Contents/Resources + .icns).
#  4. Wrap the .app in a .dmg with a README explaining "right click →
#     Open" since we're not signed/notarized.
#
# Usage
# =====
#   ./packages/desktop/scripts/build-macos.sh
#
# Output
# ======
#   packages/desktop/dist/Nexus-macos-universal.dmg
#
# Prereqs (one-time)
# ==================
#   * .NET 10 SDK on macOS
#   * `hdiutil` (preinstalled)
#   * `iconutil` (preinstalled, for .iconset → .icns)
#   * `librsvg` for converting the SVG logo to PNGs at multiple sizes
#       brew install librsvg
#   * Optional: `create-dmg` for prettier .dmg layout (else we fall
#     back to plain hdiutil).

set -euo pipefail

cd "$(dirname "$0")/.."   # packages/desktop/

PROJECT="RuneDesktop.UI/RuneDesktop.UI.csproj"
CONFIG="Release"
DIST="dist"
APP_NAME="Nexus"
BUNDLE_ID="ai.nexus.desktop"
VERSION=$(grep -oE '<Version>[^<]+' "$PROJECT" | head -1 | sed 's/<Version>//' || echo "0.1.0")

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  Building Nexus.app (macOS universal, unsigned)"
echo "  version: $VERSION"
echo "════════════════════════════════════════════════════════════════"
echo ""

# Sanity check
command -v dotnet >/dev/null || { echo "✗ dotnet not on PATH — install .NET 10 SDK"; exit 1; }
command -v lipo   >/dev/null || { echo "✗ lipo not found — Xcode CLT required"; exit 1; }

# ── Step 1: publish for both arches ──────────────────────────────────
rm -rf "$DIST"
mkdir -p "$DIST"

for rid in osx-arm64 osx-x64; do
    echo "→ publish $rid"
    dotnet publish "$PROJECT" \
        -c "$CONFIG" \
        -r "$rid" \
        --self-contained true \
        -p:PublishSingleFile=false \
        -p:DebugType=none \
        -p:DebugSymbols=false \
        -o "$DIST/publish-$rid" \
        --nologo --verbosity minimal
done

# ── Step 2: lipo arm64 + x64 into a universal binary ─────────────────
echo "→ lipo into universal"
mkdir -p "$DIST/$APP_NAME.app/Contents/MacOS"
mkdir -p "$DIST/$APP_NAME.app/Contents/Resources"

# Native binary
lipo -create \
    "$DIST/publish-osx-arm64/RuneDesktop.UI" \
    "$DIST/publish-osx-x64/RuneDesktop.UI" \
    -output "$DIST/$APP_NAME.app/Contents/MacOS/$APP_NAME"
chmod +x "$DIST/$APP_NAME.app/Contents/MacOS/$APP_NAME"

# Copy all the managed/native libs from one of the publish dirs (they're
# the same on both arches except the renamed binary). We can't use
# bash extglob `!(RuneDesktop.UI)` here because `bash -n` syntax-checks
# before `shopt -s extglob` would take effect — so do the rsync trick.
rsync -a --exclude='RuneDesktop.UI' \
    "$DIST/publish-osx-arm64/" \
    "$DIST/$APP_NAME.app/Contents/MacOS/"

# Lipo the dylib's that ship per-arch (Avalonia native bits).
for dylib in $(find "$DIST/publish-osx-arm64" -name "*.dylib" -type f); do
    rel="${dylib#$DIST/publish-osx-arm64/}"
    arm="$DIST/publish-osx-arm64/$rel"
    x64="$DIST/publish-osx-x64/$rel"
    if [ -f "$arm" ] && [ -f "$x64" ] && ! lipo -info "$arm" 2>/dev/null | grep -q "Architectures in"; then
        :  # not a fat library, skip
    fi
    if [ -f "$x64" ]; then
        lipo -create "$arm" "$x64" -output "$DIST/$APP_NAME.app/Contents/MacOS/$rel" 2>/dev/null \
            || cp "$arm" "$DIST/$APP_NAME.app/Contents/MacOS/$rel"
    fi
done

# ── Step 3: Info.plist + icon ─────────────────────────────────────────

# Build the .icns from the SVG logo if librsvg is available.
ICON_SRC="RuneDesktop.UI/Assets/nexus-logo.svg"
ICNS_OUT="$DIST/$APP_NAME.app/Contents/Resources/$APP_NAME.icns"

if [ -f "$ICON_SRC" ] && command -v rsvg-convert >/dev/null && command -v iconutil >/dev/null; then
    echo "→ generating .icns from $ICON_SRC"
    iconset="$DIST/$APP_NAME.iconset"
    rm -rf "$iconset" && mkdir -p "$iconset"
    for sz in 16 32 64 128 256 512 1024; do
        rsvg-convert -w $sz -h $sz "$ICON_SRC" -o "$iconset/icon_${sz}x${sz}.png" 2>/dev/null || true
    done
    # macOS expects @2x variants too
    for sz in 16 32 128 256 512; do
        dbl=$((sz * 2))
        rsvg-convert -w $dbl -h $dbl "$ICON_SRC" -o "$iconset/icon_${sz}x${sz}@2x.png" 2>/dev/null || true
    done
    iconutil -c icns "$iconset" -o "$ICNS_OUT" 2>/dev/null \
        && echo "  ✓ wrote $ICNS_OUT" \
        || echo "  ⚠ iconutil failed — bundle will use the default icon"
    rm -rf "$iconset"
else
    echo "  ⚠ rsvg-convert or iconutil missing; default app icon"
fi

cat > "$DIST/$APP_NAME.app/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>     <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>      <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>         <string>$VERSION</string>
    <key>CFBundleShortVersionString</key> <string>$VERSION</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleExecutable</key>      <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>        <string>$APP_NAME</string>
    <key>LSMinimumSystemVersion</key>  <string>11.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>NSHumanReadableCopyright</key> <string>© Nexus contributors. Apache-2.0.</string>
</dict>
</plist>
EOF

echo "→ wrote Info.plist"

# ── Step 4: build the .dmg ────────────────────────────────────────────

DMG="$DIST/$APP_NAME-macos-universal-$VERSION.dmg"
STAGE="$DIST/dmg-stage"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"

# Layout the dmg: app + Applications symlink + INSTALL.txt explaining
# the unsigned-build right-click-Open dance.
cp -R "$DIST/$APP_NAME.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

cat > "$STAGE/INSTALL.txt" <<'EOF'
Nexus desktop — installation
============================

  1. Drag Nexus.app to Applications.
  2. Open it.

If macOS says "Nexus.app cannot be opened because it is from an
unidentified developer" or "is damaged", that's because this build
isn't signed. Two ways around it:

  a. Right-click Nexus.app → "Open" → confirm. Only needed once.

  b. From a terminal, after copying to Applications:
        xattr -d com.apple.quarantine /Applications/Nexus.app

A signed + notarized build is on the roadmap.

First-time setup
----------------

The app boots into a Welcome wizard that asks for your Nexus server
URL — paste the address printed by `scripts/deploy_setup.sh` on
your VPS, click "Test connection", then Continue.
EOF

echo "→ creating .dmg"
hdiutil create \
    -volname "$APP_NAME $VERSION" \
    -srcfolder "$STAGE" \
    -format UDZO \
    -fs HFS+ \
    -ov \
    "$DMG" >/dev/null

# Cleanup
rm -rf "$STAGE" "$DIST/publish-osx-arm64" "$DIST/publish-osx-x64"

echo ""
echo "✓ Built $DMG"
ls -lh "$DMG"
echo ""
echo "Test locally:"
echo "  open \"$DMG\""
