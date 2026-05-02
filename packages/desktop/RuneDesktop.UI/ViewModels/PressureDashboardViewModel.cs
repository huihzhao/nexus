// SPDX-License-Identifier: Apache-2.0
//
// PressureDashboardViewModel — Phase C3
//
// Renders three views over the GET /agent/evolution/pressure response:
//
//   1. **Gauges** — one progress bar per evolver. Layer drives colour
//      (L0 gold, L1 blue, L2 green, L4 grey). 90%+ flashes ⏳.
//   2. **Lineage** — fed_by relationships expressed as small arrows
//      between rows ("KnowledgeCompiler ← MemoryEvolver"). The
//      lineage detail card pops up on click and reads triggered_by
//      from the matching evolution_proposal in the timeline.
//   3. **Histogram** — 24h sparkline per evolver, one bucket per
//      hour. Visualises the AHE pyramid: L0 sparse, L1 busy.
//
// Polled every 5s by CognitionPanelViewModel (cheaper than the 2s
// thinking stream because pressure changes slowly).

using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Threading.Tasks;
using CommunityToolkit.Mvvm.ComponentModel;
using RuneDesktop.Core.Services;

namespace RuneDesktop.UI.ViewModels;

/// <summary>One row in the gauges section. Self-contained: every
/// XAML binding the gauge template needs is a property here.</summary>
public partial class PressureGaugeViewModel : ObservableObject
{
    public EvolutionPressureItem Source { get; }

    public PressureGaugeViewModel(EvolutionPressureItem source)
    {
        Source = source;
    }

    public string Evolver => Source.Evolver;
    public string Layer => Source.Layer;
    public string Unit => Source.Unit;
    public string Status => Source.Status;
    public IReadOnlyList<string> FedBy => Source.FedBy;

    /// <summary>Display-friendly name with layer prefix ("L0 ·
    /// PersonaEvolver"). Sorted by layer in the parent so the gauges
    /// stack in pyramid order top-to-bottom.</summary>
    public string DisplayName => $"{Layer} · {Evolver}";

    /// <summary>Fill ratio in [0, 1]. ``live`` evolvers always
    /// render 100% (they fire every tick — a flat line, not a
    /// progress bar). Threshold == infinity (Python ``inf``)
    /// arrives as ``double.PositiveInfinity`` after JSON parse.</summary>
    public double FillRatio
    {
        get
        {
            if (string.Equals(Status, "live", StringComparison.OrdinalIgnoreCase))
                return 1.0;
            if (Source.Threshold <= 0 || double.IsInfinity(Source.Threshold))
                return string.Equals(Status, "ready", StringComparison.OrdinalIgnoreCase)
                    ? 1.0 : 0.0;
            var r = Source.Accumulator / Source.Threshold;
            return Math.Max(0.0, Math.Min(1.0, r));
        }
    }

    /// <summary>Percentage label e.g. "47%" — "live" for live
    /// evolvers, "ready" for primed ones. Saves the XAML having
    /// to format multi-state.</summary>
    public string FillLabel
    {
        get
        {
            if (string.Equals(Status, "live", StringComparison.OrdinalIgnoreCase))
                return "live";
            if (string.Equals(Status, "ready", StringComparison.OrdinalIgnoreCase))
                return "ready ⏳";
            if (string.Equals(Status, "fired_recently", StringComparison.OrdinalIgnoreCase))
                return "just fired ✓";
            if (double.IsInfinity(Source.Threshold)) return Status;
            return $"{(int)Math.Round(FillRatio * 100)}%";
        }
    }

    /// <summary>Accumulator / threshold ratio printed for the
    /// hover tooltip. "8 / 30 days" makes the units explicit.</summary>
    public string GaugeDetail
    {
        get
        {
            if (string.Equals(Status, "live", StringComparison.OrdinalIgnoreCase))
                return $"runs every {Unit}";
            if (double.IsInfinity(Source.Threshold))
                return $"{Source.Accumulator:F0} {Unit}";
            return $"{Source.Accumulator:F1} / {Source.Threshold:F1} {Unit}";
        }
    }

    /// <summary>Layer-driven brush key — XAML uses this in a
    /// {DynamicResource} binding to colour the bar accordingly.
    /// L0 gold, L1 blue, L2 green, L4 grey, anything else neutral.</summary>
    public string LayerColor => Layer switch
    {
        "L0" => "#F0B90B",   // gold (BrandAccent)
        "L1" => "#7DBC68",   // green
        "L2" => "#5AAEFF",   // blue
        "L4" => "#9CA3AF",   // grey
        _    => "#9CA3AF",
    };

    /// <summary>fed_by rendered as a single string for the lineage
    /// arrow row: "← MemoryEvolver, KnowledgeCompiler".</summary>
    public string FedByLabel => FedBy.Count == 0
        ? ""
        : "← " + string.Join(", ", FedBy);

