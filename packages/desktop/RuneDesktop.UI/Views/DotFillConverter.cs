// SPDX-License-Identifier: Apache-2.0
//
// DotFillConverter — Phase A4 persistence indicator support.
//
// Each thinking step renders four small Ellipses in a row:
// queued → EventLog → Greenfield → BSC anchor. Each dot has its
// own colour palette (lit vs unlit) so a glance reveals how far
// along the audit trail this step has propagated.
//
// One static converter per phase keeps the colour mapping in one
// place and lets XAML stay declarative ({x:Static
// local:DotFillConverter.Greenfield}).

using System;
using System.Globalization;
using Avalonia.Data.Converters;
using Avalonia.Media;

namespace RuneDesktop.UI.Views;

public sealed class DotFillConverter : IValueConverter
{
    private readonly IBrush _on;
    private readonly IBrush _off;

    private DotFillConverter(IBrush on, IBrush off)
    {
        _on = on;
        _off = off;
    }

    public object? Convert(object? value, Type targetType,
                           object? parameter, CultureInfo culture)
    {
        var lit = value is bool b && b;
        return lit ? _on : _off;
    }

    public object? ConvertBack(object? value, Type targetType,
                               object? parameter, CultureInfo culture)
        => throw new NotSupportedException();

    /// <summary>"Just emitted" phase. Blue-grey when lit — every
    /// step starts here, so it's the most "neutral" of the four.
    /// Off-state stays the same dark grey as the others — visually
    /// you only ever see "off" for an unreached phase.</summary>
    public static readonly DotFillConverter Queued =
        new(SolidColorBrush.Parse("#5AAEFF"),
            SolidColorBrush.Parse("#3A4350"));

    /// <summary>EventLog committed (server SQLite). Lit green —
    /// "data is local-durable now, it'd survive a process kill".</summary>
    public static readonly DotFillConverter EventLog =
        new(SolidColorBrush.Parse("#7DBC68"),
            SolidColorBrush.Parse("#3A4350"));

    /// <summary>Greenfield mirrored (gnfd:// PUT acknowledged).
    /// Amber when lit — bytes have left the server's disk and live
    /// on the user's bucket.</summary>
    public static readonly DotFillConverter Greenfield =
        new(SolidColorBrush.Parse("#E5B45A"),
            SolidColorBrush.Parse("#3A4350"));

    /// <summary>BSC anchor included this row's hash. Brand gold —
    /// "this step is now provably part of the agent's history,
    /// verifiable by a third party with the canonical bytes".
    /// This is the dot the user clicks for the BSCscan link.</summary>
    public static readonly DotFillConverter BscAnchor =
        new(SolidColorBrush.Parse("#F0B90B"),
            SolidColorBrush.Parse("#3A4350"));
}
