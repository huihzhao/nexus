# Testing Guide for RuneDesktop.UI

## Overview

This guide covers unit testing ViewModels and integration testing with mock Core components.

## Test Structure

Create a sibling project: `RuneDesktop.UI.Tests`

```bash
dotnet new xunit -n RuneDesktop.UI.Tests
cd RuneDesktop.UI.Tests
dotnet add reference ../RuneDesktop.UI/RuneDesktop.UI.csproj
dotnet add package Moq
```

## ViewModel Unit Tests

### LoginViewModel Tests

```csharp
using Xunit;
using Moq;
using RuneDesktop.UI.ViewModels;
using RuneDesktop.Core.Services;
using RuneDesktop.Core.Models;

namespace RuneDesktop.UI.Tests.ViewModels;

public class LoginViewModelTests
{
    private readonly Mock<ApiClient> _mockApiClient;
    private readonly LoginViewModel _viewModel;

    public LoginViewModelTests()
    {
        _mockApiClient = new Mock<ApiClient>();
        _viewModel = new LoginViewModel(_mockApiClient.Object);
    }

    [Fact]
    public async Task LoginCommand_WithValidInput_ShouldFireOnLoginSuccess()
    {
        // Arrange
        _viewModel.ServerUrl = "https://api.test.com";
        _viewModel.DisplayName = "TestUser";

        var expectedResponse = new ApiResponse<LoginResponse>
        {
            IsSuccess = true,
            Data = new LoginResponse
            {
                JwtToken = "test-jwt-token",
                AgentProfile = new AgentProfile
                {
                    AgentId = "agent-123",
                    Name = "TestUser",
                    Erc8004TokenId = "token-456"
                }
            }
        };

        _mockApiClient
            .Setup(x => x.LoginAsync(It.IsAny<string>()))
            .ReturnsAsync(expectedResponse);

        var onLoginSuccessCalled = false;
        var receivedArgs = default(LoginViewModel.LoginSuccessArgs);

        _viewModel.OnLoginSuccess += (s, args) =>
        {
            onLoginSuccessCalled = true;
            receivedArgs = args;
        };

        // Act
        await _viewModel.LoginCommand.ExecuteAsync(null);

        // Assert
        Assert.True(onLoginSuccessCalled);
        Assert.NotNull(receivedArgs);
        Assert.Equal("test-jwt-token", receivedArgs.JwtToken);
        Assert.Equal("TestUser", receivedArgs.AgentProfile.Name);
    }

    [Fact]
    public async Task LoginCommand_WithInvalidInput_ShouldSetErrorMessage()
    {
        // Arrange
        _viewModel.ServerUrl = "";
        _viewModel.DisplayName = "TestUser";

        // Act
        await _viewModel.LoginCommand.ExecuteAsync(null);

        // Assert
        Assert.Equal("Server URL is required", _viewModel.ErrorMessage);
    }

    [Fact]
    public async Task RegisterCommand_WithValidInput_ShouldFireOnLoginSuccess()
    {
        // Arrange
        _viewModel.ServerUrl = "https://api.test.com";
        _viewModel.DisplayName = "NewUser";

        var expectedResponse = new ApiResponse<LoginResponse>
        {
            IsSuccess = true,
            Data = new LoginResponse
            {
                JwtToken = "new-jwt-token",
                AgentProfile = new AgentProfile
                {
                    AgentId = "agent-789",
                    Name = "NewUser",
                    Erc8004TokenId = null
                }
            }
        };

        _mockApiClient
            .Setup(x => x.RegisterAsync(It.IsAny<string>()))
            .ReturnsAsync(expectedResponse);

        var onLoginSuccessCalled = false;

        _viewModel.OnLoginSuccess += (s, args) => onLoginSuccessCalled = true;

        // Act
        await _viewModel.RegisterCommand.ExecuteAsync(null);

        // Assert
        Assert.True(onLoginSuccessCalled);
    }

    [Theory]
    [InlineData("a", false)]  // Too short
    [InlineData("ValidUser123", true)]
    [InlineData("A", false)]
    [InlineData("VeryLongNameThatExceeds50CharactersForTestingValidation12345", false)]
    public void DisplayName_Validation_ShouldBeCorrect(string displayName, bool shouldBeValid)
    {
        // Arrange
        _viewModel.DisplayName = displayName;
        _viewModel.ServerUrl = "https://api.test.com";

        // Act
        var result = _viewModel.LoginCommand.CanExecute(null);

        // Assert
        Assert.Equal(shouldBeValid, result);
    }
}
```

### ChatViewModel Tests

