using Avalonia.Layout;
using Avalonia.Media;
using CommunityToolkit.Mvvm.ComponentModel;
using RuneDesktop.Core.Models;

namespace RuneDesktop.UI.ViewModels;

public partial class ChatMessageViewModel : ObservableObject
{
    [ObservableProperty] private string _content;
    [ObservableProperty] private bool _isUser;
    [ObservableProperty] private string _formattedTime;

    // UI properties for message styling
    public IBrush BubbleColor { get; }
    public IBrush TextColor { get; }
    public IBrush TimeColor { get; }
    public HorizontalAlignment HAlignment { get; }

    public ChatMessageViewModel(ChatMessage model)
    {
        _content = model.Content;
        _isUser = model.Role == ChatMessageRole.User;
        _formattedTime = FormatTime(model.Timestamp);

        // Dark-theme palette aligned with App.axaml tokens. Hard-coded
        // here (rather than DynamicResource'd from XAML) because the
        // brushes need to be available at view-model construction time
        // for ItemsControl bindings — Avalonia DynamicResource doesn't
        // resolve cleanly through pure CLR properties.
        if (_isUser)
        {
            // Claude orange — user input is the actor
            BubbleColor = new SolidColorBrush(Color.Parse("#D97757"));
            TextColor   = new SolidColorBrush(Color.Parse("#1F2329"));
            TimeColor   = new SolidColorBrush(Color.Parse("#7A2F1A"));
            HAlignment  = HorizontalAlignment.Right;
        }
        else
        {
            // Card surface for assistant — quiet, gives prominence to user
            BubbleColor = new SolidColorBrush(Color.Parse("#262A31"));
            TextColor   = new SolidColorBrush(Color.Parse("#E8E6DC"));
            TimeColor   = new SolidColorBrush(Color.Parse("#6B6E69"));
            HAlignment  = HorizontalAlignment.Left;
        }
    }

    private static string FormatTime(DateTime timestamp)
    {
        var diff = DateTime.UtcNow - timestamp;
        if (diff.TotalSeconds < 60) return "just now";
        if (diff.TotalMinutes < 60) return $"{(int)diff.TotalMinutes}m ago";
        if (diff.TotalHours < 24) return $"{(int)diff.TotalHours}h ago";
        return timestamp.ToString("MMM d, h:mm tt");
    }
}
