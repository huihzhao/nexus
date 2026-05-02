// SPDX-License-Identifier: Apache-2.0
//
// StringToPointsConverter — Phase D 续 / #159 Brain panel Section 2.
//
// Avalonia 11's Polyline.Points is typed `IList<Point>`. The XAML
// parser converts a STRING LITERAL like "0,106 53.33,90 ..." to that
// list via the built-in PointsTypeConverter — but {Binding} does NOT
// invoke that converter, it just hands the raw string to the property
// setter, which silently fails (Points stays empty → polyline invisible).
//
// This converter bridges the gap: declare it as the binding's
// Converter and the same string format works through binding too.
//
// We compute the coordinates as a plain string in BrainPanelViewModel
// (rather than emitting an IList<Point> directly) to keep the VM
// free of Avalonia type dependencies — that way the VM is testable
// without Avalonia, and the View stays the only place that knows
// about Avalonia types.

using System;
using System.Collections.Generic;
using System.Globalization;
using Avalonia;
using Avalonia.Data.Converters;

namespace RuneDesktop.UI.Views;

public sealed class StringToPointsConverter : IValueConverter
{
    public static readonly StringToPointsConverter Instance = new();

    public object? Convert(object? value, Type targetType,
                           object? parameter, CultureInfo culture)
    {
        var s = value as string;
        if (string.IsNullOrEmpty(s)) return new List<Point>();

        var points = new List<Point>();
        // Format: "x1,y1 x2,y2 x3,y3 ..."  (space-separated pairs,
        // comma-separated coords, no trailing whitespace).
        var pairs = s.Split(' ', StringSplitOptions.RemoveEmptyEntries);
        foreach (var pair in pairs)
        {
            var coords = pair.Split(',');
            if (coords.Length != 2) continue;
            // Always parse with InvariantCulture — the VM emits
            // numbers with InvariantCulture too, but if a user's
            // locale uses comma decimals (de_DE / fr_FR), letting
            // CultureInfo.CurrentCulture in here would chew the
            // string into wrong points and silently crater the chart.
            if (double.TryParse(coords[0], NumberStyles.Float,
                                CultureInfo.InvariantCulture, out var x) &&
                double.TryParse(coords[1], NumberStyles.Float,
                                CultureInfo.InvariantCulture, out var y))
            {
                points.Add(new Point(x, y));
            }
        }
        return points;
    }

    public object? ConvertBack(object? value, Type targetType,
                               object? parameter, CultureInfo culture)
        => throw new NotSupportedException();
}