```csharp
using Xunit;
using Moq;
using Avalonia.Collections;
using RuneDesktop.UI.ViewModels;
using RuneDesktop.Core.Services;
using RuneDesktop.Core.Models;

namespace RuneDesktop.UI.Tests.ViewModels;

public class ChatViewModelTests
{
    private readonly Mock<RuneEngine> _mockEngine;
    private readonly Mock<ApiClient> _mockApiClient;
    private readonly ChatViewModel _viewModel;

    public ChatViewModelTests()
    {
        _mockEngine = new Mock<RuneEngine>();
        _mockApiClient = new Mock<ApiClient>();
        _viewModel = new ChatViewModel(_mockEngine.Object, _mockApiClient.Object);
    }

    [Fact]
    public void Initialize_ShouldSubscribeToEngineEvents()
    {
        // Act
        _viewModel.Initialize("https://api.test.com", "test-jwt");

        // Assert
        _mockApiClient.Verify(x => x.SetServerUrl("https://api.test.com"), Times.Once);
        _mockApiClient.Verify(x => x.SetJwtToken("test-jwt"), Times.Once);
        _mockEngine.Verify(x => x.Initialize(_mockApiClient.Object), Times.Once);
    }

    [Fact]
    public async Task SendMessage_WithValidInput_ShouldAddMessagesToCollection()
    {
        // Arrange
        _viewModel.Initialize("https://api.test.com", "test-jwt");
        _viewModel.InputText = "Hello, bot!";

        var botResponse = new ApiResponse<ChatMessageResponse>
        {
            IsSuccess = true,
            Data = new ChatMessageResponse
            {
                Content = "Hello, human!",
                Timestamp = DateTime.UtcNow
            }
        };

        _mockApiClient
            .Setup(x => x.SendChatMessageAsync(It.IsAny<string>()))
            .ReturnsAsync(botResponse);

        // Act
        await _viewModel.SendMessageCommand.ExecuteAsync(null);

        // Assert
        Assert.NotEmpty(_viewModel.Messages);
        Assert.Equal(2, _viewModel.Messages.Count); // User + Bot
        Assert.Equal("Hello, bot!", _viewModel.Messages[0].Content);
        Assert.Equal("Hello, human!", _viewModel.Messages[1].Content);
    }

    [Fact]
    public async Task SendMessage_WithServerError_ShouldDisplayErrorMessage()
    {
        // Arrange
        _viewModel.Initialize("https://api.test.com", "test-jwt");
        _viewModel.InputText = "Test message";

        var errorResponse = new ApiResponse<ChatMessageResponse>
        {
            IsSuccess = false,
            Message = "Server error occurred"
        };

        _mockApiClient
            .Setup(x => x.SendChatMessageAsync(It.IsAny<string>()))
            .ReturnsAsync(errorResponse);

        // Act
        await _viewModel.SendMessageCommand.ExecuteAsync(null);

        // Assert
        // Should contain error message
        var errorMessages = _viewModel.Messages.Where(m => m.Role == ChatMessageRole.System).ToList();
        Assert.NotEmpty(errorMessages);
        Assert.Contains("Server error occurred", errorMessages.First().Content);
    }

    [Fact]
    public void Reset_ShouldClearAllData()
    {
        // Arrange
        _viewModel.Initialize("https://api.test.com", "test-jwt");
        _viewModel.Messages.Add(new ChatMessageViewModel(new ChatMessage
        {
            Role = ChatMessageRole.User,
            Content = "Test",
            Timestamp = DateTime.UtcNow
        }));
        _viewModel.MemoryCount = 5;

        // Act
        _viewModel.Reset();

        // Assert
        Assert.Empty(_viewModel.Messages);
        Assert.Equal(0, _viewModel.MemoryCount);
        Assert.Equal("", _viewModel.InputText);
    }
}
```

### Converter Tests

```csharp
using Xunit;
using RuneDesktop.UI.Converters;
using Avalonia.Media;
using Avalonia.Layout;

namespace RuneDesktop.UI.Tests.Converters;

public class ConverterTests
{
    [Theory]
    [InlineData("Chat", "Chat", true)]
    [InlineData("Login", "Chat", false)]
    [InlineData("Chat", "Login", false)]
    public void EnumToBoolConverter_ShouldCompareCorrectly(string value, string parameter, bool expected)
    {
        // Arrange
        var converter = new EnumToBoolConverter();

        // Act
        var result = converter.Convert(value, typeof(bool), parameter, null);

        // Assert
        Assert.Equal(expected, result);
    }

    [Theory]
    [InlineData("text", true)]
    [InlineData("", false)]
    [InlineData(null, false)]
    public void StringToBoolConverter_ShouldConvertCorrectly(string value, bool expected)
    {
        // Arrange
        var converter = new StringToBoolConverter();

        // Act
        var result = converter.Convert(value, typeof(bool), null, null);

        // Assert
        Assert.Equal(expected, result);
    }

    [Theory]
    [InlineData(true, "#3B82F6")]   // Blue for user
    [InlineData(false, "#E5E7EB")]  // Gray for bot
    public void BoolToBrushConverter_ShouldReturnCorrectColor(bool isUser, string expectedColor)
    {
        // Arrange
        var converter = new BoolToBrushConverter();

        // Act
        var result = converter.Convert(isUser, typeof(SolidColorBrush), null, null);

        // Assert
        Assert.NotNull(result);
        Assert.IsType<SolidColorBrush>(result);
    }

    [Theory]
    [InlineData(true, HorizontalAlignment.Right)]
    [InlineData(false, HorizontalAlignment.Left)]
    public void BoolToHAlignmentConverter_ShouldReturnCorrectAlignment(bool isUser, HorizontalAlignment expected)
    {
        // Arrange
        var converter = new BoolToHAlignmentConverter();

        // Act
        var result = converter.Convert(isUser, typeof(HorizontalAlignment), null, null);

        // Assert
        Assert.Equal(expected, result);
    }
}
```

