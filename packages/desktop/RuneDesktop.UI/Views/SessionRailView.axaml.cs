using System;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Threading;
using RuneDesktop.UI.ViewModels;

namespace RuneDesktop.UI.Views;

/// <summary>
/// Code-behind for <c>SessionRailView.axaml</c> — handles row clicks
/// (session select) and the delete-button click (with confirmation).
///
/// We open a small modal Window for the delete confirmation rather
/// than inlining a flyout because the user message about "this also
/// cleans up chain data" deserves a real dialog with the BSC
/// immutability disclaimer in plain sight, not a fly-by toast.
/// </summary>
public partial class SessionRailView : UserControl
{
    public SessionRailView()
    {
        InitializeComponent();
    }

    private void OnSessionRowClicked(object? sender, RoutedEventArgs e)
    {
        if (sender is not Button btn) return;
        if (btn.Tag is not string sessionId) return;
        if (DataContext is not SessionListViewModel rail) return;
        rail.Select(sessionId);
    }

    /// <summary>Double-click a session row → enter inline rename mode.
    /// Handler resolves the row VM via the button's DataContext (each
    /// row in the ItemsControl gets its own SessionItemViewModel as
    /// DataContext) and flips IsRenaming there. Default sessions are
    /// silently ignored — they can't be renamed.</summary>
    private void OnSessionRowDoubleTapped(object? sender, TappedEventArgs e)
    {
        if (sender is not Button btn) return;
        if (btn.DataContext is not SessionItemViewModel row) return;
        row.BeginRename();
        e.Handled = true;
    }

    /// <summary>Enter saves, Esc cancels. Anything else falls through.
    /// We push the actual rename through the rail VM so it talks to
    /// the API and refreshes the row's title in-place.</summary>
    private async void OnSessionRenameKeyDown(object? sender, KeyEventArgs e)
    {
        if (sender is not TextBox tb) return;
        if (tb.DataContext is not SessionItemViewModel row) return;
        if (DataContext is not SessionListViewModel rail) return;

        if (e.Key == Key.Enter)
        {
            e.Handled = true;
            var newTitle = (tb.Text ?? "").Trim();
            if (string.IsNullOrEmpty(newTitle))
            {
                // Empty input → keep current title, just exit edit mode.
                row.CancelRename();
                return;
            }
            row.IsRenaming = false;
            // Optimistic in-row update: show the new title immediately
            // so there's no lag while the server round-trip resolves.
            row.Title = newTitle;
            await rail.RenameAsync(row.Id, newTitle);
        }
        else if (e.Key == Key.Escape)
        {
            e.Handled = true;
            row.CancelRename();
        }
    }

    /// <summary>Lost focus = treat as Esc (cancel). We don't auto-save
    /// on blur because the user might've clicked away by accident; an
    /// explicit Enter to commit is the safer default and matches how
    /// most other apps (ChatGPT, Cursor, VS Code rename) behave.</summary>
    private void OnSessionRenameLostFocus(object? sender, RoutedEventArgs e)
    {
        if (sender is not TextBox tb) return;
        if (tb.DataContext is not SessionItemViewModel row) return;
        if (!row.IsRenaming) return;
        // If the title has been edited but not committed, commit it
        // here as a courtesy — losing focus right after typing should
        // not silently throw away the user's change.
        if (DataContext is not SessionListViewModel rail) { row.CancelRename(); return; }
        var newTitle = (tb.Text ?? "").Trim();
        if (!string.IsNullOrEmpty(newTitle) && newTitle != row.Title)
        {
            row.IsRenaming = false;
            row.Title = newTitle;
            _ = rail.RenameAsync(row.Id, newTitle);
        }
        else
        {
            row.CancelRename();
        }
    }

    /// <summary>When the inline TextBox appears (either via double-tap
    /// or auto-rename on a fresh session), focus it and select all
    /// the current text so the user can just start typing.</summary>
    private void OnSessionRenameAttached(object? sender, VisualTreeAttachmentEventArgs e)
    {
        if (sender is not TextBox tb) return;
        Dispatcher.UIThread.Post(() =>
        {
            tb.Focus();
            tb.SelectAll();
        });
    }

