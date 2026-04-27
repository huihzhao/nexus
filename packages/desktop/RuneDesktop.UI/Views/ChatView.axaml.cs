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
            // Snap to bottom on first show, after layout has run.
            ScheduleScrollToEnd();
        };

        DetachedFromVisualTree += (_, _) =>
        {
            UnhookMessages();
            _messageScrollViewer = null;
        };

        DataContextChanged += (_, _) =>
        {
            HookMessages(DataContext as ChatViewModel);
            WireUpFilePicker(DataContext as ChatViewModel);
            ScheduleScrollToEnd();
        };
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
        }
    }
}
