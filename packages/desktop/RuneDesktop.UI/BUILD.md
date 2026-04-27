# Build & Deployment Guide

## Prerequisites

- .NET 8.0 SDK or later
- Avalonia 11.2.x
- Visual Studio Code, Visual Studio, or JetBrains Rider

## Building the Project

### Development Build

```bash
cd RuneDesktop.UI
dotnet build
```

### Release Build

```bash
dotnet build -c Release
```

## Running the Application

### From Source

```bash
dotnet run --project RuneDesktop.UI/RuneDesktop.UI.csproj
```

### From Built Binaries

After building, the executable is located in `bin/Debug` or `bin/Release`.

**Windows**:
```bash
./bin/Release/net8.0/RuneDesktop.UI.exe
```

**macOS**:
```bash
./bin/Release/net8.0/RuneDesktop.UI
```

**Linux**:
```bash
./bin/Release/net8.0/RuneDesktop.UI
```

## Publishing for Distribution

### Self-Contained Release (includes .NET runtime)

```bash
dotnet publish -c Release -r win-x64 -o ./publish/windows
dotnet publish -c Release -r osx-arm64 -o ./publish/macos-arm64
dotnet publish -c Release -r osx-x64 -o ./publish/macos-x64
dotnet publish -c Release -r linux-x64 -o ./publish/linux
```

**Runtime Identifiers**:
- `win-x64`: Windows 64-bit
- `win-arm64`: Windows ARM64
- `osx-x64`: macOS Intel
- `osx-arm64`: macOS Apple Silicon (M1+)
- `linux-x64`: Linux 64-bit

### Framework-Dependent Release (smaller, requires .NET runtime)

```bash
dotnet publish -c Release -o ./publish/framework-dependent
```

## Project Structure Notes

- **RuneDesktop.UI.csproj**: Defines package references, target framework, and output type (WinExe)
- **App.axaml**: Application root with theme, colors, and converters
- **Program.cs**: Entry point using AppBuilder with platform detection
- **ViewModels/**: MVVM logic (observe properties, async commands)
- **Views/**: XAML layouts (MainWindow, LoginView, ChatView)
- **Converters/**: Value converters for data binding
- **Helpers/**: Utilities (settings, notifications)

## Dependency Management

Update packages:
```bash
dotnet add package Avalonia --version 11.2.0
dotnet add package CommunityToolkit.Mvvm --version 8.2.0
```

List all package versions:
```bash
dotnet list package --outdated
```

## Configuration

### Server URL Configuration

Default server URL in LoginViewModel:
```csharp
private string _serverUrl = "https://api.runeprotocol.io";
```

Users can override this in the login screen.

### Theme Configuration

Default theme in App.axaml:
```xml
RequestedThemeVariant="Light"
```

Change to "Dark" if needed.

## Debugging

### Visual Studio Code

Add to `.vscode/launch.json`:
```json
{
  "version": "0.2.0",
  "configurations": [
    {
      "name": ".NET 8.0",
      "type": "coreclr",
      "request": "launch",
      "program": "${workspaceFolder}/RuneDesktop.UI/bin/Debug/net8.0/RuneDesktop.UI.dll",
      "args": [],
      "cwd": "${workspaceFolder}",
      "stopAtEntry": false,
      "serverReadyAction": {
        "pattern": "\\bNow listening on:\\s+(https?://\\S+)"
      }
    }
  ]
}
```

### Console Output

Debug information logs to the console via:
```csharp
System.Diagnostics.Debug.WriteLine("Message");
```

## Troubleshooting

### Build Errors

**Missing Avalonia package**: Run `dotnet restore`

**Incompatible .NET version**: Ensure .NET 8.0+ is installed
```bash
dotnet --version
```

### Runtime Errors

**Platform not supported**: Check that the RID (runtime identifier) matches your OS

**Missing native dependencies** (Linux): Install required libraries
```bash
sudo apt-get install libgl1-mesa-glx libxkbcommon-x11-0
```

### View Not Rendering

- Check XAML syntax in View files
- Verify DataContext is set in code-behind
- Review binding paths match ViewModel property names
- Use `Converter=` with curly braces: `{Binding Property, Converter={StaticResource ConverterKey}}`

## Performance Tips

1. Use AvaloniaList<T> for ObservableCollections (better performance)
2. Virtualize ItemsControl when displaying large lists
3. Cache converters in App.xaml.Resources
4. Use async/await for long-running operations
5. Unsubscribe from events in ViewModels when cleanup needed

## Code Style

- Use nullable reference types (`#nullable enable`)
- Follow Async naming conventions (AsyncRelayCommand)
- Use partial classes for ViewModels (MVVM Toolkit generates members)
- Keep Views minimal (logic in ViewModels)
- Register converters globally in App.axaml

## Platform-Specific Issues

### Windows
- Standard install location: `C:\Program Files\`
- Registry for uninstall info: `HKEY_LOCAL_MACHINE\Software\Microsoft\Windows\CurrentVersion\Uninstall`

### macOS
- App bundle: `MyApp.app/Contents/MacOS/MyApp`
- Code signing: Use `codesign` tool for distribution
- Notarization: Required for Gatekeeper approval

### Linux
- Desktop file: Create `.desktop` entry for application menu
- Dependencies: May need to install GTK libraries

## CI/CD Integration

Example GitHub Actions workflow:
```yaml
name: Build and Test
on: [push, pull_request]
jobs:
  build:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-dotnet@v3
        with:
          dotnet-version: '8.0.x'
      - run: dotnet build -c Release
      - run: dotnet test
      - run: dotnet publish -c Release -r ${{ matrix.rid }}
```

## Version Management

Update version in RuneDesktop.UI.csproj:
```xml
<Version>1.0.0</Version>
<InformationalVersion>1.0.0+build.123</InformationalVersion>
```

## Documentation

- **ARCHITECTURE.md**: Component structure and design patterns
- **BUILD.md**: This file, build and deployment instructions
- Code comments for complex logic in ViewModels

## Support

For issues with Avalonia, visit: https://github.com/AvaloniaUI/Avalonia
For MVVM Toolkit: https://github.com/CommunityToolkit/dotnet
