// SPDX-License-Identifier: Apache-2.0
//
// Code-behind for WelcomeView. Empty by design — the wizard is fully
// MVVM-driven via WelcomeViewModel. This file exists only because
// Avalonia requires every .axaml to have a paired partial class.

using Avalonia.Controls;

namespace RuneDesktop.UI.Views;

public partial class WelcomeView : UserControl
{
    public WelcomeView()
    {
        InitializeComponent();
    }
}
