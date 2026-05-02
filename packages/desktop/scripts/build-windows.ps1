# build-windows.ps1 — produce an unsigned Nexus.exe + zip on Windows.
#
# What this does
# ==============
#  1. dotnet publish for win-x64, self-contained (no .NET install needed
#     on target).
#  2. Drop INSTALL.txt explaining SmartScreen "More info → Run anyway".
#  3. Either:
#       (a) zip the publish dir → dev distribution (default), or
#       (b) if Inno Setup is installed, run iscc.exe on the .iss script
#           below to produce a real Setup-Nexus-vX.Y.Z.exe installer.
#
# Usage
# =====
#   .\packages\desktop\scripts\build-windows.ps1            # zip only
#   .\packages\desktop\scripts\build-windows.ps1 -Installer # also build .exe
#
# Output
# ======
#   packages\desktop\dist\Nexus-windows-x64.zip
#   packages\desktop\dist\Setup-Nexus-vX.Y.Z.exe   (if -Installer)
#
# Prereqs (one-time)
# ==================
#   * .NET 10 SDK
#   * (optional) Inno Setup 6 — winget install JRSoftware.InnoSetup

param(
    [switch]$Installer
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")  # packages/desktop/

$Project = "RuneDesktop.UI/RuneDesktop.UI.csproj"
$Config  = "Release"
$Dist    = "dist"
$AppName = "Nexus"

# Read version from the csproj. Fallback to 0.1.0 if missing.
$Version = "0.1.0"
$projXml = [xml](Get-Content $Project)
$verNode = $projXml.SelectSingleNode("//Version")
if ($verNode) { $Version = $verNode.'#text' }

Write-Host ""
Write-Host "════════════════════════════════════════════════════════════════"
Write-Host "  Building Nexus.exe (Windows x64, unsigned)"
Write-Host "  version: $Version"
Write-Host "════════════════════════════════════════════════════════════════"
Write-Host ""

# Sanity checks
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    Write-Error "dotnet not on PATH — install .NET 10 SDK"
    exit 1
}

# ── Step 1: publish ──────────────────────────────────────────────────
if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
New-Item -ItemType Directory -Path $Dist | Out-Null

$publishDir = Join-Path $Dist "publish-win-x64"
Write-Host "→ publish win-x64"
dotnet publish $Project `
    -c $Config `
    -r "win-x64" `
    --self-contained true `
    -p:PublishSingleFile=false `
    -p:DebugType=none `
    -p:DebugSymbols=false `
    -o $publishDir `
    --nologo --verbosity minimal

# ── Step 2: drop INSTALL.txt next to the .exe ────────────────────────
@"
Nexus desktop — installation
============================

If Windows shows "Windows protected your PC":
  1. Click "More info"
  2. Click "Run anyway"

That's because this build isn't code-signed. A signed installer is on
the roadmap.

First-time setup
----------------

The app boots into a Welcome wizard that asks for your Nexus server
URL — paste the address printed by ``scripts/deploy_setup.sh`` on
your VPS, click "Test connection", then Continue.
"@ | Set-Content -Encoding UTF8 (Join-Path $publishDir "INSTALL.txt")

# ── Step 3: zip ──────────────────────────────────────────────────────
$ZipPath = Join-Path $Dist "$AppName-windows-x64-$Version.zip"
Write-Host "→ creating zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path "$publishDir\*" -DestinationPath $ZipPath -CompressionLevel Optimal
Write-Host "✓ wrote $ZipPath"

# ── Step 4 (optional): Inno Setup installer ──────────────────────────
if ($Installer) {
    $iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue)
    if (-not $iscc) {
        Write-Warning "Inno Setup not found on PATH (iscc.exe). Skipping installer."
        Write-Warning "Install with: winget install JRSoftware.InnoSetup"
    } else {
        $issPath = "scripts\nexus-installer.iss"
        if (Test-Path $issPath) {
            Write-Host "→ running Inno Setup"
            & $iscc $issPath /DAppVersion=$Version /DSourceDir=(Resolve-Path $publishDir).Path /DOutputDir=(Resolve-Path $Dist).Path
            Write-Host "✓ Installer at $Dist\Setup-$AppName-v$Version.exe"
        } else {
            Write-Warning "$issPath missing — installer skipped."
        }
    }
}

Write-Host ""
Write-Host "Output:"
Get-ChildItem $Dist -File | Select-Object Name, Length | Format-Table
