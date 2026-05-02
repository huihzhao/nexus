# Packaging the Nexus desktop

How to produce platform installers (.dmg / .exe / .AppImage) of the desktop client. Current target is **unsigned dev builds** — fine for internal testing, friction-laden for public distribution. See "Signing" at the bottom for what's needed to take this to production.

---

## What you get

| Platform | Output | Build command |
|---|---|---|
| macOS (arm64 + x64 universal) | `Nexus-macos-universal-{version}.dmg` | `./scripts/build-macos.sh` |
| Windows x64 | `Nexus-windows-x64-{version}.zip` (+ optional `Setup-Nexus-v{version}.exe`) | `pwsh ./scripts/build-windows.ps1 [-Installer]` |
| Linux x86_64 | `Nexus-linux-x86_64-{version}.AppImage` (+ `.tar.gz`) | `./scripts/build-linux.sh` |

All three produce **self-contained** binaries — users don't need to install .NET separately. That makes each output ~80-150 MB, which is the price of a portable runtime.

---

## First-run UX (same on every platform)

Every fresh install boots into a **Welcome wizard** that:

1. Asks for the Nexus server URL (placeholder hints at `https://1-2-3-4.nip.io` from `scripts/deploy_setup.sh`)
2. Lets the user click "Test connection" — the desktop hits `<url>/healthz` (and falls back to `/docs`) to verify the URL is reachable
3. Shows a green/red pill with a friendly status message
4. "Continue →" is enabled only after a successful test (or the user explicitly clicks "Use anyway")
5. Saves the URL to the per-user app-data directory:
   - macOS: `~/Library/Application Support/RuneProtocol/settings.json`
   - Linux: `~/.config/RuneProtocol/settings.json`
   - Windows: `%APPDATA%\RuneProtocol\settings.json`
6. Hands off to the normal Login screen

After setup, the **gear icon** at the top-right of the Login screen re-opens the wizard so users can switch between dev / staging / prod servers without reinstalling.

The legacy `appsettings.json`-next-to-the-binary path still works as a developer fallback for `dotnet run` workflows; SettingsStore wins if both are present.

---

## macOS

```bash
# One-time prereqs (Homebrew)
brew install librsvg          # for converting the SVG logo to .icns
# .NET 10 SDK from https://dotnet.microsoft.com/download

# Build
cd packages/desktop
./scripts/build-macos.sh
```

Output: `dist/Nexus-macos-universal-{version}.dmg`. Drag-to-Applications layout, with `INSTALL.txt` inside the .dmg explaining the right-click-Open dance for unsigned builds.

**User instructions** (paste into your release notes):

1. Open `Nexus-macos-universal.dmg`
2. Drag `Nexus.app` to **Applications**
3. **Right-click `Nexus.app` → "Open"** → click "Open" again in the warning dialog
   - First run only. macOS remembers the choice.
   - If you see "Nexus.app is damaged", run in terminal:
     ```bash
     xattr -d com.apple.quarantine /Applications/Nexus.app
     ```

---

## Windows

```powershell
# One-time prereqs
# Install .NET 10 SDK from https://dotnet.microsoft.com/download
# Optional, for the .exe installer:
winget install JRSoftware.InnoSetup

# Build (zip only)
pwsh -File .\packages\desktop\scripts\build-windows.ps1

# Build with installer
pwsh -File .\packages\desktop\scripts\build-windows.ps1 -Installer
```

Output:
- `dist\Nexus-windows-x64-{version}.zip` — extract anywhere, double-click `RuneDesktop.UI.exe`
- `dist\Setup-Nexus-v{version}.exe` (if `-Installer`) — full installer with Add/Remove Programs entry, Start Menu shortcut, optional Desktop shortcut

**User instructions**:

1. Run `Setup-Nexus-v{version}.exe` (or extract the zip and run `RuneDesktop.UI.exe`)
2. If "Windows protected your PC" appears: click **More info** → **Run anyway**
3. Walk through the Welcome wizard

---

## Linux

```bash
# One-time prereqs (Debian/Ubuntu)
sudo apt install librsvg2-bin
# Install .NET 10 SDK: https://learn.microsoft.com/dotnet/core/install/linux
# AppImage tool:
curl -fsSL https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage \
  -o ~/.local/bin/appimagetool && chmod +x ~/.local/bin/appimagetool

# Build
cd packages/desktop
./scripts/build-linux.sh
```

Output:
- `dist/Nexus-linux-x86_64-{version}.AppImage` — single file, `chmod +x` and run
- `dist/Nexus-linux-x86_64-{version}.tar.gz` — fallback for distros without libfuse

**User instructions**:

```bash
chmod +x Nexus-linux-x86_64.AppImage
./Nexus-linux-x86_64.AppImage
```

No security warnings — Linux trusts execute bits.

---

## Versioning

The build scripts read `<Version>` from `RuneDesktop.UI/RuneDesktop.UI.csproj`. To cut a release:

1. Bump `<Version>0.1.0</Version>` in the csproj.
2. Run all three build scripts.
3. Tag the git commit (`git tag v0.1.0`).
4. Upload the three artifacts to GitHub Releases.

---

## Signing (NOT done in this build)

| Platform | What's needed | Cost | Where to add it |
|---|---|---|---|
| macOS | Apple Developer ID Application certificate + notarization | $99/yr | After `lipo`, run `codesign --deep --sign "Developer ID Application: Your Name" Nexus.app`, then `xcrun notarytool submit ... --wait` and `xcrun stapler staple Nexus.app` |
| Windows | Code-signing certificate (DigiCert / Sectigo / Certum) | $200-400/yr | After publish, run `signtool sign /tr ... /td sha256 /fd sha256 RuneDesktop.UI.exe` (and again on the Setup .exe) |
| Linux | Nothing required | $0 | — |

A signed-build CI workflow (GitHub Actions) is on the roadmap; until then unsigned dev builds work for internal testing and trusted users.

The unsigned macOS path (right-click → Open) works *exactly once* per Mac, so for alpha testing it's fine. For >100 testers or any public distribution, get the Developer ID — the friction otherwise is brutal.

---

## CI release flow (future)

```yaml
# .github/workflows/release.yml (sketch — not in repo yet)
on:
  push:
    tags: ['v*']
jobs:
  macos:
    runs-on: macos-14
    steps: [checkout, setup-dotnet, brew install librsvg,
            ./packages/desktop/scripts/build-macos.sh,
            actions/upload-release-asset]
  windows: { runs-on: windows-latest, steps: [..., pwsh -File ...build-windows.ps1 -Installer, ...] }
  linux:   { runs-on: ubuntu-latest,  steps: [..., apt install librsvg2-bin appimagetool, ./...build-linux.sh, ...] }
```

When you do this, set up the matching signing secrets per platform (`APPLE_TEAM_ID`, `WINDOWS_CERT_PFX`, etc.) and uncomment the codesign / signtool calls in each script.