    /// <summary>True when the gauge is at 90%+ AND not in live mode —
    /// triggers the ⏳ pulse animation in XAML.</summary>
    public bool IsAboutToFire =>
        !string.Equals(Status, "live", StringComparison.OrdinalIgnoreCase)
        && FillRatio >= 0.9
        && !string.Equals(Status, "fired_recently", StringComparison.OrdinalIgnoreCase);
}


/// <summary>One bar in the 24h frequency histogram.</summary>
public partial class HistogramBarViewModel : ObservableObject
{
    public int HourIndex { get; }    // 0 = 24h ago, 23 = now
    public int Count { get; }

    public HistogramBarViewModel(int hourIndex, int count)
    {
        HourIndex = hourIndex;
        Count = count;
    }

    /// <summary>Bar height as a fraction of the row's max — 0..1.
    /// The parent row computes the divisor (max across 24 buckets)
    /// because individual bars don't know peer values.</summary>
    public double Height { get; set; }

    public string Tooltip => Count switch
    {
        0 => $"-{24 - HourIndex}h: no firings",
        1 => $"-{24 - HourIndex}h: 1 firing",
        _ => $"-{24 - HourIndex}h: {Count} firings",
    };
}


/// <summary>One row of the histogram — an evolver + its 24 buckets.
/// Colour matches the gauge for visual consistency.</summary>
public partial class HistogramRowViewModel : ObservableObject
{
    public string Evolver { get; }
    public string LayerColor { get; }
    public ObservableCollection<HistogramBarViewModel> Bars { get; } = new();
    public int TotalIn24h { get; }

    public HistogramRowViewModel(
        string evolver, string layerColor, IReadOnlyList<int> hourly,
        int max)
    {
        Evolver = evolver;
        LayerColor = layerColor;
        TotalIn24h = hourly.Sum();
        for (var i = 0; i < hourly.Count; i++)
        {
            var bar = new HistogramBarViewModel(i, hourly[i])
            {
                Height = max > 0 ? (double)hourly[i] / max : 0.0,
            };
            Bars.Add(bar);
        }
    }
}


/// <summary>Top-level Pressure Dashboard VM. Owned by
/// CognitionPanelViewModel; refreshed alongside the 2s polled
/// streams (its own 5s cadence is enforced inside RefreshAsync —
/// throttled by ``_lastFetch``).</summary>
public partial class PressureDashboardViewModel : ObservableObject
{
    private readonly ApiClient _api;
    private DateTime _lastFetch = DateTime.MinValue;
    // 5s minimum between fetches so the 2s cognition poll doesn't
    // hammer the server. Pressure changes slowly — this is plenty.
    private static readonly TimeSpan _minInterval = TimeSpan.FromSeconds(5);

    public ObservableCollection<PressureGaugeViewModel> Gauges { get; } = new();
    public ObservableCollection<HistogramRowViewModel> Histogram { get; } = new();
    /// <summary>Phase D 续 / #159: recent verdict feed shown below
    /// the histogram. Newest first; 10 rows max from the server.</summary>
    public ObservableCollection<VerdictFeedItemViewModel> Verdicts { get; } = new();

    [ObservableProperty] private bool _isEmpty = true;
    [ObservableProperty] private string _lastUpdated = "";

    public PressureDashboardViewModel(ApiClient api)
    {
        _api = api;
        Gauges.CollectionChanged += (_, _) =>
            IsEmpty = Gauges.Count == 0;
    }

    /// <summary>Fetch + apply. Throttled to 5s so callers can poll
    /// at any cadence without flooding the endpoint. Best-effort —
    /// network blips leave the existing dashboard data in place.</summary>
    public async Task RefreshAsync(bool force = false)
    {
        if (!force && DateTime.UtcNow - _lastFetch < _minInterval) return;
        _lastFetch = DateTime.UtcNow;

        EvolutionPressureResponse? data = null;
        try { data = await _api.GetEvolutionPressureAsync(); }
        catch { return; }
        if (data is null) return;

        Avalonia.Threading.Dispatcher.UIThread.Post(() =>
        {
            ApplyGauges(data.Evolvers);
            ApplyHistogram(data.Histogram24h);
            ApplyVerdicts(data.RecentVerdicts);
            LastUpdated = DateTime.Now.ToString("HH:mm:ss");
        });
    }

    private void ApplyVerdicts(List<EvolutionVerdictItem> items)
    {
        Verdicts.Clear();
        foreach (var v in items)
            Verdicts.Add(new VerdictFeedItemViewModel(v));
    }

    private void ApplyGauges(List<EvolutionPressureItem> items)
    {
        Gauges.Clear();
        // Sort by layer so the pyramid reads top-to-bottom: L0
        // (apex, slowest) → L4 (base, fastest).
        var sorted = items
            .OrderBy(i => LayerOrder(i.Layer))
            .ThenBy(i => i.Evolver)
            .ToList();
        foreach (var item in sorted)
            Gauges.Add(new PressureGaugeViewModel(item));
    }

