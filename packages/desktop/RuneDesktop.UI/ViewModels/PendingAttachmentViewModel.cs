using CommunityToolkit.Mvvm.ComponentModel;
using RuneDesktop.Core.Services;

namespace RuneDesktop.UI.ViewModels;

/// <summary>
/// View-model wrapper for an attachment staged in the input bar before
/// the user hits Send. Holds the raw <see cref="ChatAttachment"/> plus
/// presentation helpers (display size, type icon hint).
/// </summary>
public partial class PendingAttachmentViewModel : ObservableObject
{
    public ChatAttachment Attachment { get; }

    public string Name => Attachment.Name;
    public string Mime => Attachment.Mime;
    public long SizeBytes => Attachment.SizeBytes;

    /// <summary>"1.2 KB" / "340 B" / "2.4 MB" — whichever fits.</summary>
    public string DisplaySize
    {
        get
        {
            if (SizeBytes < 1024) return $"{SizeBytes} B";
            if (SizeBytes < 1024 * 1024) return $"{SizeBytes / 1024.0:0.#} KB";
            return $"{SizeBytes / (1024.0 * 1024):0.##} MB";
        }
    }

    /// <summary>True if we managed to read the file as text.</summary>
    public bool IsText => Attachment.ContentText is not null;

    public PendingAttachmentViewModel(ChatAttachment attachment)
    {
        Attachment = attachment;
    }
}
