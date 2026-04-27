# Rune Protocol Desktop UI - Architecture

## Overview

This is a cross-platform desktop application built with **Avalonia 11.2** for Windows, macOS, and Linux. The UI follows the MVVM pattern with the Community Toolkit MVVM library for clean, reactive code.

## Project Structure

```
RuneDesktop.UI/
├── App.axaml                  # Application root with theme and resources
├── App.axaml.cs              # Application initialization
├── Program.cs                # Entry point
├── RuneDesktop.UI.csproj     # Project file with dependencies
│
├── ViewModels/               # MVVM ViewModels (all inherit ObservableObject)
│   ├── MainViewModel.cs      # Root view model, handles navigation
│   ├── LoginViewModel.cs     # Login/registration logic
│   ├── ChatViewModel.cs      # Chat state and message handling
│   └── ChatMessageViewModel.cs # Single message display model
│
├── Views/                    # XAML UI views
│   ├── MainWindow.axaml     # Main window layout (title bar + content)
│   ├── MainWindow.axaml.cs  # Code-behind (minimal)
│   ├── LoginView.axaml      # Login/registration screen
│   ├── LoginView.axaml.cs   # Code-behind (minimal)
│   ├── ChatView.axaml       # Main chat interface
│   └── ChatView.axaml.cs    # Auto-scroll behavior
│
├── Converters/              # Value converters for XAML bindings
│   ├── EnumToBoolConverter.cs
│   ├── StringToBoolConverter.cs
│   ├── BoolToBrushConverter.cs
│   ├── BoolToForegroundConverter.cs
│   └── BoolToHAlignmentConverter.cs
│
├── Helpers/                 # Utility classes
│   ├── NotificationHelper.cs # User notifications
│   └── AppSettings.cs       # Settings persistence
│
└── Assets/                  # Images, icons (add rune-icon.png here)
```

## Dependencies

```xml
<PackageReference Include="Avalonia" Version="11.2.*" />
<PackageReference Include="Avalonia.Desktop" Version="11.2.*" />
<PackageReference Include="Avalonia.Themes.Fluent" Version="11.2.*" />
<PackageReference Include="Avalonia.Fonts.Inter" Version="11.2.*" />
<PackageReference Include="CommunityToolkit.Mvvm" Version="8.2.*" />
<PackageReference Include="Avalonia.Markup.Xaml.Loader" Version="11.2.*" />
<ProjectReference Include="../RuneDesktop.Core/RuneDesktop.Core.csproj" />
```

## Key Components

### ViewModels

All ViewModels inherit from `ObservableObject` and use source-generated properties:

**MainViewModel**
- Manages navigation between Login and Chat views
- Holds references to LoginViewModel and ChatViewModel
- Subscribes to LoginSuccess event to handle authentication

**LoginViewModel**
- Collects Server URL and Display Name
- Async RegisterCommand and LoginCommand
- Fires OnLoginSuccess event with JWT and AgentProfile
- Validation with error messages

**ChatViewModel**
- Manages message collection (AvaloniaList<ChatMessageViewModel>)
- Binds to RuneEngine for real-time updates
- SendMessageCommand: sends to server via ApiClient, appends to local storage
- Updates stats (MemoryCount, SkillCount, TurnCount)
- Auto-scrolls messages in ChatView

**ChatMessageViewModel**
- Wraps ChatMessage with formatting (IsUser, FormattedTime)
- Computes relative time display ("5m ago", "2h ago", etc.)

### Views

**MainWindow**
- Title bar with app name and connection status
- ContentControl switches between LoginView and ChatView based on CurrentView

