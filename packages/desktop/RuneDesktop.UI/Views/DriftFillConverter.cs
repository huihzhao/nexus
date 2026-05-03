// SPDX-License-Identifier: Apache-2.0
//
// DriftFillConverter — picks a brush for the "anchored" namespace dot
// based on whether the namespace has drifted past its last anchor.
//
// Used by the namespace cards in ChatView.axaml: each card has three
// status dots (local / mirrored / anchored). The third dot used to
// always render green when DotAnchored was true. With this converter,
// it renders green when fully synced and amber when commits have
// outpaced the last anchor by more than ~1h (per
// NamespaceGlanceViewModel.IsDrifted).
//
// Without this, "anchored 2 hours ago, never re-anchored since" looked
// identical to "anchored 30 seconds ago" — the silent-drift twin to
// the fallback-active bug.

using System;
using System.Globalization;
using Avalonia.Data.Converters;
using Avalonia.Media;

namespace RuneDesktop.UI.Views;

public sealed class DriftFillConverter : IValueConverter
{
    public static readonly DriftFillConverter Instance = new();

    // Same hex values as App.axaml's SuccessBrush / WarningBrush.
    private static readonly IBrush Healthy = SolidColorBrush.Parse("#3FB950");
    private static readonly IBrush Drifted = SolidColorBrush.Parse("#D29922");

    public object? Convert(object? value, Type targetType,
                           object? parameter, CultureInfo culture)
    {
        var drifted = value is bool b && b;
        // ``ConverterParameter="invert"`` flips the polarity so the
        // SAME converter can render the chat-input sync strip's dot,
        // where the input bool is GreenfieldReady (true = healthy)
        // rather than IsDrifted (true = degraded). Keeps us from
        // proliferating one-line bool-to-brush converters.
        if (parameter is string s && s.Equals("invert", StringComparison.OrdinalIgnoreCase))
            drifted = !drifted;
        return drifted ? Drifted : Healthy;
    }

    public object? ConvertBack(object? value, Type targetType,
                               object? parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
