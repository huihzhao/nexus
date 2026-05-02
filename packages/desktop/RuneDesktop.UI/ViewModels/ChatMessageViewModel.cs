using System.Collections.Generic;
using System.Collections.ObjectModel;
using Avalonia.Layout;
using Avalonia.Media;
using CommunityToolkit.Mvvm.ComponentModel;
using RuneDesktop.Core.Models;
using RuneDesktop.Core.Services;

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

    /// <summary>Attachment chips rendered above the message text. Set
    /// from history reload (server returns structured attachments via
    /// /agent/messages) or from the optimistic SendMessageAsync path
    /// when the user just attached files. Empty for assistant messages
    /// and for user messages without attachments.</summary>
    public ObservableCollection<MessageAttachmentViewModel> Attachments { get; } = new();

    public bool HasAttachments => Attachments.Count > 0;

    public ChatMessageViewModel(ChatMessage model,
                                 IReadOnlyList<MessageAttachmentViewModel>? attachments = null)
    {
        _content = model.Content;
        _isUser = model.Role == ChatMessageRole.User;
        _formattedTime = FormatTime(model.Timestamp);
        if (attachments is not null)
        {
            foreach (var a in attachments) Attachments.Add(a);
        }
        Attachments.CollectionChanged += (_, _) => OnPropertyChanged(nameof(HasAttachments));

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

/// <summary>One attachment chip rendered inside a message bubble.
/// The XAML template binds Glyph (type-specific icon) + Name + Size
/// to a horizontal pill. Click to view is a future enhancement —
/// for now the chip is informational.</summary>
public partial class MessageAttachmentViewModel : ObservableObject
{
    [ObservableProperty] private string _name = "";
    [ObservableProperty] private string _mime = "";
    [ObservableProperty] private long _sizeBytes;

    /// <summary>Type-specific icon. Cheap heuristic on extension —
    /// good enough for visual distinction without a full mime DB.</summary>
    public string Glyph
    {
        get
        {
            var ext = (System.IO.Path.GetExtension(Name) ?? "").ToLowerInvariant();
            return ext switch
            {
                ".pdf" => "📄",
                ".doc" or ".docx" => "📝",
                ".xls" or ".xlsx" or ".csv" => "📊",
                ".ppt" or ".pptx" => "📽",
                ".png" or ".jpg" or ".jpeg" or ".gif" or ".webp" => "🖼",
                ".mp3" or ".wav" or ".m4a" or ".ogg" => "🎵",
                ".mp4" or ".mov" or ".avi" or ".mkv" => "🎞",
                ".zip" or ".tar" or ".gz" => "📦",
                ".json" or ".yaml" or ".yml" or ".toml" => "⚙",
                ".md" or ".txt" or ".log" => "📃",
                ".py" or ".js" or ".ts" or ".cs" or ".go" or ".rs" => "⌨",
                _ => "📎",
            };
        }
    }

    public string SizeText
    {
        get
        {
            if (SizeBytes <= 0) return "";
            if (SizeBytes < 1024) return $"{SizeBytes} B";
            if (SizeBytes < 1024 * 1024) return $"{SizeBytes / 1024.0:0.#} KB";
            return $"{SizeBytes / (1024.0 * 1024.0):0.##} MB";
        }
    }

    public static MessageAttachmentViewModel FromHistory(HistoryAttachmentInfo info)
        => new()
        {
            Name = info.Name,
            Mime = info.Mime,
            SizeBytes = info.SizeBytes,
        };

    public static MessageAttachmentViewModel FromPending(ChatAttachment attachment)
        => new()
        {
            Name = attachment.Name,
            Mime = attachment.Mime,
            SizeBytes = attachment.SizeBytes,
        };
}
