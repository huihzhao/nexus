// SPDX-License-Identifier: Apache-2.0
//
// TimelineHeightConverter — Phase D 续 / #159 Brain Panel support.
//
// Same idea as HistogramHeightConverter but scaled to the 60-pixel
// track used by the Brain Panel's 7-day learning timeline (versus
// 20px for the cognition pressure histogram). Bound from
// TimelineDayViewModel.HeightRatio (which the parent VM normalises
// against the max-total across the 7-day window).

using System;
using System.Globalization;
using Avalonia.Data.Converters;

namespace RuneDesktop.UI.Views;

public sealed class TimelineHeightConverter : IValueConverter
{
    public static readonly TimelineHeightConverter Instance = new();

    /// <summary>Visible minimum so a 1-item day isn't invisible
    /// next to a 50-item day.</summary>
    private const double MinVisibleHeight = 3.0;

    /// <summary>Total bar track height in pixels — must match the
    /// outer Border ``Height="60"`` in ChatView.axaml's Brain Panel
    /// timeline section.</summary>
    private const double TrackHeight = 60.0;

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
        return Math.Max(MinVisibleHeight, px);
    }

    public object? ConvertBack(object? value, Type targetType,
                               object? parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
