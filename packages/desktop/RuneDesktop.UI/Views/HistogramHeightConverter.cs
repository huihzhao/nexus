// SPDX-License-Identifier: Apache-2.0
//
// HistogramHeightConverter — Phase C3 Pressure Dashboard support.
//
// Each bar in the 24h frequency histogram (CognitionPanelViewModel
// → Pressure → Histogram) carries a normalised ``Height`` in [0, 1]
// (computed in HistogramRowViewModel relative to the global max so
// rows are visually comparable). XAML binds the bar's Border.Height
// to that value through this converter, which scales it into actual
// pixel heights.
//
// Why a converter and not a direct binding: ``Height`` is a fraction
// but XAML wants pixels, and we want to clamp tiny non-zero values
// to a visible minimum (otherwise a 1-firing bucket disappears
// against a row where another evolver fired 50 times).

using System;
using System.Globalization;
using Avalonia.Data.Converters;

namespace RuneDesktop.UI.Views;

public sealed class HistogramHeightConverter : IValueConverter
{
    public static readonly HistogramHeightConverter Instance = new();

    /// <summary>Visible minimum so a single firing isn't invisible
    /// next to an evolver that fired 50 times.</summary>
    private const double MinVisibleHeight = 2.0;

    /// <summary>Total bar track height in pixels — must match the
    /// ItemsControl ``Height="20"`` in ChatView.axaml.</summary>
    private const double TrackHeight = 20.0;

    public object? Convert(object? value, Type targetType,
                           object? parameter, CultureInfo culture)
    {
        if (value is null) return 0.0;
        double ratio = value switch
        {
            double d => d,
            float f => f,
            int i => i,
            long l => l,
            _ => 0.0,
        };
        if (ratio <= 0.0) return 0.0;
        ratio = Math.Min(1.0, ratio);
        var px = ratio * TrackHeight;
        // Clamp to MinVisibleHeight only when the bucket has data
        // (ratio > 0). Empty buckets stay at 0 so the gaps in the
        // sparkline remain visible — that's the whole point of the
        // pyramid-shape visualization.
        return Math.Max(MinVisibleHeight, px);
    }

    public object? ConvertBack(object? value, Type targetType,
                               object? parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