    private void ApplyHistogram(Dictionary<string, List<int>> data)
    {
        Histogram.Clear();
        // Compute global max so all rows scale to the same axis —
        // makes the pyramid shape jump out (L0's lone bar vs L1's
        // saturated row).
        var globalMax = data.Values
            .SelectMany(v => v)
            .DefaultIfEmpty(0)
            .Max();
        if (globalMax <= 0) globalMax = 1;
        // Same layer order as the gauges so visually they line up.
        var ordered = data
            .OrderBy(kv => LayerOrderForEvolver(kv.Key))
            .ThenBy(kv => kv.Key);
        foreach (var (evolver, hourly) in ordered)
        {
            // Pad to 24 buckets if the server returned fewer (defensive).
            var padded = hourly.Count == 24 ? hourly :
                hourly.Concat(Enumerable.Repeat(0, 24 - hourly.Count)).Take(24).ToList();
            Histogram.Add(new HistogramRowViewModel(
                evolver,
                LayerColorForEvolver(evolver),
                padded,
                globalMax));
        }
    }

    /// <summary>Display order: L0 first (apex), L1, L2, L4, others.</summary>
    private static int LayerOrder(string layer) => layer switch
    {
        "L0" => 0,
        "L1" => 1,
        "L2" => 2,
        "L4" => 4,
        _ => 9,
    };

    /// <summary>Histogram-only fallback when we don't have the
    /// matching gauge data (e.g. a verdict happened for an evolver
    /// not currently in pressure_state). Hard-coded since the set
    /// of evolvers is bounded.</summary>
    private static int LayerOrderForEvolver(string evolver) => evolver switch
    {
        "PersonaEvolver" => 0,
        "MemoryEvolver" or "EventLogCompactor" or "KnowledgeCompiler" => 1,
        "SkillEvolver" => 2,
        "ChainBackend" => 4,
        _ => 9,
    };

    private static string LayerColorForEvolver(string evolver) => evolver switch
    {
        "PersonaEvolver" => "#F0B90B",
        "MemoryEvolver" or "EventLogCompactor" or "KnowledgeCompiler" => "#7DBC68",
        "SkillEvolver" => "#5AAEFF",
        "ChainBackend" => "#9CA3AF",
        _ => "#9CA3AF",
    };
}


/// <summary>One row in the Pressure Dashboard's verdict feed
/// (Phase D 续 / #159). Shows what was decided and why.</summary>
public partial class VerdictFeedItemViewModel : ObservableObject
{
    public EvolutionVerdictItem Source { get; }

    public VerdictFeedItemViewModel(EvolutionVerdictItem source)
    {
        Source = source;
    }

    public string EditId => Source.EditId;
    public string Evolver => Source.Evolver;
    public string TargetNamespace => Source.TargetNamespace;
    public string Decision => Source.Decision;
    public string ChangeSummary => Source.ChangeSummary;
    public string Evidence => Source.Evidence;
    public double Timestamp => Source.Timestamp;

    /// <summary>"KEPT" / "REVERTED" / "?" — uppercase chip text.</summary>
    public string DecisionBadge => Decision switch
    {
        "kept" => "KEPT",
        "reverted" => "REVERTED",
        _ => "PENDING",
    };

    /// <summary>Hex colour for the decision chip.</summary>
    public string DecisionColor => Decision switch
    {
        "kept" => "#0F6E56",
        "reverted" => "#A32D2D",
        _ => "#888780",
    };

    public string RelativeTime
    {
        get
        {
            if (Timestamp <= 0) return "";
            var when = DateTimeOffset.FromUnixTimeSeconds((long)Timestamp);
            var elapsed = DateTimeOffset.UtcNow - when;
            if (elapsed.TotalMinutes < 1) return "now";
            if (elapsed.TotalMinutes < 60) return $"{(int)elapsed.TotalMinutes}m ago";
            if (elapsed.TotalHours < 24) return $"{(int)elapsed.TotalHours}h ago";
            return $"{(int)elapsed.TotalDays}d ago";
        }
    }

    /// <summary>Reasoning blurb: "regression=0.30, drift=0.15"
    /// → human-friendlier "regression: 30% · drift: +15%". Short
    /// enough to fit a single line in the feed.</summary>
    public string ReasoningLabel
    {
        get
        {
            var parts = new List<string>();
            if (Source.RegressionScore > 0)
                parts.Add($"regression: {Source.RegressionScore * 100:F0}%");
            if (Math.Abs(Source.AbcDriftDelta) > 0.001)
                parts.Add($"drift: {Source.AbcDriftDelta:+0.00;-0.00;0.00}");
            if (parts.Count == 0 && !string.IsNullOrEmpty(Source.Evidence))
                return Source.Evidence;
            return string.Join(" · ", parts);
        }
    }
}