    private async void OnSessionRowDeleteClicked(object? sender, RoutedEventArgs e)
    {
        if (sender is not Button btn) return;
        if (btn.Tag is not string sessionId) return;
        if (DataContext is not SessionListViewModel rail) return;

        // Look up the title for the dialog.
        var item = rail.Sessions.FirstOrDefault(s => s.Id == sessionId);
        var title = item?.Title ?? sessionId;

        var owner = TopLevel.GetTopLevel(this) as Window;
        if (owner is null) return;

        var confirmed = await ShowConfirmDialog(owner, title);
        if (!confirmed) return;

        var result = await rail.DeleteHardAsync(sessionId);
        if (result is null)
        {
            await ShowToast(owner, "Delete failed",
                "Server didn't acknowledge the delete. Try again or " +
                "check the server logs.");
            return;
        }

        await ShowToast(owner, "Session deleted",
            $"Wiped {result.DeletedEventCount} event(s) from local storage.\n\n" +
            result.BscNote);
    }

    private static System.Threading.Tasks.Task<bool> ShowConfirmDialog(
        Window owner, string title)
    {
        var tcs = new System.Threading.Tasks.TaskCompletionSource<bool>();

        var dialog = new Window
        {
            Title = "Delete session?",
            Width = 460,
            Height = 240,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            CanResize = false,
            ShowInTaskbar = false,
        };

        var stack = new StackPanel { Margin = new Avalonia.Thickness(20), Spacing = 12 };
        stack.Children.Add(new TextBlock
        {
            Text = $"Delete \"{title}\"?",
            FontSize = 16,
            FontWeight = Avalonia.Media.FontWeight.SemiBold,
        });
        stack.Children.Add(new TextBlock
        {
            Text =
                "This wipes the session's messages from local storage and " +
                "drops any pending Greenfield writes for it. " +
                "BSC state-root anchors are immutable on chain — historic " +
                "anchors will remain but the agent will never surface this " +
                "session's content from any read path again.\n\n" +
                "This action cannot be undone.",
            FontSize = 12,
            TextWrapping = Avalonia.Media.TextWrapping.Wrap,
            Foreground = Avalonia.Media.Brushes.Gray,
        });

        var btns = new StackPanel
        {
            Orientation = Avalonia.Layout.Orientation.Horizontal,
            HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Right,
            Spacing = 8,
        };
        var cancelBtn = new Button { Content = "Cancel", Padding = new Avalonia.Thickness(14, 6) };
        var deleteBtn = new Button
        {
            Content = "Delete",
            Padding = new Avalonia.Thickness(14, 6),
            Background = Avalonia.Media.Brushes.IndianRed,
            Foreground = Avalonia.Media.Brushes.White,
        };
        cancelBtn.Click += (_, _) => { tcs.TrySetResult(false); dialog.Close(); };
        deleteBtn.Click += (_, _) => { tcs.TrySetResult(true); dialog.Close(); };
        btns.Children.Add(cancelBtn);
        btns.Children.Add(deleteBtn);
        stack.Children.Add(btns);

        dialog.Content = stack;
        dialog.Closed += (_, _) => tcs.TrySetResult(false);

        Dispatcher.UIThread.Post(async () =>
        {
            await dialog.ShowDialog(owner);
        });

        return tcs.Task;
    }

    private static System.Threading.Tasks.Task ShowToast(
        Window owner, string title, string body)
    {
        var dialog = new Window
        {
            Title = title,
            Width = 460,
            Height = 220,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            CanResize = false,
            ShowInTaskbar = false,
        };
        var stack = new StackPanel { Margin = new Avalonia.Thickness(20), Spacing = 12 };
        stack.Children.Add(new TextBlock
        {
            Text = title,
            FontSize = 15,
            FontWeight = Avalonia.Media.FontWeight.SemiBold,
        });
        stack.Children.Add(new TextBlock
        {
            Text = body,
            FontSize = 12,
            TextWrapping = Avalonia.Media.TextWrapping.Wrap,
            Foreground = Avalonia.Media.Brushes.Gray,
        });
        var ok = new Button
        {
            Content = "OK",
            Padding = new Avalonia.Thickness(14, 6),
            HorizontalAlignment = Avalonia.Layout.HorizontalAlignment.Right,
        };
        ok.Click += (_, _) => dialog.Close();
        stack.Children.Add(ok);
        dialog.Content = stack;
        return dialog.ShowDialog(owner);
    }
}