**LoginView**
- Centered card layout with Rune logo (gold circle + "R")
- Server URL TextBox (default: https://api.runeprotocol.io)
- Display Name TextBox
- Register and Login buttons (gold + dark theme)
- Error message area (red background)
- Loading spinner during auth

**ChatView**
- Two-column layout:
  - **Left Sidebar (280px)**: Dark theme (#0F172A)
    - Agent profile card with avatar, name, AgentId, ERC badge
    - Stats grid (Memories, Skills, Turns, Chain Ops)
    - Memory list (scrollable)
    - Logout button
  - **Right Main Area**: Chat interface
    - ScrollViewer with ItemsControl for messages
    - Each message: avatar circle + bubble
      - User messages: right-aligned, blue (#3B82F6), white text
      - Bot messages: left-aligned, light gray (#E5E7EB), dark text
    - Input bar: TextBox + file attach + Send button

### Converters

Value converters enable clean XAML bindings:

- **EnumToBoolConverter**: Checks if enum equals parameter (for CurrentView routing)
- **StringToBoolConverter**: Checks if string is not empty
- **BoolToBrushConverter**: Blue for user, gray for assistant
- **BoolToForegroundConverter**: White for user, dark for assistant
- **BoolToHAlignmentConverter**: Right for user, left for assistant

### Design System

**Colors** (defined in App.axaml):
- Gold Accent: #F0B90B (Rune branding)
- Dark Background: #0F172A (sidebar, headers)
- Light Gray: #F3F4F6 (messages, cards)
- Text: #1F2937 (main), #6B7280 (secondary), #9CA3AF (tertiary)
- Blue: #3B82F6 (user messages, accents)
- Red: #EF4444 (errors)

**Typography**:
- Font: Inter (via Avalonia.Fonts.Inter)
- Headings: Bold, 28px or 18px
- Body: 14px
- Secondary: 12px, gray

**Spacing & Dimensions**:
- Border radius: 8px (containers), 12px (bubbles), 6px (stats cards)
- Sidebar width: 280px
- Message max-width: 600px
- Padding: 16px (standard), 12px (cards), 32px (login card)

## How It Works

### Login Flow

1. User enters Server URL and Display Name
2. Clicks Register or Login
3. LoginViewModel validates input, calls ApiClient
4. ApiClient.RegisterAsync() or LoginAsync() sends request to server
5. Server responds with JWT and AgentProfile
6. OnLoginSuccess fires, MainViewModel receives args
7. MainViewModel sets CurrentView to Chat, initializes ChatViewModel
8. ChatViewModel subscribes to RuneEngine events

### Chat Flow

1. User types message and presses Send
2. SendMessageCommand appends user ChatMessage to Messages
3. ApiClient.SendChatMessageAsync() sends to server with JWT
4. Server processes and returns bot response
5. ChatViewModel appends bot ChatMessage
6. ChatViewModel logs exchange to LocalEventLog via RuneEngine
7. RuneEngine fires OnChatMessageReceived
8. ChatView auto-scrolls to show newest message

### Event Subscriptions

**RuneEngine Events** (in ChatViewModel):
- OnEventLogged: Update stats
- OnChatMessageReceived: Add message to UI
- OnContextUpdated: Update MemoryCount, SkillCount, TurnCount

**Login Success** (in MainViewModel):
- LoginViewModel.OnLoginSuccess: Switch to Chat, init ChatViewModel

## Production Readiness

**Features Implemented**:
- MVVM pattern with source-generated properties
- Cross-platform support (Windows, macOS, Linux)
- JWT authentication persistence
- Local message history via RuneEngine
- Real-time UI updates
- Validation and error handling
- Responsive layout
- Fluent theme integration

**Future Enhancements**:
- Passkey authentication via WebView
- File attachment upload
- Message search and filtering
- User preferences (theme, font size)
- Offline mode with sync
- Message reactions and threading
- Voice input/output
- Markdown rendering in messages

## Building & Running

```bash
# Restore packages
dotnet restore

# Build
dotnet build

# Run
dotnet run

# Publish for distribution
dotnet publish -c Release -o ./publish
```

## Testing

Add unit tests to a `RuneDesktop.UI.Tests` project:
- ViewModels (ObservableProperty changes, Commands)
- Converters (value transformations)
- Integration tests with mocked ApiClient and RuneEngine

## Notes

- All ViewModels are instantiated in MainViewModel constructor
- ChatViewModel.Initialize() is called after successful login
- ChatView uses auto-scroll behavior via code-behind (best practice for Avalonia)
- Converters are registered in App.axaml.Resources for global access
- AppSettings persists last server URL and display name for convenience
