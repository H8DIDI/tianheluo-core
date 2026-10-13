# -*- coding: utf-8 -*-
"""Compact themed dialogs for Sendit.

Replaces chains of forms.ask_for_string popups with single-window forms that
match the Revit dark/light theme. All dialogs are modal and return None (or
False) when cancelled.

    ask_form(title, fields, ...)  -> dict of values or None
    ask_string(title, prompt,...) -> str or None
    pick_item(title, items, ...)  -> str or None
    message(title, text, ...)     -> None
    confirm(title, text, ...)     -> bool
"""

from System.Windows import (Window, WindowStartupLocation, SizeToContent,
                            ResizeMode, Thickness, TextWrapping,
                            HorizontalAlignment, Visibility)
from System.Windows.Controls import (StackPanel, DockPanel, Dock, TextBlock,
                                     TextBox, CheckBox, RadioButton, Button,
                                     ListBox, Orientation, ScrollViewer,
                                     ScrollBarVisibility)
from System.Windows.Media import SolidColorBrush, Color

from sendit import theme

_ERROR_BRUSH = SolidColorBrush(Color.FromRgb(0xD3, 0x4A, 0x4A))
_ERROR_BRUSH.Freeze()


def _make_window(title, owner, width=None):
    w = Window()
    w.Title = title
    w.ResizeMode = ResizeMode.NoResize
    w.SizeToContent = SizeToContent.WidthAndHeight
    w.ShowInTaskbar = False
    if owner is not None:
        try:
            w.Owner = owner
            w.WindowStartupLocation = WindowStartupLocation.CenterOwner
        except Exception:
            w.WindowStartupLocation = WindowStartupLocation.CenterScreen
    else:
        w.WindowStartupLocation = WindowStartupLocation.CenterScreen
    if width:
        w.Width = width
        w.SizeToContent = SizeToContent.Height
    theme.style_window(w)
    return w


def _muted_text(window, text, max_width=420):
    tb = TextBlock()
    tb.Text = text
    tb.TextWrapping = TextWrapping.Wrap
    tb.MaxWidth = max_width
    tb.Margin = Thickness(0, 0, 0, 10)
    tb.Foreground = window.Resources["FgMuted"]
    return tb


def _button_row(window, ok_label, cancel_label, on_ok):
    row = StackPanel()
    row.Orientation = Orientation.Horizontal
    row.HorizontalAlignment = HorizontalAlignment.Right
    row.Margin = Thickness(0, 14, 0, 0)

    if cancel_label:
        cancel = Button()
        cancel.Content = cancel_label
        cancel.MinWidth = 80
        cancel.Margin = Thickness(0, 0, 8, 0)
        cancel.IsCancel = True
        row.Children.Add(cancel)

    ok = Button()
    ok.Content = ok_label
    ok.MinWidth = 80
    ok.IsDefault = True
    ok.Style = window.Resources["AccentButton"]
    ok.Click += on_ok
    row.Children.Add(ok)
    return row


