using System.Collections.Generic;
using System.Collections.Specialized;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using RuneDesktop.UI.ViewModels;

namespace RuneDesktop.UI.Views;

public partial class ChatView : UserControl
{
    private ScrollViewer? _messageScrollViewer;
    private INotifyCollectionChanged? _observedMessages;

    public ChatView()
    {
        InitializeComponent();

        AttachedToVisualTree += (_, _) =>
        {
            _messageScrollViewer = this.FindControl<ScrollViewer>("MessageScrollViewer");
            // If the VM was set before the visual tree attached, hook now.
            HookMessages(DataContext as ChatViewModel);
            WireUpFilePicker(DataContext as ChatViewModel);
            WireUpDragDrop();
            // Snap to bottom on first show, after layout has run.
            ScheduleScrollToEnd();
        };

        DetachedFromVisualTree += (_, _) =>
        {
            UnhookMessages();
            UnwireDragDrop();
            _messageScrollViewer = null;
        };

        DataContextChanged += (_, _) =>
        {
            HookMessages(DataContext as ChatViewModel);
            WireUpFilePicker(DataContext as ChatViewModel);
            ScheduleScrollToEnd();
        };
    }

    // ── Drag-and-drop file attachments ───────────────────────────────
    //
    // The chat-area Grid in ChatView.axaml is marked DragDrop.AllowDrop
    // so files dragged from Finder / Explorer onto the chat surface
    // get picked up here. We wire DragEnter / DragLeave / DragOver /
    // Drop on attach, unwire on detach. The handlers flip
    // ChatViewModel.IsDraggingOverChat so the "Drop to attach"
    // overlay appears, then forward dropped IStorageFile entries to
    // ChatViewModel.HandleDroppedFilesAsync — the same upload pipeline
    // the paperclip button uses.

    private Grid? _chatColumnGrid;

    private void WireUpDragDrop()
    {
        if (_chatColumnGrid is not null) return;
        _chatColumnGrid = this.FindControl<Grid>("ChatColumnGrid");
        if (_chatColumnGrid is null) return;
        _chatColumnGrid.AddHandler(DragDrop.DragEnterEvent, OnChatDragEnter);
        _chatColumnGrid.AddHandler(DragDrop.DragOverEvent, OnChatDragOver);
        _chatColumnGrid.AddHandler(DragDrop.DragLeaveEvent, OnChatDragLeave);
        _chatColumnGrid.AddHandler(DragDrop.DropEvent, OnChatDrop);
    }

    private void UnwireDragDrop()
    {
        if (_chatColumnGrid is null) return;
        _chatColumnGrid.RemoveHandler(DragDrop.DragEnterEvent, OnChatDragEnter);
        _chatColumnGrid.RemoveHandler(DragDrop.DragOverEvent, OnChatDragOver);
        _chatColumnGrid.RemoveHandler(DragDrop.DragLeaveEvent, OnChatDragLeave);
        _chatColumnGrid.RemoveHandler(DragDrop.DropEvent, OnChatDrop);
        _chatColumnGrid = null;
    }

    // ⚠ Avalonia 11.3 deprecated DragEventArgs.Data + DataFormats.Files
    // in favour of DataTransfer + DataFormat.File. The new API is
    // safe but its surface (IAsyncEnumerable<IDataTransferItem>?) is
    // a meaningful change — migrating here would also need a
    // matching change in vm.HandleDroppedFilesAsync's signature.
    // Suppress until the chat-attachment pipeline gets a wider
    // refactor (file-handling Phase 2 / Sprint 1 P1).
#pragma warning disable CS0618
    private static bool HasFiles(DragEventArgs e)
        => e.Data?.Contains(DataFormats.Files) == true;
#pragma warning restore CS0618

    private void OnChatDragEnter(object? sender, DragEventArgs e)
    {
        if (!HasFiles(e)) return;
        e.DragEffects = DragDropEffects.Copy;
        if (DataContext is ChatViewModel vm) vm.IsDraggingOverChat = true;
        e.Handled = true;
    }

    private void OnChatDragOver(object? sender, DragEventArgs e)
    {
        if (!HasFiles(e))
        {
            e.DragEffects = DragDropEffects.None;
            return;
        }
        e.DragEffects = DragDropEffects.Copy;
        e.Handled = true;
    }

    private void OnChatDragLeave(object? sender, DragEventArgs e)
    {
        if (DataContext is ChatViewModel vm) vm.IsDraggingOverChat = false;
    }

    private async void OnChatDrop(object? sender, DragEventArgs e)
    {
        if (DataContext is not ChatViewModel vm) return;
        vm.IsDraggingOverChat = false;
        if (!HasFiles(e)) return;
        e.Handled = true;

#pragma warning disable CS0618 // see HasFiles comment
        var items = e.Data!.GetFiles();
#pragma warning restore CS0618
        if (items is null) return;

        // GetFiles can yield IStorageItem (folders included). We only
        // hand the upload pipeline files — folders are silently skipped.
        var files = new List<IStorageFile>();
        foreach (var item in items)
        {
            if (item is IStorageFile f) files.Add(f);
        }
        if (files.Count == 0) return;

        try { await vm.HandleDroppedFilesAsync(files); }
        catch { /* errors land on vm.AttachmentError already */ }
    }

