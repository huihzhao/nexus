# UI-Core Integration Guide

## Overview

The RuneDesktop.UI layer integrates with RuneDesktop.Core through two main entry points:
- **ApiClient**: HTTP communication with the cloud server
- **RuneEngine**: Local state management and event log persistence

## Core Dependencies

Ensure RuneDesktop.Core exports these types:

```csharp
// Models
public class ChatMessage
{
    public ChatMessageRole Role { get; set; }
    public string Content { get; set; }
    public DateTime Timestamp { get; set; }
}

public enum ChatMessageRole { User, Assistant, System }

public class AgentProfile
{
    public string AgentId { get; set; }
    public string Name { get; set; }
    public string? Erc8004TokenId { get; set; }
}

// Services
public class ApiClient
{
    public void SetServerUrl(string url);
    public void SetJwtToken(string token);
    public Task<ApiResponse<LoginResponse>> RegisterAsync(string displayName);
    public Task<ApiResponse<LoginResponse>> LoginAsync(string displayName);
    public Task<ApiResponse<ChatMessageResponse>> SendChatMessageAsync(string content);
}

public class RuneEngine
{
    public void Initialize(ApiClient client);
    public Task LogEventAsync(ChatEvent @event);
    public List<ChatMessage> GetMessageHistory();
    public ContextSnapshot GetCurrentContext();
    
    public event Action<EventLogEntry> OnEventLogged;
    public event Action<ChatMessage> OnChatMessageReceived;
    public event Action<ContextSnapshot> OnContextUpdated;
}

// Events & Data
public class EventLogEntry { }
public class ChatEvent { }
public class ContextSnapshot
{
    public int MemoryCount { get; set; }
    public int SkillCount { get; set; }
    public int TurnCount { get; set; }
}
```

## API Response Types

The UI expects these response structures:

```csharp
public class ApiResponse<T>
{
    public bool IsSuccess { get; set; }
    public T? Data { get; set; }
    public string? Message { get; set; }
}

public class LoginResponse
{
    public string JwtToken { get; set; }
    public AgentProfile AgentProfile { get; set; }
}

public class ChatMessageResponse
{
    public string Content { get; set; }
    public DateTime Timestamp { get; set; }
}
```

## ViewModel Integration Points

### MainViewModel

```csharp
public partial class MainViewModel : ObservableObject
{
    private readonly RuneEngine _engine;
    private readonly ApiClient _apiClient;

    public ChatViewModel ChatViewModel { get; }
    public LoginViewModel LoginViewModel { get; }

    public MainViewModel()
    {
        _engine = new RuneEngine();
        _apiClient = new ApiClient();
        // ...
    }
}
```

**Key Methods**:
- `HandleLoginSuccess(LoginViewModel.LoginSuccessArgs args)`: Called after login
  - Sets IsLoggedIn = true
  - Switches to Chat view
  - Calls ChatViewModel.Initialize()

### LoginViewModel Integration

```csharp
// Register endpoint
var response = await _apiClient.RegisterAsync(DisplayName);
if (response.IsSuccess)
{
    OnLoginSuccess?.Invoke(this, new LoginSuccessArgs
    {
        ServerUrl = ServerUrl,
        JwtToken = response.Data.JwtToken,
        AgentProfile = response.Data.AgentProfile
    });
}

// Login endpoint
var response = await _apiClient.LoginAsync(DisplayName);
// Same success path as Register
```

**Requirements**:
- ApiClient.SetServerUrl() before calling Register/Login
- Response must contain JwtToken and AgentProfile
- Error messages returned in response.Message

### ChatViewModel Integration

```csharp
public void Initialize(string serverUrl, string jwtToken)
{
    _apiClient.SetServerUrl(serverUrl);
    _apiClient.SetJwtToken(jwtToken);
    _engine.Initialize(_apiClient);

    // Subscribe to engine events
    _engine.OnEventLogged += HandleEngineEvent;
    _engine.OnChatMessageReceived += HandleChatMessage;
    _engine.OnContextUpdated += HandleContextUpdate;

    // Load history
    LoadMessageHistory();
}
```

**Event Handlers**:
```csharp
private void HandleEngineEvent(EventLogEntry entry) => UpdateStats();
private void HandleChatMessage(ChatMessage message)
{
    Messages.Add(new ChatMessageViewModel(message));
    UpdateStats();
}
private void HandleContextUpdate(ContextSnapshot snapshot)
{
    MemoryCount = snapshot.MemoryCount;
    SkillCount = snapshot.SkillCount;
    TurnCount = snapshot.TurnCount;
}
```

### SendMessage Flow

```csharp
[RelayCommand]
public async Task SendMessage()
{
    var messageContent = InputText.Trim();
    InputText = "";

    // 1. Add user message to UI
    Messages.Add(new ChatMessageViewModel(new ChatMessage
    {
        Role = ChatMessageRole.User,
        Content = messageContent,
        Timestamp = DateTime.UtcNow
    }));

    // 2. Send to server
    var response = await _apiClient.SendChatMessageAsync(messageContent);

    // 3. Add bot response
    if (response.IsSuccess)
    {
        Messages.Add(new ChatMessageViewModel(new ChatMessage
        {
            Role = ChatMessageRole.Assistant,
            Content = response.Data.Content,
            Timestamp = DateTime.UtcNow
        }));

        // 4. Log locally
        await _engine.LogEventAsync(new ChatEvent
        {
            EventType = "ChatMessageExchange",
            UserMessage = messageContent,
            BotMessage = response.Data.Content,
            Timestamp = DateTime.UtcNow
        });
    }
}
```

