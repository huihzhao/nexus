# Rune Protocol Desktop - UI Layer

A production-ready Avalonia UI for a cross-platform agentic chat application (Windows, macOS, Linux).

## Features

- **Clean MVVM Architecture**: CommunityToolkit.MVVM with source-generated properties
- **Cross-Platform**: Runs on Windows, macOS, and Linux
- **Modern Design**: Fluent theme with Rune Protocol branding (gold #F0B90B)
- **Real-Time Chat**: Async/await for responsive UI, no blocking operations
- **Secure Authentication**: JWT-based login with secure token storage
- **Local Persistence**: SQLite event log via RuneEngine integration
- **Extensible**: Easy to add new features, views, and view models

## Quick Start

### Prerequisites
- .NET 8.0 SDK or later
- Code editor (VS Code, Visual Studio, or Rider)

### Build & Run
```bash
cd RuneDesktop.UI
dotnet restore
dotnet build
dotnet run
```

### First Steps
1. Read **QUICK_START.md** for development setup
2. Check **ARCHITECTURE.md** for system design
3. Review **INTEGRATION.md** to connect with Core layer

## File Organization

```
RuneDesktop.UI/
├── ViewModels/              ← Business logic (MVVM)
│   ├── MainViewModel.cs
│   ├── LoginViewModel.cs
│   ├── ChatViewModel.cs
│   └── ChatMessageViewModel.cs
├── Views/                   ← UI layouts (XAML)
│   ├── MainWindow.axaml
│   ├── LoginView.axaml
│   └── ChatView.axaml
├── Converters/              ← Data binding helpers
├── Helpers/                 ← Utilities (Settings, Notifications)
├── App.axaml                ← Theme, colors, converters
├── Program.cs               ← Entry point
└── RuneDesktop.UI.csproj    ← Project config
```

## Key Components

### Views
- **MainWindow**: Root window with title bar and view switching
- **LoginView**: Registration/login form with validation
- **ChatView**: Two-panel chat interface (agent sidebar + message area)

### ViewModels
- **MainViewModel**: Navigation and authentication lifecycle
- **LoginViewModel**: Form validation and auth commands
- **ChatViewModel**: Message management and RuneEngine binding
- **ChatMessageViewModel**: Display formatting for messages

### Design System
- **Colors**: Gold accent (#F0B90B), dark sidebar (#0F172A), light gray backgrounds
- **Typography**: Inter font, modern hierarchy
- **Spacing**: 8px grid, 12px borders, 16px padding

## Dependencies

- `Avalonia 11.2.*` - UI framework
- `CommunityToolkit.Mvvm 8.2.*` - MVVM patterns
- `Avalonia.Themes.Fluent` - Design theme
- `Avalonia.Fonts.Inter` - Typography
- `RuneDesktop.Core` - Business logic and models

## Documentation

| Document | Purpose |
|----------|---------|
| **QUICK_START.md** | Developer onboarding, common tasks, debugging |
| **ARCHITECTURE.md** | System design, patterns, component hierarchy |
| **BUILD.md** | Build commands, publishing, troubleshooting |
| **INTEGRATION.md** | Core layer API contracts, testing, deployment |
| **MANIFEST.md** | Complete file listing and structure |

## Development Workflow

### Adding a New View
1. Create `Views/MyView.axaml` (XAML layout)
2. Create `Views/MyView.axaml.cs` (code-behind)
3. Create `ViewModels/MyViewModel.cs` (business logic)
4. Update navigation in `MainViewModel`

### Updating the Chat UI
- Modify `Views/ChatView.axaml` for layout
- Update `ViewModels/ChatViewModel.cs` for behavior
- Add converters in `Converters/` as needed

### Testing
- Unit test ViewModels with mock ApiClient and RuneEngine
- See INTEGRATION.md for mock class examples
- Use Avalonia's test framework for UI tests

## Design Highlights

### Reactive & Responsive
- ObservableProperty changes trigger UI updates
- AsyncRelayCommand for non-blocking operations
- AvaloniaList for efficient collection binding

### Clean Separation
- ViewModels contain all business logic
- Views are declarative XAML (minimal code-behind)
- Converters handle display transformations

### Extensible Architecture
- Value converters for new data types
- Helper classes for shared utilities
- Event-driven communication between components

## Authentication Flow

1. User enters server URL and display name
2. LoginViewModel validates inputs
3. ApiClient sends auth request (Register or Login)
4. Server responds with JWT and AgentProfile
5. LoginViewModel fires OnLoginSuccess event
6. MainViewModel switches to Chat view
7. ChatViewModel initializes and loads message history

## Chat Flow

1. User types and sends message
2. ChatViewModel sends via ApiClient
3. Message appends to local collection
4. Server processes and returns bot response
5. ChatViewModel appends bot message
6. RuneEngine logs exchange to LocalEventLog
7. UI auto-scrolls to show newest message

## Customization

### Change Colors
Edit `App.axaml` color resources:
```xml
<SolidColorBrush x:Key="RuneGoldBrush">#F0B90B</SolidColorBrush>
```

### Change Default Server URL
Edit `LoginViewModel.cs`:
```csharp
[ObservableProperty]
private string serverUrl = "https://your-server.com";
```

### Modify Layout
Edit XAML files in `Views/` folder with Avalonia syntax.

## Performance Tips

- Use AvaloniaList<T> for observable collections
- Async/await for network calls
- Virtualization for large lists
- Cache converters in App.Resources

## Troubleshooting

**Build errors?**
- Run `dotnet restore`
- Check .NET 8.0+ installed

**Binding not working?**
- Verify property name matches ViewModel (case-sensitive)
- Check DataContext is set
- Review XAML syntax

**Commands not firing?**
- Ensure Command name = method name + "Command"
- Check method is async Task (not void)

See BUILD.md for more troubleshooting.

## Platform Support

- **Windows**: Windows 10/11 (x64, ARM64)
- **macOS**: 10.15+ (Intel, Apple Silicon)
- **Linux**: Ubuntu 18.04+ and equivalents

## Future Enhancements

- [ ] Passkey authentication (WebView-based)
- [ ] File uploads and attachments
- [ ] Message search and filtering
- [ ] Dark/light theme toggle
- [ ] Offline mode with sync
- [ ] Message reactions
- [ ] Conversation threading
- [ ] Voice input/output

## Contributing

1. Follow C# naming conventions (PascalCase, camelCase)
2. Keep ViewModels focused on data/commands
3. Keep Views minimal (layout only)
4. Add documentation for new patterns
5. Test changes locally before committing

## License

Part of Rune Protocol ecosystem. See parent repository for details.

## Support & Resources

- **Avalonia Docs**: https://docs.avaloniaui.net/
- **MVVM Toolkit**: https://learn.microsoft.com/en-us/windows/communitytoolkit/mvvm/
- **Issue Tracker**: File bugs with minimal reproduction
- **Community**: Join Avalonia Discord for discussion

---

**Status**: Production-ready

**Last Updated**: April 2026

**All 26 files included and documented.**