def ask_form(title, fields, owner=None, message=None, ok_label="OK",
             cancel_label="Cancel"):
    """Show a single themed form built from field specs.

    Each field is a dict:
        key      -- result-dict key (required)
        label    -- caption shown above/next to the input (required)
        type     -- 'text' (default) | 'int' | 'check' | 'choice'
        default  -- initial value
        choices  -- list of strings (type 'choice' only, shown as radios)
        hint     -- optional muted helper line under the input

    Returns:
        dict of key->value (str/int/bool) or None if cancelled.
    """
    w = _make_window(title, owner)
    root = StackPanel()
    root.Margin = Thickness(16)
    root.MinWidth = 340
    if message:
        root.Children.Add(_muted_text(w, message))

    editors = {}  # key -> (kind, control(s))
    for spec in fields:
        kind = spec.get("type", "text")
        key = spec["key"]
        if kind == "check":
            cb = CheckBox()
            cb.Content = spec["label"]
            cb.IsChecked = bool(spec.get("default"))
            cb.Margin = Thickness(0, 8, 0, 0)
            root.Children.Add(cb)
            editors[key] = ("check", cb)
        elif kind == "choice":
            lbl = TextBlock()
            lbl.Text = spec["label"]
            lbl.Margin = Thickness(0, 8, 0, 3)
            root.Children.Add(lbl)
            radio_panel = StackPanel()
            radio_panel.Orientation = Orientation.Horizontal
            radios = []
            for choice in spec.get("choices", []):
                rb = RadioButton()
                rb.Content = choice
                rb.GroupName = "grp_" + key
                rb.Margin = Thickness(0, 0, 14, 0)
                rb.IsChecked = (choice == spec.get("default"))
                radio_panel.Children.Add(rb)
                radios.append(rb)
            if radios and not any(r.IsChecked for r in radios):
                radios[0].IsChecked = True
            root.Children.Add(radio_panel)
            editors[key] = ("choice", radios)
        else:  # text / int
            lbl = TextBlock()
            lbl.Text = spec["label"]
            lbl.Margin = Thickness(0, 8, 0, 3)
            root.Children.Add(lbl)
            tb = TextBox()
            tb.Text = u"{0}".format(spec.get("default", "") or "")
            tb.MinWidth = 320
            root.Children.Add(tb)
            editors[key] = (kind, tb)
        if spec.get("hint"):
            hint = TextBlock()
            hint.Text = spec["hint"]
            hint.TextWrapping = TextWrapping.Wrap
            hint.MaxWidth = 420
            hint.FontSize = 11.0
            hint.Margin = Thickness(0, 2, 0, 0)
            hint.Foreground = w.Resources["FgMuted"]
            root.Children.Add(hint)

    result = {}

    def on_ok(sender, args):
        result.clear()
        valid = True
        for spec in fields:
            key = spec["key"]
            kind, ctrl = editors[key]
            if kind == "check":
                result[key] = bool(ctrl.IsChecked)
            elif kind == "choice":
                picked = next((r.Content for r in ctrl if r.IsChecked), None)
                result[key] = picked
            elif kind == "int":
                try:
                    result[key] = int((ctrl.Text or "").strip())
                    ctrl.ClearValue(TextBox.BorderBrushProperty)
                except (TypeError, ValueError):
                    ctrl.BorderBrush = _ERROR_BRUSH
                    valid = False
            else:
                result[key] = ctrl.Text or ""
        if valid:
            w.DialogResult = True
            w.Close()

    root.Children.Add(_button_row(w, ok_label, cancel_label, on_ok))
    w.Content = root

    # focus the first text input
    for spec in fields:
        kind, ctrl = editors[spec["key"]]
        if kind in ("text", "int"):
            ctrl.SelectAll()
            ctrl.Focus()
            break

    if w.ShowDialog():
        return result
    return None


def ask_string(title, prompt, default="", owner=None, message=None):
    """Single themed text prompt. Returns str or None."""
    vals = ask_form(title,
                    [{"key": "value", "label": prompt, "default": default}],
                    owner=owner, message=message)
    return vals["value"] if vals else None


def pick_item(title, items, owner=None, message=None, allow_filter=True):
    """Themed filterable single-select list. Returns the item or None."""
    w = _make_window(title, owner, width=440)
    root = DockPanel()
    root.Margin = Thickness(16)

    result = {"value": None}

    def commit():
        if listbox.SelectedItem is not None:
            result["value"] = listbox.SelectedItem
            w.DialogResult = True
            w.Close()

    def on_ok(sender, args):
        commit()

    buttons = _button_row(w, "Select", "Cancel", on_ok)
    DockPanel.SetDock(buttons, Dock.Bottom)
    root.Children.Add(buttons)

    if message:
        msg = _muted_text(w, message)
        DockPanel.SetDock(msg, Dock.Top)
        root.Children.Add(msg)

    listbox = ListBox()
    listbox.Height = 320

    all_items = [u"{0}".format(i) for i in items]

    def refill(query):
        listbox.Items.Clear()
        q = (query or "").lower()
        for it in all_items:
            if not q or q in it.lower():
                listbox.Items.Add(it)
        if listbox.Items.Count:
            listbox.SelectedIndex = 0

    if allow_filter:
        filter_box = TextBox()
        filter_box.Margin = Thickness(0, 0, 0, 8)

        def on_filter(sender, args):
            refill(filter_box.Text)

        filter_box.TextChanged += on_filter
        DockPanel.SetDock(filter_box, Dock.Top)
        root.Children.Add(filter_box)

    def on_double_click(sender, args):
        commit()

    listbox.MouseDoubleClick += on_double_click
    root.Children.Add(listbox)
    refill("")

    w.Content = root
    if allow_filter:
        filter_box.Focus()
    if w.ShowDialog():
        return result["value"]
    return None


