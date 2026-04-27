using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RuneDesktop.Core.Services;

namespace RuneDesktop.UI.ViewModels;

/// <summary>
/// One row in the sidebar's Activity Stream. Every "thing the agent did
/// recently" maps to one of these — chat turn, attachment distillation,
/// memory snapshot, anchor lifecycle. Visual styling (icon + colour) is
/// computed from the server's <c>kind</c> field so the panel stays in
/// sync as we add new event types.
/// </summary>
public partial class ActivityItemViewModel : ObservableObject
{
    public ActivityItem Source { get; }

    public string Kind => Source.Kind;
    public string Summary => Source.Summary;
    public string RelativeTime => FormatRelative(Source.Timestamp);
    public string Icon { get; }
    public string IconBg { get; }
    public string IconFg { get; }

    public ActivityItemViewModel(ActivityItem source)
    {
        Source = source;
        (Icon, IconBg, IconFg) = StyleFor(source.Kind);
    }

    private static (string icon, string bg, string fg) StyleFor(string kind) =>
        kind switch
        {
            "chat.user"                 => ("U",  "#3A4350", "#E8E6DC"),
            "chat.assistant"            => ("R",  "#F0B90B", "#1F2329"),
            "file.attached"             => ("📎", "#3A4350", "#E8E6DC"),
            "file.distilled"            => ("💎", "#2E5066", "#9DCAE7"),
            "memory.compact"            => ("🧠", "#3D2E66", "#C4A6E8"),
            "anchor.anchored"           => ("✓",  "#2E5C3A", "#7DBC68"),
            "anchor.pending"            => ("⏳", "#3A4350", "#9CA3AF"),
            "anchor.failed"             => ("↻",  "#5C4A2E", "#E5B45A"),
            "anchor.failed_permanent"   => ("✕",  "#5C2E2E", "#E36B6B"),
            "anchor.awaiting_registration" => ("⌛", "#5C4A2E", "#E5B45A"),
            "anchor.stored_only"        => ("◐",  "#5C4A2E", "#E5B45A"),
            _                           => ("•",  "#3A4350", "#9CA3AF"),
        };

    private static string FormatRelative(string isoTimestamp)
    {
        if (string.IsNullOrEmpty(isoTimestamp)) return "";
        if (!DateTime.TryParse(isoTimestamp, null,
            System.Globalization.DateTimeStyles.RoundtripKind, out var t))
            return "";
        var diff = DateTime.UtcNow - t.ToUniversalTime();
        if (diff.TotalSeconds < 60) return "just now";
        if (diff.TotalMinutes < 60) return $"{(int)diff.TotalMinutes}m ago";
        if (diff.TotalHours < 24) return $"{(int)diff.TotalHours}h ago";
        return t.ToLocalTime().ToString("MMM d, HH:mm");
    }
}

/// <summary>
/// Polls /api/v1/agent/timeline every few seconds and exposes the result
/// as an <c>ObservableCollection</c>. Sidebar binds directly. Caller is
/// responsible for calling <see cref="Start"/> after login + <see
/// cref="Stop"/> on logout / disposal.
/// </summary>
public partial class ActivityStreamViewModel : ObservableObject
{
    private readonly ApiClient _api;
    private Timer? _pollTimer;
    private static readonly TimeSpan _interval = TimeSpan.FromSeconds(5);

    public ObservableCollection<ActivityItemViewModel> Items { get; } = new();

    [ObservableProperty] private bool _isEmpty = true;

    public ActivityStreamViewModel(ApiClient api)
    {
        _api = api;
        Items.CollectionChanged += (_, _) => IsEmpty = Items.Count == 0;
    }

    public void Start()
    {
        Stop();
        // Kick a refresh now, then on a timer.
        _ = RefreshAsync();
        _pollTimer = new Timer(
            _ => _ = RefreshAsync(),
            null, _interval, _interval);
    }

    public void Stop()
    {
        _pollTimer?.Dispose();
        _pollTimer = null;
    }

    public async Task RefreshAsync()
    {
        try
        {
            var fresh = await _api.GetTimelineAsync(limit: 60);
            // Replace contents — the server already returned newest first.
            // Cheap diff: if the top item's (kind, sync_id, anchor_id) hasn't
            // changed since last refresh, skip the rebuild to avoid flicker.
            if (fresh.Count > 0 && Items.Count > 0)
            {
                var top = Items[0];
                if (top.Source.Kind == fresh[0].Kind &&
                    top.Source.SyncId == fresh[0].SyncId &&
                    top.Source.AnchorId == fresh[0].AnchorId &&
                    top.Source.Summary == fresh[0].Summary)
                {
                    return;
                }
            }
            Avalonia.Threading.Dispatcher.UIThread.Post(() =>
            {
                Items.Clear();
                foreach (var it in fresh)
                    Items.Add(new ActivityItemViewModel(it));
            });
        }
        catch
        {
            // Polling never throws into the dispatcher.
        }
    }

    [RelayCommand]
    private Task RefreshNow() => RefreshAsync();
}