## Expected Core Implementations

### ApiClient

**SetServerUrl(string url)**
- Stores base URL for subsequent requests
- Example: https://api.runeprotocol.io

**SetJwtToken(string token)**
- Adds Authorization header: `Bearer {token}`
- Persists token in SecureTokenStore (for future sessions)

**RegisterAsync(string displayName) -> ApiResponse<LoginResponse>**
- POST `/auth/register` with displayName
- Returns new JWT and AgentProfile
- Creates user account on first call

**LoginAsync(string displayName) -> ApiResponse<LoginResponse>**
- POST `/auth/login` with displayName
- Returns JWT and existing AgentProfile
- For MVP (later integrate Passkey via WebView)

**SendChatMessageAsync(string content) -> ApiResponse<ChatMessageResponse>**
- POST `/chat/message` with message content
- Includes JWT in Authorization header
- Returns bot's response and timestamp
- May raise events for long-running LLM processing

### RuneEngine

**Initialize(ApiClient client)**
- Stores reference to ApiClient
- Loads LocalEventLog from SQLite
- Validates JWT token validity

**LogEventAsync(ChatEvent @event)**
- Persists event to LocalEventLog
- Fires OnEventLogged
- May batch updates for performance

**GetMessageHistory() -> List<ChatMessage>**
- Retrieves all chat messages from LocalEventLog
- Returns in chronological order
- Called during ChatViewModel.Initialize()

**GetCurrentContext() -> ContextSnapshot**
- Aggregates stats from LocalEventLog
- Memory count: number of persisted memories
- Skill count: number of learned skills
- Turn count: number of conversation turns

**Events**:
- **OnEventLogged**: Fired after event persisted
- **OnChatMessageReceived**: Fired when new message added (local or from server)
- **OnContextUpdated**: Fired when stats change

## Error Handling

### UI-Level Error Handling

```csharp
try
{
    var response = await _apiClient.SendChatMessageAsync(messageContent);
    if (response.IsSuccess)
    {
        // Add message to UI
    }
    else
    {
        // Add error message from response.Message
        Messages.Add(new ChatMessageViewModel(new ChatMessage
        {
            Role = ChatMessageRole.System,
            Content = $"Error: {response.Message}",
            Timestamp = DateTime.UtcNow
        }));
    }
}
catch (Exception ex)
{
    // Add exception message
    Messages.Add(new ChatMessageViewModel(new ChatMessage
    {
        Role = ChatMessageRole.System,
        Content = $"Error: {ex.Message}",
        Timestamp = DateTime.UtcNow
    }));
}
```

### Validation

LoginViewModel validates inputs before sending:
```csharp
private bool ValidateInput()
{
    if (string.IsNullOrWhiteSpace(ServerUrl))
    {
        ErrorMessage = "Server URL is required";
        return false;
    }
    if (string.IsNullOrWhiteSpace(DisplayName) || DisplayName.Length < 2)
    {
        ErrorMessage = "Display name must be at least 2 characters";
        return false;
    }
    return true;
}
```

## Testing Integration

### Mock ApiClient for UI Testing

```csharp
public class MockApiClient : ApiClient
{
    public override Task<ApiResponse<LoginResponse>> LoginAsync(string displayName)
    {
        return Task.FromResult(new ApiResponse<LoginResponse>
        {
            IsSuccess = true,
            Data = new LoginResponse
            {
                JwtToken = "mock-jwt-token",
                AgentProfile = new AgentProfile
                {
                    AgentId = "agent-123",
                    Name = displayName,
                    Erc8004TokenId = "token-456"
                }
            }
        });
    }

    public override Task<ApiResponse<ChatMessageResponse>> SendChatMessageAsync(string content)
    {
        return Task.FromResult(new ApiResponse<ChatMessageResponse>
        {
            IsSuccess = true,
            Data = new ChatMessageResponse
            {
                Content = $"You said: {content}",
                Timestamp = DateTime.UtcNow
            }
        });
    }
}
```

### Mock RuneEngine for UI Testing

```csharp
public class MockRuneEngine : RuneEngine
{
    public override List<ChatMessage> GetMessageHistory()
    {
        return new List<ChatMessage>
        {
            new ChatMessage
            {
                Role = ChatMessageRole.Assistant,
                Content = "Hello! I'm Rune.",
                Timestamp = DateTime.UtcNow.AddMinutes(-5)
            }
        };
    }

    public override ContextSnapshot GetCurrentContext()
    {
        return new ContextSnapshot
        {
            MemoryCount = 5,
            SkillCount = 3,
            TurnCount = 10
        };
    }
}
```

## Deployment Checklist

Before deploying, ensure:

- [ ] ApiClient endpoints match server implementation
- [ ] JWT token format is compatible (Bearer scheme)
- [ ] RuneEngine persists to valid SQLite path
- [ ] SecureTokenStore encrypts credentials
- [ ] All error responses include descriptive messages
- [ ] Long-running operations show loading UI
- [ ] Network timeouts are handled gracefully
- [ ] Offline mode gracefully degrades (if supported)

## Future Enhancements

1. **Passkey Authentication**: Replace simple username with WebView-based passkey
2. **File Uploads**: Integrate file attachment with ApiClient.UploadFileAsync()
3. **Streaming Responses**: Handle server-sent events for real-time LLM responses
4. **Message Reactions**: Add emoji reactions via ApiClient endpoints
5. **Threading**: Support conversation branches/threads
6. **Search**: Full-text search over LocalEventLog
7. **Sync**: Conflict resolution for multi-device sessions
8. **Offline Mode**: Queue messages locally, sync when online