    /// <summary>
    /// Avalonia's file picker is reached via the <see cref="TopLevel"/>
    /// (Window) of the visual tree, which the ViewModel by design doesn't
    /// know about. We inject a closure that opens it; the VM uses it from
    /// the <c>AttachFilesCommand</c>.
    /// </summary>
    private void WireUpFilePicker(ChatViewModel? vm)
    {
        if (vm is null) return;
        vm.FilePickerProvider = OpenFilePickerAsync;
    }

    private async Task<IReadOnlyList<IStorageFile>> OpenFilePickerAsync()
    {
        var top = TopLevel.GetTopLevel(this);
        if (top is null) return [];

        var result = await top.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = "Attach files to send to your agent",
            AllowMultiple = true,
            // No file-type filter per product requirement: any file goes.
        });
        return result;
    }

    private void HookMessages(ChatViewModel? vm)
    {
        UnhookMessages();
        if (vm is null) return;

        _observedMessages = vm.Messages;
        _observedMessages.CollectionChanged += OnMessagesChanged;
    }

    private void UnhookMessages()
    {
        if (_observedMessages is null) return;
        _observedMessages.CollectionChanged -= OnMessagesChanged;
        _observedMessages = null;
    }

    private void OnMessagesChanged(object? sender, NotifyCollectionChangedEventArgs e)
    {
        // Only stick to the bottom when items are appended; don't fight the
        // user scrolling up to read history during a Reset/Replace.
        if (e.Action == NotifyCollectionChangedAction.Add ||
            e.Action == NotifyCollectionChangedAction.Reset)
        {
            ScheduleScrollToEnd();
        }
    }

    /// <summary>
    /// ScrollToEnd needs to run AFTER the new item has been measured and
    /// added to the visual tree, otherwise the ScrollViewer's extent is
    /// still the old size and we end up scrolling to the previous bottom.
    /// Posting at Background priority lets the layout pass complete first.
    /// </summary>
    private void ScheduleScrollToEnd()
    {
        if (_messageScrollViewer is null) return;
        Dispatcher.UIThread.Post(() => _messageScrollViewer?.ScrollToEnd(),
                                 DispatcherPriority.Background);
    }

    private void InputBox_KeyDown(object? sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter)
        {
            if (e.KeyModifiers.HasFlag(KeyModifiers.Shift))
            {
                // Shift+Enter: insert newline
                if (sender is TextBox tb)
                {
                    var pos = tb.CaretIndex;
                    tb.Text = tb.Text?.Insert(pos, "\n") ?? "\n";
                    tb.CaretIndex = pos + 1;
                }
                e.Handled = true;
            }
            else
            {
                // Enter: send message
                e.Handled = true;
                if (DataContext is ChatViewModel vm && vm.SendMessageCommand.CanExecute(null))
                {
                    vm.SendMessageCommand.Execute(null);
                }
            }
            return;
        }

        // Cmd+V (macOS) / Ctrl+V (Win/Linux) — intercept paste and
        // check the clipboard for files. If files are present, hand
        // them to the upload pipeline (same flow as drag-drop).
        // Pasted plain text falls through to the normal TextBox
        // behaviour so users can still paste a URL into the input.
        bool platformMod = e.KeyModifiers.HasFlag(KeyModifiers.Meta)
                        || e.KeyModifiers.HasFlag(KeyModifiers.Control);
        if (e.Key == Key.V && platformMod)
        {
            _ = TryHandleClipboardFilesAsync();
            // Don't set Handled: we want plain text paste to keep
            // working. The async helper checks for files; if none,
            // it does nothing and the default text paste runs.
        }
    }

    /// <summary>If the clipboard contains file references (e.g. user
    /// copied files in Finder/Explorer), upload them via the same
    /// path drag-drop uses. No-op when the clipboard only holds
    /// text — that case keeps the default TextBox paste behaviour.</summary>
    private async System.Threading.Tasks.Task TryHandleClipboardFilesAsync()
    {
        if (DataContext is not ChatViewModel vm) return;
        var top = TopLevel.GetTopLevel(this);
        var clip = top?.Clipboard;
        if (clip is null) return;

        // Avalonia exposes copied-file references via DataFormats.Files
        // on every platform that supports it. Pasted-image-from-screen
        // shot data is a separate clipboard format we don't tackle yet
        // (per-OS variance + would need temp-file roundtrip).
        try
        {
            // Same Avalonia-11.3 obsolete API as drag-drop above —
            // migrate together when we revisit the file pipeline.
#pragma warning disable CS0618
            var formats = await clip.GetFormatsAsync();
            if (formats is null || !formats.Contains(DataFormats.Files)) return;
            var raw = await clip.GetDataAsync(DataFormats.Files);
#pragma warning restore CS0618
            if (raw is not System.Collections.IEnumerable items) return;

            var files = new List<IStorageFile>();
            foreach (var item in items)
            {
                if (item is IStorageFile f) files.Add(f);
            }
            if (files.Count == 0) return;
            await vm.HandleDroppedFilesAsync(files);
        }
        catch { /* best-effort; AttachmentError surfaces real failures */ }
    }
}
