# Releasing the desktop app

How to cut a new release and get installers to users. Roughly 10 minutes,
mostly waiting for CI.

## TL;DR

```bash
git commit -am "..."          # land the changes
git tag v0.1.0                # whatever version
git push origin main --tags   # triggers the release workflow
```

GitHub Actions runs `release-desktop.yml`, builds on macOS / Windows /
Linux runners in parallel, drops a **draft** Release in the repo's
Releases page. You eyeball it, edit notes, click **Publish**.

Send your users:

```
https://github.com/<your-org>/rune-protocol/releases/latest
```

That URL always points at the most recent published release.

## Versioning

Tag format: `v<MAJOR>.<MINOR>.<PATCH>` (e.g. `v0.1.0`, `v0.1.1-rc1`).

The leading `v` is the trigger pattern in the workflow's `on.push.tags`
filter. The workflow strips it (`v0.1.0` → `0.1.0`) and patches the
csproj's `<Version>` element before building, so the produced files
end up named `Nexus-macos-universal-0.1.0.dmg` etc.

The csproj doesn't need a `<Version>` field permanently — CI injects
one per build. Keep it out so feature branches don't fight over what
"the version" is.

## What gets built

| Platform | File                                | Size     |
|----------|-------------------------------------|----------|
| macOS    | `Nexus-macos-universal-X.Y.Z.dmg`   | ~80 MB   |
| Windows  | `Setup-Nexus-vX.Y.Z.exe`            | ~70 MB   |
| Windows  | `Nexus-windows-x64-X.Y.Z.zip`       | ~80 MB   |
| Linux    | `Nexus-linux-x86_64-X.Y.Z.AppImage` | ~80 MB   |
| Linux    | `Nexus-linux-x86_64-X.Y.Z.tar.gz`   | ~75 MB   |

macOS is a universal binary — same .dmg works on Apple Silicon and Intel.
Windows ships both an Inno Setup installer (preferred) and a portable
zip (for users who can't run installers).

## What your users see (unsigned builds)

These builds are **not code-signed**. Every OS pops a one-time warning;
each artifact ships an `INSTALL.txt` explaining how to dismiss it, but
most users won't read it. Walk them through the first install verbally,
or pin a screenshot to your README.

### macOS

> **"Nexus.app cannot be opened because Apple cannot check it for
> malicious software"**
>
> Right-click `Nexus.app` → **Open** → confirm. Once.

If they get **"Nexus.app is damaged and can't be opened"** instead
(happens when the .dmg is downloaded over a Safari + HTTPS combo that
sets a stricter quarantine flag), they need:

```bash
xattr -d com.apple.quarantine /Applications/Nexus.app
```

### Windows

> **"Windows protected your PC — Microsoft Defender SmartScreen
> prevented an unrecognized app from starting."**
>
> Click **More info** → **Run anyway**.

After enough downloads, SmartScreen "warms up" against the executable's
hash and the prompt stops appearing — but only for that exact build. A
new version resets the counter.

### Linux

```bash
chmod +x Nexus-linux-x86_64-*.AppImage
./Nexus-linux-x86_64-*.AppImage
```

No prompts. If they hit `dlopen failed: libfuse.so.2`, they need
`sudo apt install libfuse2` (or libfuse2t64 on Ubuntu 24.04+). The
`.tar.gz` is a fallback that doesn't need FUSE.

### After it opens

Every platform lands on the Welcome wizard. Users paste their server
URL (the one you set up on the VPS), hit **Test connection**, then
**Continue**. The URL is persisted at `~/.../RuneProtocol/settings.json`
so they only do this once.

## Manual build (no CI)

If you want to test a build locally before tagging, run the script
that matches your OS:

```bash
# macOS
brew install librsvg                             # one-time
bash packages/desktop/scripts/build-macos.sh

# Linux
sudo apt install -y librsvg2-bin                 # one-time
# + appimagetool — see the script's header comment
bash packages/desktop/scripts/build-linux.sh

# Windows (PowerShell)
.\packages\desktop\scripts\build-windows.ps1 -Installer
```

Each drops outputs into `packages/desktop/dist/`. You can only build
for your current OS this way — that's why CI matters.

## Re-running CI without a new tag

`workflow_dispatch` is wired up. Go to **Actions → Release Desktop →
Run workflow** in the GitHub UI. Pass a version override (e.g.
`0.1.0-test`) so the artifact names don't collide.

## Roadmap: signed releases

Code signing eliminates the SmartScreen / Gatekeeper warnings entirely.
Cost + work, in order of impact:

1. **macOS Developer ID + notarization** — $99/yr Apple Developer
   membership. Adds two CI steps (`codesign` + `xcrun notarytool`).
   Eliminates the "unidentified developer" warning. ~1 day of work.

2. **Windows Authenticode** — $200-400/yr for a code-signing cert
   (DigiCert, Sectigo, etc.). Can use EV cert ($400-600/yr) to skip
   SmartScreen "warm-up" period. Adds `signtool` step in CI.

3. **Auto-update** — Velopack is the cleanest .NET / Avalonia option.
   Embeds an update channel URL into the build; the app self-updates
   in the background. Pairs naturally with signed releases.

Don't sign until you have actual users complaining. The unsigned-with-
INSTALL.txt path works fine for early-access distribution.
