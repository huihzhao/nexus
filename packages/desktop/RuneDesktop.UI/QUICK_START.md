# Quick Start Guide

## Prerequisites

- .NET 8.0 SDK
- A code editor (VS Code, Visual Studio, or Rider)
- RuneDesktop.Core project in adjacent directory

## Project Setup

1. **Clone the Repository**
   ```bash
   git clone <repo-url>
   cd rune-desktop
   ```

2. **Restore Dependencies**
   ```bash
   dotnet restore
   ```

3. **Build**
   ```bash
   dotnet build RuneDesktop.UI/RuneDesktop.UI.csproj
   ```

4. **Run**
   ```bash
   dotnet run --project RuneDesktop.UI
   ```

## File Structure at a Glance

```
RuneDesktop.UI/
├── ViewModels/          ← Business logic (MVVM)
├── Views/               ← UI layouts (XAML)
├── Converters/          ← Data binding helpers
├── Helpers/             ← Utilities
└── Assets/              ← Images, icons
```

## Key ViewModel Pattern

All ViewModels use MVVM Community Toolkit:

```csharp
[ObservableProperty]
private string displayName = "";

[RelayCommand]
public async Task LoginAsync()
{
    // Async command implementation
}
```

The `[ObservableProperty]` and `[RelayCommand]` attributes are source-generated at compile time. No need for boilerplate INotifyPropertyChanged.

## Adding a New Feature

### 1. Create a ViewModel

File: `ViewModels/MyFeatureViewModel.cs`
```csharp
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;

namespace RuneDesktop.UI.ViewModels;

public partial class MyFeatureViewModel : ObservableObject
{
    [ObservableProperty]
    private string inputText = "";

    [RelayCommand]
    public async Task ProcessInput()
    {
        // Implementation
    }
}
```

### 2. Create a View

File: `Views/MyFeatureView.axaml`
```xml
<UserControl xmlns="https://github.com/avaloniaui"
             x:Class="RuneDesktop.UI.Views.MyFeatureView">
    <TextBox Text="{Binding InputText}"/>
    <Button Content="Process"
            Command="{Binding ProcessInputCommand}"/>
</UserControl>
```

File: `Views/MyFeatureView.axaml.cs`
```csharp
using Avalonia.Controls;
using RuneDesktop.UI.ViewModels;

namespace RuneDesktop.UI.Views;

public partial class MyFeatureView : UserControl
{
    public MyFeatureView()
    {
        InitializeComponent();
    }
}
```

### 3. Integrate into Navigation

Update `MainViewModel.cs`:
```csharp
public MyFeatureViewModel MyFeatureViewModel { get; }

public MainViewModel()
{
    // ... existing code ...
    MyFeatureViewModel = new MyFeatureViewModel();
}
```

Update `MainWindow.axaml` to show/hide the view based on navigation state.

## Common Tasks

### Binding to a Property

In ViewModel:
```csharp
[ObservableProperty]
private string title = "Default Title";
```

In XAML:
```xml
<TextBlock Text="{Binding Title}"/>
```

### Creating an Async Command

```csharp
[RelayCommand]
public async Task FetchData()
{
    try
    {
        var result = await _apiClient.FetchAsync();
        // Update properties
    }
    catch (Exception ex)
    {
        ErrorMessage = ex.Message;
    }
}
```

In XAML:
```xml
<Button Command="{Binding FetchDataCommand}" Content="Fetch"/>
```

### Using a Converter

Register in `App.axaml`:
```xml
<converters:MyConverter x:Key="MyConverterKey"/>
```

Use in XAML:
```xml
<TextBlock Text="{Binding Value, Converter={StaticResource MyConverterKey}}"/>
```

### Styling Elements

Use colors defined in `App.axaml`:
```xml
<Border Background="{StaticResource RuneGoldBrush}"/>
```

Or inline:
```xml
<Border Background="#F0B90B"/>
```

### Collections and ItemsControl

```csharp
public partial class MyViewModel : ObservableObject
{
    [ObservableProperty]
    private AvaloniaList<ItemViewModel> items = new();

    public MyViewModel()
    {
        Items.Add(new ItemViewModel { Name = "Item 1" });
    }
}
```

In XAML:
```xml
<ItemsControl Items="{Binding Items}">
    <ItemsControl.ItemTemplate>
        <DataTemplate>
            <TextBlock Text="{Binding Name}"/>
        </DataTemplate>
    </ItemsControl.ItemTemplate>
</ItemsControl>
```

## Debugging Tips

1. **Console Output**
   ```csharp
   System.Diagnostics.Debug.WriteLine("My debug message");
   ```

2. **Breakpoints** (in VS Code or Visual Studio)
   - Set breakpoint in ViewModel
   - Run with debugger
   - Step through code

3. **Data Binding Issues**
   - Check XAML syntax (x:Class must match code-behind)
   - Verify property names match exactly (case-sensitive)
   - Use Converter debugging to test value transformations

4. **Event Not Firing**
   - Ensure Command property name matches RelayCommand method name + "Command"
   - Check that DataContext is set (usually automatic in code-behind)

## Testing

Write unit tests for ViewModels:

```csharp
[TestClass]
public class LoginViewModelTests
{
    [TestMethod]
    public async Task LoginCommand_WithValidInput_ShouldInvokeLoginSuccess()
    {
        // Arrange
        var mockApiClient = new MockApiClient();
        var vm = new LoginViewModel(mockApiClient);
        vm.ServerUrl = "https://test.com";
        vm.DisplayName = "TestUser";

        // Act
        await vm.LoginCommand.ExecuteAsync(null);

        // Assert
        Assert.IsTrue(vm.IsLoading == false);
    }
}
```

## Performance Considerations

1. **Use AvaloniaList<T>** for observable collections (faster than ObservableCollection)
2. **Async/await** for long-running operations (don't block UI thread)
3. **Virtualization** for large lists (ItemsControl with ScrollViewer)
4. **Lazy loading** for data-heavy views

## Common Pitfalls

1. **Forgetting `partial` in ViewModel class**
   ```csharp
   public partial class MyViewModel : ObservableObject // ← Must be partial
   ```

2. **Wrong binding path**
   ```xml
   <!-- ✗ Wrong - property doesn't exist -->
   <TextBlock Text="{Binding DisplayNamee}"/>
   
   <!-- ✓ Correct -->
   <TextBlock Text="{Binding DisplayName}"/>
   ```

3. **Not setting DataContext**
   ```csharp
   public MyView()
   {
       InitializeComponent();
       DataContext = new MyViewModel(); // ← Must be set
   }
   ```

4. **Async void commands**
   ```csharp
   // ✗ Wrong - fire-and-forget
   [RelayCommand]
   public async void BadCommand() { }
   
   // ✓ Correct - returns Task
   [RelayCommand]
   public async Task GoodCommand() { }
   ```

## Resources

- **Avalonia Docs**: https://docs.avaloniaui.net/
- **MVVM Toolkit**: https://learn.microsoft.com/en-us/windows/communitytoolkit/mvvm/
- **GitHub Issues**: Use for bug reports and feature requests

## Getting Help

1. Check ARCHITECTURE.md for design patterns
2. Review existing ViewModels for examples
3. Search online for similar Avalonia patterns
4. File an issue with minimal reproduction case

## Next Steps

- [ ] Review ARCHITECTURE.md for system design
- [ ] Check BUILD.md for build/publish details
- [ ] Read INTEGRATION.md for Core layer connection
- [ ] Run the app locally: `dotnet run --project RuneDesktop.UI`
- [ ] Try modifying a ViewModel and see changes in real-time

Happy coding!
