; Inno Setup script for the Nexus desktop installer (Windows).
;
; Driven by build-windows.ps1 -Installer. Two #define inputs from
; the command line:
;   /DAppVersion=0.1.0
;   /DSourceDir=...\dist\publish-win-x64
;   /DOutputDir=...\dist
;
; What the installer does
; =======================
;   * Copies the self-contained .NET publish dir into Program Files.
;   * Creates a Start Menu shortcut + optional Desktop shortcut.
;   * Registers an uninstaller (Add/Remove Programs entry).
;   * Does NOT register file associations / URL handlers — keep the
;     surface small for an unsigned dev build.
;
; Note: this is unsigned. Users will see SmartScreen "Windows protected
; your PC" on first run; "More info → Run anyway" gets through. Code
; signing is on the roadmap (separate SIGNING.md).

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\publish-win-x64"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{B97E1ECC-NEXU-4A1A-9D29-A78C9A5DE001}
AppName=Nexus
AppVersion={#AppVersion}
AppVerName=Nexus {#AppVersion}
AppPublisher=Nexus contributors
AppPublisherURL=https://github.com/your-org/rune-protocol
DefaultDirName={autopf}\Nexus
DefaultGroupName=Nexus
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Setup-Nexus-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
WizardStyle=modern
LicenseFile=
SetupIconFile=

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; \
  GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copy the entire publish output. recursesubdirs picks up Avalonia's
; native runtime libs (av_libglesv2.dll etc.) automatically.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Nexus"; Filename: "{app}\RuneDesktop.UI.exe"
Name: "{group}\{cm:UninstallProgram,Nexus}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Nexus"; Filename: "{app}\RuneDesktop.UI.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\RuneDesktop.UI.exe"; \
  Description: "{cm:LaunchProgram,Nexus}"; \
  Flags: nowait postinstall skipifsilent
