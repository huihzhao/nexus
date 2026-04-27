using System;
using System.Collections.ObjectModel;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using RuneDesktop.Core.Services;

namespace RuneDesktop.UI.ViewModels;

/// <summary>
/// Wraps a <see cref="MemoryEntry"/> for the slide-over Memories panel.
/// Adds presentation helpers (relative time, byte size formatting).
/// </summary>
public partial class MemoryItemViewModel : ObservableObject
{
    public MemoryEntry Source { get; }

    public string Content => Source.Content;
    public int EventCount => Source.EventCount;
    public string SizeLabel =>
        Source.CharCount switch
        {
            < 1024 => $"{Source.CharCount} chars",
            _      => $"{Source.CharCount / 1024.0:0.#} KB",
        };
    public string RelativeTime => RelativeTimeFormatter.Format(Source.CreatedAt);
    public string Range =>
        Source.FirstSyncId is long f && Source.LastSyncId is long l
            ? $"events #{f}–#{l}"
            : "";

    public MemoryItemViewModel(MemoryEntry source) { Source = source; }
}

/// <summary>
/// Wraps a <see cref="SyncAnchorEntry"/> for the slide-over Anchors panel.
/// </summary>
public partial class AnchorItemViewModel : ObservableObject
{
    public SyncAnchorEntry Source { get; }

    public string Status => Source.Status;
    public string ShortHash => Source.ShortHash;
    public string ShortTx => Source.ShortTx;
    public int EventCount => Source.EventCount;
    public string Range => $"events #{Source.FirstSyncId}–#{Source.LastSyncId}";
    public string RelativeTime => RelativeTimeFormatter.Format(Source.UpdatedAt);
    public string StatusColor => Status switch
    {
        "anchored"              => "#7DBC68",
        "pending"               => "#9CA3AF",
        "failed"                => "#E5B45A",
        "failed_permanent"      => "#E36B6B",
        "awaiting_registration" => "#E5B45A",
        "stored_only"           => "#E5B45A",
        _                       => "#9CA3AF",
    };
    public string BscScanUrl =>
        string.IsNullOrEmpty(Source.BscTxHash)
            ? ""
            : $"https://testnet.bscscan.com/tx/{Source.BscTxHash}";
    public bool HasBscTx => !string.IsNullOrEmpty(Source.BscTxHash);

    public AnchorItemViewModel(SyncAnchorEntry source) { Source = source; }
}

/// <summary>
/// Backing model for the slide-over panel. The View binds Visible/Mode/
/// the items collections; opening / closing is driven via <see
/// cref="OpenMemoriesAsync"/> / <see cref="OpenAnchorsAsync"/> /
/// <see cref="CloseCommand"/>.
/// </summary>
public partial class DetailPanelViewModel : ObservableObject
{
    private readonly ApiClient _api;

    public enum PanelMode { None, Memories, Anchors }

    [ObservableProperty] private PanelMode _mode = PanelMode.None;
    [ObservableProperty] private bool _isOpen;
    [ObservableProperty] private bool _isLoading;
    [ObservableProperty] private string _title = "";

    /// <summary>
    /// Latest memory snapshot — the agent's "current brain" view.
    /// Each subsequent compaction folds prior snapshots in (see
    /// memory_service._load_window_events: it includes earlier
    /// memory_compact events as input), so the newest row is the
    /// merged current state. Older entries live in <see
    /// cref="HistoryMemories"/> as an immutable audit trail.
    /// </summary>
    [ObservableProperty] private MemoryItemViewModel? _currentBrain;

    /// <summary>Older snapshots, newest first, for the History section.</summary>
    public ObservableCollection<MemoryItemViewModel> HistoryMemories { get; } = new();

    public ObservableCollection<AnchorItemViewModel> Anchors { get; } = new();

    public bool ShowMemories => Mode == PanelMode.Memories;
    public bool ShowAnchors  => Mode == PanelMode.Anchors;
    public bool HasCurrentBrain => CurrentBrain is not null;
    public bool HasHistory      => HistoryMemories.Count > 0;
    public bool MemoriesEmpty   => CurrentBrain is null && HistoryMemories.Count == 0;
    public bool AnchorsEmpty    => Anchors.Count == 0;

    partial void OnModeChanged(PanelMode value)
    {
        OnPropertyChanged(nameof(ShowMemories));
        OnPropertyChanged(nameof(ShowAnchors));
    }
    partial void OnCurrentBrainChanged(MemoryItemViewModel? value)
    {
        OnPropertyChanged(nameof(HasCurrentBrain));
        OnPropertyChanged(nameof(MemoriesEmpty));
    }

    public DetailPanelViewModel(ApiClient api) { _api = api; }

    public async Task OpenMemoriesAsync()
    {
        Mode = PanelMode.Memories;
        Title = "Memory Snapshots";
        IsOpen = true;
        IsLoading = true;
        try
        {
            var fresh = await _api.GetMemoriesAsync(limit: 80);
            Avalonia.Threading.Dispatcher.UIThread.Post(() =>
            {
                // Newest-first: index 0 is "current brain", rest is history.
                HistoryMemories.Clear();
                if (fresh.Count == 0)
                {
                    CurrentBrain = null;
                }
                else
                {
                    CurrentBrain = new MemoryItemViewModel(fresh[0]);
                    for (int i = 1; i < fresh.Count; i++)
                        HistoryMemories.Add(new MemoryItemViewModel(fresh[i]));
                }
                OnPropertyChanged(nameof(HasHistory));
                OnPropertyChanged(nameof(MemoriesEmpty));
            });
        }
        finally { IsLoading = false; }
    }

    public async Task OpenAnchorsAsync()
    {
        Mode = PanelMode.Anchors;
        Title = "On-chain Anchors";
        IsOpen = true;
        IsLoading = true;
        try
        {
            var fresh = await _api.GetSyncAnchorsAsync(limit: 80);
            Avalonia.Threading.Dispatcher.UIThread.Post(() =>
            {
                Anchors.Clear();
                foreach (var a in fresh) Anchors.Add(new AnchorItemViewModel(a));
                OnPropertyChanged(nameof(AnchorsEmpty));
            });
        }
        finally { IsLoading = false; }
    }

    [RelayCommand]
    private void Close()
    {
        IsOpen = false;
        Mode = PanelMode.None;
    }
}

/// <summary>Shared relative-time helper used by item view models.</summary>
internal static class RelativeTimeFormatter
{
    public static string Format(string isoTimestamp)
    {
        if (string.IsNullOrEmpty(isoTimestamp)) return "";
        if (!DateTime.TryParse(isoTimestamp, null,
            System.Globalization.DateTimeStyles.RoundtripKind, out var t))
            return "";
        var diff = DateTime.UtcNow - t.ToUniversalTime();
        if (diff.TotalSeconds < 60) return "just now";
        if (diff.TotalMinutes < 60) return $"{(int)diff.TotalMinutes}m ago";
        if (diff.TotalHours < 24)   return $"{(int)diff.TotalHours}h ago";
        return t.ToLocalTime().ToString("MMM d, HH:mm");
    }
}
