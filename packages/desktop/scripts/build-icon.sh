#!/usr/bin/env bash
#
# Convert the SVG logo to platform-specific raster icons that the OS
# uses for the dock / taskbar / Start menu. Run this once after pulling
# (or whenever you change RuneDesktop.UI/Assets/nexus-logo.svg).
#
# Outputs:
#   RuneDesktop.UI/Assets/nexus-icon.png    (256x256, generic Linux)
#   RuneDesktop.UI/Assets/nexus-icon.ico    (Windows .ico, multi-size)
#   RuneDesktop.UI/Assets/nexus-icon.icns   (macOS bundle icon)
#
# Dependencies:
#   * librsvg (rsvg-convert)  brew install librsvg     OR  apt install librsvg2-bin
#   * ImageMagick (magick / convert)   brew install imagemagick
#   * On macOS: iconutil (ships with Xcode CLT)
#
set -eu

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SVG="$DIR/RuneDesktop.UI/Assets/nexus-logo.svg"
OUT="$DIR/RuneDesktop.UI/Assets"

if [[ ! -f "$SVG" ]]; then
  echo "ERROR: source SVG not found at $SVG" >&2
  exit 1
fi

if ! command -v rsvg-convert >/dev/null; then
  echo "ERROR: rsvg-convert not on PATH (brew install librsvg / apt install librsvg2-bin)" >&2
  exit 1
fi
if ! command -v magick >/dev/null && ! command -v convert >/dev/null; then
  echo "ERROR: ImageMagick (magick / convert) not on PATH" >&2
  exit 1
fi
IM="magick"; command -v magick >/dev/null || IM="convert"

mkdir -p "$OUT"

echo "→ rasterise SVG to PNGs ..."
for sz in 16 32 64 128 256 512 1024; do
  rsvg-convert -w "$sz" -h "$sz" "$SVG" -o "$OUT/nexus-icon-$sz.png"
done

# Generic 256px icon some Linux desktops pick up.
cp "$OUT/nexus-icon-256.png" "$OUT/nexus-icon.png"

echo "→ Windows .ico (multi-size) ..."
"$IM" \
  "$OUT/nexus-icon-16.png" \
  "$OUT/nexus-icon-32.png" \
  "$OUT/nexus-icon-64.png" \
  "$OUT/nexus-icon-128.png" \
  "$OUT/nexus-icon-256.png" \
  "$OUT/nexus-icon.ico"

if [[ "$(uname)" == "Darwin" ]]; then
  echo "→ macOS .icns (iconset → iconutil) ..."
  ICONSET="$OUT/nexus.iconset"
  rm -rf "$ICONSET"; mkdir "$ICONSET"
  cp "$OUT/nexus-icon-16.png"   "$ICONSET/icon_16x16.png"
  cp "$OUT/nexus-icon-32.png"   "$ICONSET/icon_16x16@2x.png"
  cp "$OUT/nexus-icon-32.png"   "$ICONSET/icon_32x32.png"
  cp "$OUT/nexus-icon-64.png"   "$ICONSET/icon_32x32@2x.png"
  cp "$OUT/nexus-icon-128.png"  "$ICONSET/icon_128x128.png"
  cp "$OUT/nexus-icon-256.png"  "$ICONSET/icon_128x128@2x.png"
  cp "$OUT/nexus-icon-256.png"  "$ICONSET/icon_256x256.png"
  cp "$OUT/nexus-icon-512.png"  "$ICONSET/icon_256x256@2x.png"
  cp "$OUT/nexus-icon-512.png"  "$ICONSET/icon_512x512.png"
  cp "$OUT/nexus-icon-1024.png" "$ICONSET/icon_512x512@2x.png"
  iconutil -c icns "$ICONSET" -o "$OUT/nexus-icon.icns"
  rm -rf "$ICONSET"
fi

# Tidy up intermediate per-size PNGs except the 256 keeper.
for sz in 16 32 64 128 512 1024; do
  rm -f "$OUT/nexus-icon-$sz.png"
done

echo
echo "Done. Files written under $OUT:"
ls -1 "$OUT" | grep -E '^nexus-icon\.(png|ico|icns)$' || true
echo
echo "Next: rebuild the desktop project so the new icons get picked up."