## Integration Tests

```csharp
using Xunit;
using Moq;
using RuneDesktop.UI.ViewModels;
using RuneDesktop.Core.Services;
using RuneDesktop.Core.Models;

namespace RuneDesktop.UI.Tests.Integration;

public class LoginToChat_IntegrationTests
{
    [Fact]
    public async Task FullAuthenticationFlow_ShouldNavigateToChat()
    {
        // Arrange
        var mockApiClient = new Mock<ApiClient>();
        var mockRuneEngine = new Mock<RuneEngine>();

        var mainVm = new MainViewModel();
        var loginVm = mainVm.LoginViewModel;
        var chatVm = mainVm.ChatViewModel;

        loginVm.ServerUrl = "https://api.test.com";
        loginVm.DisplayName = "TestUser";

        var loginResponse = new ApiResponse<LoginResponse>
        {
            IsSuccess = true,
            Data = new LoginResponse
            {
                JwtToken = "test-jwt",
                AgentProfile = new AgentProfile
                {
                    AgentId = "agent-123",
                    Name = "TestUser",
                    Erc8004TokenId = "token-456"
                }
            }
        };

        mockApiClient
            .Setup(x => x.LoginAsync(It.IsAny<string>()))
            .ReturnsAsync(loginResponse);

        // Act
        await loginVm.LoginCommand.ExecuteAsync(null);

        // Assert
        Assert.True(mainVm.IsLoggedIn);
        Assert.NotNull(mainVm.AgentProfile);
        Assert.Equal("TestUser", mainVm.AgentProfile.Name);
    }
}
```

## Mock Classes

Create reusable mocks for testing:

```csharp
// MockApiClient.cs
public class MockApiClient : ApiClient
{
    public override Task<ApiResponse<LoginResponse>> LoginAsync(string displayName)
    {
        return Task.FromResult(new ApiResponse<LoginResponse>
        {
            IsSuccess = true,
            Data = new LoginResponse
            {
                JwtToken = "mock-jwt",
                AgentProfile = new AgentProfile
                {
                    AgentId = "agent-123",
                    Name = displayName
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
                Content = "Mock response",
                Timestamp = DateTime.UtcNow
            }
        });
    }
}
```

## Running Tests

```bash
# Run all tests
dotnet test

# Run with verbose output
dotnet test --logger "console;verbosity=detailed"

# Run specific test class
dotnet test --filter "ClassName=LoginViewModelTests"

# Run with code coverage
dotnet test /p:CollectCoverage=true
```

## Best Practices

1. **Isolate Dependencies**: Mock ApiClient and RuneEngine
2. **Test Behavior**: Assert on commands firing, properties changing
3. **Use InlineData**: Parametrize tests for multiple scenarios
4. **Clear Naming**: Test_Action_ExpectedResult pattern
5. **Arrange-Act-Assert**: Structure each test clearly
6. **Mock Events**: Capture and assert on event handlers

## Debugging Tests

Add breakpoints in test code:
```csharp
[Fact]
public async Task MyTest()
{
    // Set breakpoint here
    await _viewModel.MyCommand.ExecuteAsync(null);
    
    // Assert with step-through
    Assert.True(condition);
}
```

Run with debugging:
```bash
dotnet test -v d
```

## Coverage Goals

Aim for:
- ViewModel logic: 80%+ coverage
- Converters: 100% coverage
- Views: Manually test (UI testing is expensive)
- Helpers: 80%+ coverage

## Continuous Integration

Example GitHub Actions:
```yaml
- name: Run tests
  run: dotnet test --logger "trx;LogFileName=test-results.trx"

- name: Publish test results
  uses: dorny/test-reporter@v1
  if: always()
  with:
    name: Test Results
    path: '*.trx'
    reporter: 'dotnet trx'
```

## Test Naming Convention

```
{MethodName}_{Scenario}_{ExpectedResult}

Examples:
- LoginCommand_WithValidInput_ShouldFireOnLoginSuccess
- SendMessage_WithServerError_ShouldDisplayErrorMessage
- DisplayName_Validation_ShouldRejectShortNames
```

## Common Assertions

```csharp
Assert.True(condition);
Assert.False(condition);
Assert.Equal(expected, actual);
Assert.NotEqual(unexpected, actual);
Assert.Null(obj);
Assert.NotNull(obj);
Assert.Empty(collection);
Assert.NotEmpty(collection);
Assert.Contains(item, collection);
Assert.Throws<Exception>(() => method());
```

## Resources

- **Xunit.net**: https://xunit.net/
- **Moq**: https://github.com/moq/moq4
- **Avalonia Testing**: https://docs.avaloniaui.net/guides/development-guides/unit-testing
