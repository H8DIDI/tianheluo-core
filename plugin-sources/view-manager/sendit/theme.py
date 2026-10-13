# -*- coding: utf-8 -*-
"""Light/dark theming for Sendit WPF windows.

Detects Revit's current UI theme (Revit 2024+ exposes UIThemeManager; older
versions fall back to light) and re-skins any WPF window -- XAML-built or
code-built -- with a matching colour palette plus a set of implicit control
styles, so tool windows always follow the Revit Dark/Light state.

Usage from a window class::

    from sendit import theme

    class MyWindow(forms.WPFWindow):
        def __init__(self, xaml):
            forms.WPFWindow.__init__(self, xaml, literal_string=True)
            theme.style_window(self)

Window XAML can reference palette brushes with {DynamicResource <Key>} and
the keyed button style {DynamicResource AccentButton}.
"""

from System.Windows.Markup import XamlReader
from System.Windows.Media import SolidColorBrush, Color, FontFamily


# Palette keys are referenced by the styles below and may be used directly
# in window XAML via {DynamicResource <Key>}.
PALETTES = {
    "light": {
        "WindowBg":       "#F3F3F3",
        "PanelBg":        "#FFFFFF",
        "Fg":             "#1B1B1B",
        "FgMuted":        "#6B6B6B",
        "Border":         "#C5C5C5",
        "GridLine":       "#E4E4E4",
        "InputBg":        "#FFFFFF",
        "ControlBg":      "#FDFDFD",
        "ControlHover":   "#E8F1FB",
        "ControlPressed": "#D8E7F7",
        "AccentBg":       "#2A6FBB",
        "AccentHover":    "#3C82CD",
        "AccentFg":       "#FFFFFF",
        "GridBg":         "#FFFFFF",
        "RowBg":          "#FFFFFF",
        "RowAltBg":       "#F6F8FA",
        "HeaderBg":       "#EFF1F4",
        "SelBg":          "#CBE2F7",
        "SelFg":          "#15212E",
    },
    "dark": {
        "WindowBg":       "#2B2B2B",
        "PanelBg":        "#333333",
        "Fg":             "#ECECEC",
        "FgMuted":        "#A0A0A0",
        "Border":         "#4A4A4A",
        "GridLine":       "#3D3D3D",
        "InputBg":        "#383838",
        "ControlBg":      "#3D3D3D",
        "ControlHover":   "#4A4F55",
        "ControlPressed": "#555B62",
        "AccentBg":       "#3E8EDE",
        "AccentHover":    "#5CA0E6",
        "AccentFg":       "#FFFFFF",
        "GridBg":         "#2F2F2F",
        "RowBg":          "#2F2F2F",
        "RowAltBg":       "#343434",
        "HeaderBg":       "#3A3A3A",
        "SelBg":          "#1C4A73",
        "SelFg":          "#FFFFFF",
    },
}


def current_theme():
    """Return 'dark' or 'light' to match Revit's UI theme."""
    try:
        from Autodesk.Revit.UI import UIThemeManager, UITheme
        if UIThemeManager.CurrentTheme == UITheme.Dark:
            return "dark"
    except Exception:
        pass  # pre-2024 Revit has no dark UI theme
    return "light"


def _brush(hexval):
    h = hexval.lstrip("#")
    br = SolidColorBrush(Color.FromRgb(
        int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))
    br.Freeze()
    return br


