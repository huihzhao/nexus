# Rune Protocol Desktop

A cross-platform AI digital twin application built on the Rune Protocol. Features offline-first synchronization, secure local state management, and seamless integration with the BNB Chain ecosystem.

## Project Overview

Rune Protocol Desktop is a desktop application that enables users to create and manage AI-powered digital twins with full event history, state management, and decentralized synchronization. The application runs natively on Windows, macOS, and Linux using Avalonia for a consistent cross-platform UI experience.

### Key Features

- **Offline-First Architecture**: Full functionality without server connectivity; automatic sync when online
- **Event-Sourced State**: Complete audit trail of all digital twin state changes
- **Secure Token Management**: Encrypted local storage of authentication credentials
- **Cross-Platform**: Native support for Windows, macOS, and Linux
- **Real-Time Synchronization**: Background sync with configurable intervals
- **BNB Chain Integration**: Direct blockchain interaction for decentralized operations

## Architecture

```
Rune Protocol Desktop
├── RuneDesktop.UI          (Avalonia client application)
├── RuneDesktop.Core        (Domain models, engines, and infrastructure)
├── RuneDesktop.Sync        (Offline-first sync engine)
└── [Server]                (FastAPI backend at rune-nexus/server)
```

### Components

#### RuneDesktop.Core
- **Models**: EventEntry, RuneState, digital twin definitions
- **ApiClient**: HTTP client for server communication with retry logic
- **RuneEngine**: Business logic for managing digital twins
- **LocalEventLog**: In-memory and persistent event storage
- **SecureTokenStore**: Encrypted credential storage using system keyrings

#### RuneDesktop.UI
- **Avalonia Framework**: Cross-platform reactive UI
- **MVVM Architecture**: Clean separation of concerns
- **ViewModels**: State management and business logic presentation
- **Views**: Native platform-specific UI components

#### RuneDesktop.Sync
- **SyncEngine**: Offline-first delta synchronization with automatic retry
- **SyncState**: Persistent sync point tracking per device
- **Push/Pull Protocol**: Bidirectional event synchronization
- **Auto-Sync**: Background timer for periodic synchronization

## Technology Stack

- **Framework**: .NET 8.0
- **UI**: Avalonia (cross-platform)
- **API Client**: System.Net.Http
- **Serialization**: System.Text.Json
- **Storage**: SQLite (via Core)
- **Security**: System keyrings / credential storage
- **Server**: FastAPI (Python) at rune-nexus/server

## Building & Running

### Prerequisites

- .NET 8.0 SDK or later
- Git
- Python 3.10+ (for server only)

### Build the Desktop App

```bash
cd rune-desktop
dotnet restore
dotnet build
```

### Run the Application

```bash
dotnet run --project RuneDesktop.UI
```

### Run the Backend Server

```bash
cd ../rune-nexus
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
python server/api_server.py
```

The server will start on `http://localhost:5000` by default.

## Project Structure

```
rune-desktop/
├── RuneDesktop.Core/
│   ├── Models/
│   │   ├── EventEntry.cs
│   │   ├── RuneState.cs
│   │   └── ...
│   ├── ApiClient.cs
│   ├── RuneEngine.cs
│   ├── LocalEventLog.cs
│   └── SecureTokenStore.cs
├── RuneDesktop.UI/
│   ├── Views/
│   │   ├── MainWindow.xaml
│   │   ├── DigitalTwinView.xaml
│   │   └── ...
│   ├── ViewModels/
│   │   ├── MainViewModel.cs
│   │   ├── DigitalTwinViewModel.cs
│   │   └── ...
│   └── App.xaml.cs
├── RuneDesktop.Sync/
│   ├── SyncEngine.cs
│   ├── SyncState.cs
│   └── RuneDesktop.Sync.csproj
├── global.json
├── .gitignore
└── README.md
```

## API Endpoints

The desktop app communicates with the server via:

- `POST /api/v1/sync/push` — Upload local unsynced events
- `GET /api/v1/sync/pull?after=<sync_id>` — Download remote events since last sync

## Configuration

Sync parameters can be configured in `SyncEngine`:

```csharp
// Start auto-sync with 30-second interval
await syncEngine.StartAutoSync(TimeSpan.FromSeconds(30));
```

Sync state is persisted to:
- **Windows**: `%APPDATA%\RuneProtocol\Sync\syncstate.json`
- **macOS/Linux**: `~/.config/RuneProtocol/Sync/syncstate.json`

## Development

### Code Style
- C# 11+ features enabled
- Nullable reference types enforced
- XML documentation on public APIs
- MVVM pattern for UI code

### Testing
Run unit tests:
```bash
dotnet test
```

### Debugging
Set breakpoints in Visual Studio or VS Code. The application includes comprehensive debug output via `Debug.WriteLine()`.

## Security Considerations

- All credentials are encrypted using system keyrings
- HTTPS is used for all server communication (production)
- Token refresh is automatic with server support
- Sensitive data in URLs is avoided
- Event data is validated before processing

## Troubleshooting

### App won't connect to server
1. Verify server is running: `http://localhost:5000/health`
2. Check network connectivity
3. Review logs in Debug Output for detailed error messages

### Events not syncing
1. Check IsOnline status in SyncEngine
2. Review sync state file: `~/.config/RuneProtocol/Sync/syncstate.json`
3. Inspect Network tab in browser dev tools (if applicable)

### Performance issues
1. Check LocalEventLog for excessive events
2. Consider archiving old events
3. Monitor background sync frequency

## Contributing

See main repository guidelines for contribution standards, code review process, and PR requirements.

## License

Proprietary - Rune Protocol Team 2026

## Support

For issues, documentation, and feature requests, contact the Rune Protocol team or submit issues to the project repository.