def pick_checks(title, items, checked=None, owner=None, message=None,
                allow_filter=True):
    """Themed multi-select checklist over a long list of labels.

    Args:
        items: list of label strings
        checked: iterable of labels that start ticked

    Returns:
        list of ticked labels (order follows `items`), or None if cancelled.
    """
    checked = set(checked or [])
    w = _make_window(title, owner, width=460)
    root = DockPanel()
    root.Margin = Thickness(16)

    boxes = {}  # label -> CheckBox

    def on_ok(sender, args):
        w.DialogResult = True
        w.Close()

    buttons = _button_row(w, "OK", "Cancel", on_ok)
    DockPanel.SetDock(buttons, Dock.Bottom)
    root.Children.Add(buttons)

    # all / none helpers
    bar = StackPanel()
    bar.Orientation = Orientation.Horizontal
    bar.Margin = Thickness(0, 0, 0, 6)
    DockPanel.SetDock(bar, Dock.Bottom)

    def set_all(state):
        for cb in boxes.values():
            if cb.Visibility == Visibility.Visible:
                cb.IsChecked = state

    btn_all = Button()
    btn_all.Content = "All"
    btn_all.MinWidth = 56
    btn_all.Margin = Thickness(0, 0, 6, 0)
    btn_all.Click += lambda s, a: set_all(True)
    btn_none = Button()
    btn_none.Content = "None"
    btn_none.MinWidth = 56
    btn_none.Click += lambda s, a: set_all(False)
    bar.Children.Add(btn_all)
    bar.Children.Add(btn_none)
    root.Children.Add(bar)

    if message:
        msg = _muted_text(w, message)
        DockPanel.SetDock(msg, Dock.Top)
        root.Children.Add(msg)

    panel = StackPanel()
    for label in items:
        cb = CheckBox()
        cb.Content = label
        cb.IsChecked = label in checked
        cb.Margin = Thickness(0, 3, 0, 3)
        panel.Children.Add(cb)
        boxes[label] = cb

    if allow_filter:
        filter_box = TextBox()
        filter_box.Margin = Thickness(0, 0, 0, 8)

        def on_filter(sender, args):
            q = (filter_box.Text or "").lower()
            for label, cb in boxes.items():
                cb.Visibility = (Visibility.Visible if (not q or q in label.lower())
                                 else Visibility.Collapsed)

        filter_box.TextChanged += on_filter
        DockPanel.SetDock(filter_box, Dock.Top)
        root.Children.Add(filter_box)

    scroll = ScrollViewer()
    scroll.VerticalScrollBarVisibility = ScrollBarVisibility.Auto
    scroll.MaxHeight = 380
    scroll.Content = panel
    root.Children.Add(scroll)

    w.Content = root
    if w.ShowDialog():
        return [label for label in items if boxes[label].IsChecked]
    return None


def message(title, text, owner=None):
    """Themed information dialog with a single OK button."""
    w = _make_window(title, owner)
    root = StackPanel()
    root.Margin = Thickness(16)
    root.MinWidth = 320
    body = TextBlock()
    body.Text = text
    body.TextWrapping = TextWrapping.Wrap
    body.MaxWidth = 460
    root.Children.Add(body)

    def on_ok(sender, args):
        w.DialogResult = True
        w.Close()

    root.Children.Add(_button_row(w, "OK", None, on_ok))
    w.Content = root
    w.ShowDialog()


def confirm(title, text, owner=None, ok_label="Yes", cancel_label="Cancel"):
    """Themed yes/cancel dialog. Returns True only on confirm."""
    w = _make_window(title, owner)
    root = StackPanel()
    root.Margin = Thickness(16)
    root.MinWidth = 320
    body = TextBlock()
    body.Text = text
    body.TextWrapping = TextWrapping.Wrap
    body.MaxWidth = 460
    root.Children.Add(body)

    def on_ok(sender, args):
        w.DialogResult = True
        w.Close()

    root.Children.Add(_button_row(w, ok_label, cancel_label, on_ok))
    w.Content = root
    return bool(w.ShowDialog())