# Implicit styles applied to every themed window. Colours come from the
# palette brushes injected by style_window(), referenced as DynamicResource
# so a single style block serves both themes.
STYLE_XAML = """
<ResourceDictionary
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">

  <Style TargetType="{x:Type Button}">
    <Setter Property="Background" Value="{DynamicResource ControlBg}"/>
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="BorderBrush" Value="{DynamicResource Border}"/>
    <Setter Property="Padding" Value="12,5"/>
    <Setter Property="VerticalAlignment" Value="Center"/>
    <Setter Property="SnapsToDevicePixels" Value="True"/>
    <Setter Property="Template">
      <Setter.Value>
        <ControlTemplate TargetType="{x:Type Button}">
          <Border Background="{TemplateBinding Background}"
                  BorderBrush="{TemplateBinding BorderBrush}"
                  BorderThickness="1" CornerRadius="3"
                  SnapsToDevicePixels="True">
            <ContentPresenter Margin="{TemplateBinding Padding}"
                              HorizontalAlignment="Center"
                              VerticalAlignment="Center"
                              RecognizesAccessKey="True"/>
          </Border>
        </ControlTemplate>
      </Setter.Value>
    </Setter>
    <Style.Triggers>
      <Trigger Property="IsMouseOver" Value="True">
        <Setter Property="Background" Value="{DynamicResource ControlHover}"/>
      </Trigger>
      <Trigger Property="IsPressed" Value="True">
        <Setter Property="Background" Value="{DynamicResource ControlPressed}"/>
      </Trigger>
      <Trigger Property="IsEnabled" Value="False">
        <Setter Property="Opacity" Value="0.45"/>
      </Trigger>
    </Style.Triggers>
  </Style>

  <Style x:Key="AccentButton" TargetType="{x:Type Button}"
         BasedOn="{StaticResource {x:Type Button}}">
    <Setter Property="Background" Value="{DynamicResource AccentBg}"/>
    <Setter Property="Foreground" Value="{DynamicResource AccentFg}"/>
    <Setter Property="BorderBrush" Value="{DynamicResource AccentBg}"/>
    <Setter Property="FontWeight" Value="SemiBold"/>
    <Style.Triggers>
      <Trigger Property="IsMouseOver" Value="True">
        <Setter Property="Background" Value="{DynamicResource AccentHover}"/>
      </Trigger>
      <Trigger Property="IsPressed" Value="True">
        <Setter Property="Background" Value="{DynamicResource AccentHover}"/>
      </Trigger>
    </Style.Triggers>
  </Style>

  <Style TargetType="{x:Type TextBox}">
    <Setter Property="Background" Value="{DynamicResource InputBg}"/>
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="BorderBrush" Value="{DynamicResource Border}"/>
    <Setter Property="BorderThickness" Value="1"/>
    <Setter Property="Padding" Value="4,3"/>
    <Setter Property="CaretBrush" Value="{DynamicResource Fg}"/>
    <Setter Property="SelectionBrush" Value="{DynamicResource AccentBg}"/>
    <Setter Property="VerticalContentAlignment" Value="Center"/>
  </Style>

  <Style TargetType="{x:Type CheckBox}">
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="VerticalAlignment" Value="Center"/>
  </Style>

  <Style TargetType="{x:Type RadioButton}">
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="VerticalAlignment" Value="Center"/>
  </Style>

  <Style TargetType="{x:Type ListBox}">
    <Setter Property="Background" Value="{DynamicResource GridBg}"/>
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="BorderBrush" Value="{DynamicResource Border}"/>
    <Setter Property="BorderThickness" Value="1"/>
  </Style>

  <Style TargetType="{x:Type ListBoxItem}">
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="Padding" Value="8,4"/>
    <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
    <Setter Property="Template">
      <Setter.Value>
        <ControlTemplate TargetType="{x:Type ListBoxItem}">
          <Border x:Name="bd" Background="Transparent"
                  Padding="{TemplateBinding Padding}"
                  SnapsToDevicePixels="True">
            <ContentPresenter/>
          </Border>
          <ControlTemplate.Triggers>
            <Trigger Property="IsMouseOver" Value="True">
              <Setter TargetName="bd" Property="Background"
                      Value="{DynamicResource ControlHover}"/>
            </Trigger>
            <Trigger Property="IsSelected" Value="True">
              <Setter TargetName="bd" Property="Background"
                      Value="{DynamicResource SelBg}"/>
              <Setter Property="Foreground" Value="{DynamicResource SelFg}"/>
            </Trigger>
          </ControlTemplate.Triggers>
        </ControlTemplate>
      </Setter.Value>
    </Setter>
  </Style>

  <Style TargetType="{x:Type DataGrid}">
    <Setter Property="Background" Value="{DynamicResource GridBg}"/>
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="BorderBrush" Value="{DynamicResource Border}"/>
    <Setter Property="BorderThickness" Value="1"/>
    <Setter Property="RowBackground" Value="{DynamicResource RowBg}"/>
    <Setter Property="AlternatingRowBackground" Value="{DynamicResource RowAltBg}"/>
    <Setter Property="HorizontalGridLinesBrush" Value="{DynamicResource GridLine}"/>
    <Setter Property="VerticalGridLinesBrush" Value="{DynamicResource GridLine}"/>
    <Setter Property="GridLinesVisibility" Value="Horizontal"/>
    <Setter Property="HeadersVisibility" Value="Column"/>
    <Setter Property="RowHeaderWidth" Value="0"/>
    <Setter Property="CanUserAddRows" Value="False"/>
    <Setter Property="VerticalScrollBarVisibility" Value="Auto"/>
    <Setter Property="HorizontalScrollBarVisibility" Value="Auto"/>
  </Style>

  <Style TargetType="{x:Type DataGridColumnHeader}">
    <Setter Property="Background" Value="{DynamicResource HeaderBg}"/>
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="BorderBrush" Value="{DynamicResource GridLine}"/>
    <Setter Property="BorderThickness" Value="0,0,1,1"/>
    <Setter Property="Padding" Value="8,5"/>
    <Setter Property="FontWeight" Value="SemiBold"/>
  </Style>

  <Style TargetType="{x:Type DataGridRow}">
    <Style.Triggers>
      <Trigger Property="IsMouseOver" Value="True">
        <Setter Property="Background" Value="{DynamicResource ControlHover}"/>
      </Trigger>
      <Trigger Property="IsSelected" Value="True">
        <Setter Property="Background" Value="{DynamicResource SelBg}"/>
      </Trigger>
    </Style.Triggers>
  </Style>

  <Style TargetType="{x:Type DataGridCell}">
    <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
    <Setter Property="Background" Value="Transparent"/>
    <Setter Property="BorderThickness" Value="0"/>
    <Setter Property="Padding" Value="6,3"/>
    <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
    <Setter Property="Template">
      <Setter.Value>
        <ControlTemplate TargetType="{x:Type DataGridCell}">
          <Border Background="{TemplateBinding Background}"
                  BorderBrush="{TemplateBinding BorderBrush}"
                  BorderThickness="{TemplateBinding BorderThickness}"
                  Padding="{TemplateBinding Padding}"
                  SnapsToDevicePixels="True">
            <ContentPresenter VerticalAlignment="Center"/>
          </Border>
        </ControlTemplate>
      </Setter.Value>
    </Setter>
    <Style.Triggers>
      <Trigger Property="IsSelected" Value="True">
        <Setter Property="Background" Value="{DynamicResource SelBg}"/>
        <Setter Property="Foreground" Value="{DynamicResource SelFg}"/>
      </Trigger>
    </Style.Triggers>
  </Style>

</ResourceDictionary>
"""


def style_window(window, theme=None):
    """Apply the palette + implicit control styles to a WPF window.

    Args:
        window: any System.Windows.Window (incl. pyrevit forms.WPFWindow)
        theme (str, optional): force 'dark' or 'light'; default follows Revit

    Returns:
        str: the theme name that was applied
    """
    name = theme or current_theme()
    palette = PALETTES.get(name, PALETTES["light"])
    for key, val in palette.items():
        window.Resources[key] = _brush(val)
    window.Resources.MergedDictionaries.Add(XamlReader.Parse(STYLE_XAML))
    window.Background = window.Resources["WindowBg"]
    window.Foreground = window.Resources["Fg"]
    window.FontFamily = FontFamily("Segoe UI")
    window.FontSize = 12.0
    return name
