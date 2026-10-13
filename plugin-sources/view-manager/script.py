# -*- coding: utf-8 -*-
__title__  = "View Manager"
__author__ = "Sendit"
__doc__    = """View Manager

A bulk, themed grid for views (the Sheet Manager workflow, for views):
filter, rename, bulk-apply a view template, bulk-set scale / detail level /
discipline (with warnings where a template governs the value), find &
replace, jump straight to a view, flag views not placed on any sheet, and
delete.

Templates... opens a power-editor: edit template name/scale, toggle which
parameters each template controls and which categories it shows, delete
unused templates, and export / import templates to JSON for office-standard
reuse across projects.

Follows Revit's Dark/Light theme (Revit 2024+)."""

from pyrevit import revit, DB, forms, script

from System.Collections.ObjectModel import ObservableCollection
from System.Collections.Generic import List

from sendit import theme, dialogs, views
from sendit.bindable import Notifiable

doc    = revit.doc
uidoc  = revit.uidoc
logger = script.get_logger()

# set by "Open in Revit"; activated after the modal window closes
PENDING_OPEN = {"view": None}


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------
class ViewRow(Notifiable):
    def __init__(self, view, sheet_map):
        Notifiable.__init__(self)
        self.view = view
        self._selected = False
        self._name = views._safe_name(view)
        self.Type = views.view_type_name(view)
        self.Scale = views.scale_of(view)
        self.Detail = views.detail_of(view)
        self.Discipline = views.discipline_of(view)
        self.Template = views.template_name(doc, view)
        self.OnSheet = sheet_map.get(view.Id.Value, u"")

    def is_dirty(self):
        return self._name != views._safe_name(self.view)

    @property
    def Selected(self):
        return self._selected

    @Selected.setter
    def Selected(self, v):
        self._selected = bool(v); self._notify("Selected")

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, v):
        self._name = v; self._notify("Name")


class TemplateRow(Notifiable):
    def __init__(self, template, usage):
        Notifiable.__init__(self)
        self.template = template
        self._selected = False
        self._name = views._safe_name(template)
        self._scale = views.scale_of(template)
        self.Type = views.view_type_name(template)
        self.Used = usage.get(template.Id.Value, 0)

    def is_dirty(self):
        return (self._name != views._safe_name(self.template)
                or self._scale != views.scale_of(self.template))

    @property
    def Selected(self):
        return self._selected

    @Selected.setter
    def Selected(self, v):
        self._selected = bool(v); self._notify("Selected")

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, v):
        self._name = v; self._notify("Name")

    @property
    def Scale(self):
        return self._scale

    @Scale.setter
    def Scale(self, v):
        self._scale = v; self._notify("Scale")


# ---------------------------------------------------------------------------
# Shared rename helper (two-pass temp names avoid mid-batch clashes)
# ---------------------------------------------------------------------------
def apply_renames(rows, label):
    """rows: objects with .Name (staged) and .<elem>.  Returns error list."""
    changes = [r for r in rows if r.is_dirty()]
    if not changes:
        return []
    errors = []
    with revit.Transaction("View Manager: " + label):
        for i, r in enumerate(changes):
            try:
                _set_elem_name(r, u"~SENDIT_TMP~{0}".format(i))
            except Exception:
                pass
        for r in changes:
            try:
                _set_elem_name(r, r.Name)
            except Exception as ex:
                errors.append(u"'{0}': {1}".format(r.Name, ex))
    return errors


def _set_elem_name(row, name):
    elem = getattr(row, "view", None) or getattr(row, "template", None)
    elem.Name = name


# ---------------------------------------------------------------------------
# Template power-editor window
# ---------------------------------------------------------------------------
TEMPLATES_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="View Template Editor" Height="560" Width="720"
        MinHeight="360" MinWidth="600"
        WindowStartupLocation="CenterOwner"
        UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
  <DockPanel Margin="10">
    <WrapPanel DockPanel.Dock="Top" Margin="0,0,0,8">
      <Button x:Name="t_apply" Content="Apply edits" Margin="0,0,6,4"
              Style="{DynamicResource AccentButton}" IsEnabled="False"
              ToolTip="Write staged name / scale edits back to the templates"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="t_includes" Content="Includes..." Margin="0,0,6,4"
              ToolTip="Toggle which parameters the selected template controls"/>
      <Button x:Name="t_cats" Content="Categories..." Margin="0,0,6,4"
              ToolTip="Toggle category visibility in the selected template"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="t_export" Content="Export..." Margin="0,0,6,4"
              ToolTip="Export ticked templates (or all) to a JSON file"/>
      <Button x:Name="t_import" Content="Import..." Margin="0,0,6,4"
              ToolTip="Apply templates from a JSON file by matching names"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="t_unused" Content="Tick unused" Margin="0,0,6,4"
              ToolTip="Tick every template no view is using"/>
      <Button x:Name="t_del" Content="Delete" Margin="0,0,6,4"
              ToolTip="Delete the ticked templates"/>
    </WrapPanel>
    <DockPanel DockPanel.Dock="Bottom" Margin="0,8,0,0">
      <Button x:Name="t_close" Content="Close" Width="80" DockPanel.Dock="Right"/>
      <TextBlock x:Name="t_status" VerticalAlignment="Center"
                 Foreground="{DynamicResource FgMuted}"/>
    </DockPanel>
    <DataGrid x:Name="t_grid" AutoGenerateColumns="False" SelectionMode="Extended">
      <DataGrid.Columns>
        <DataGridTemplateColumn Width="34">
          <DataGridTemplateColumn.CellTemplate>
            <DataTemplate>
              <CheckBox IsChecked="{Binding Selected, Mode=TwoWay,
                        UpdateSourceTrigger=PropertyChanged}"
                        HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </DataTemplate>
          </DataGridTemplateColumn.CellTemplate>
        </DataGridTemplateColumn>
        <DataGridTextColumn Header="Template Name" Binding="{Binding Name}" Width="*"/>
        <DataGridTextColumn Header="Type" Binding="{Binding Type}" Width="150" IsReadOnly="True"/>
        <DataGridTextColumn Header="Scale 1:" Binding="{Binding Scale}" Width="70"/>
        <DataGridTextColumn Header="Used by" Binding="{Binding Used}" Width="64" IsReadOnly="True"/>
      </DataGrid.Columns>
    </DataGrid>
  </DockPanel>
</Window>
"""


class TemplateEditor(forms.WPFWindow):
    def __init__(self, xaml, owner):
        forms.WPFWindow.__init__(self, xaml, literal_string=True)
        theme.style_window(self)
        if owner is not None:
            self.Owner = owner
        self.rows = []
        self.reload()
        self.t_apply.Click += self.on_apply
        self.t_includes.Click += self.on_includes
        self.t_cats.Click += self.on_categories
        self.t_export.Click += self.on_export
        self.t_import.Click += self.on_import
        self.t_unused.Click += self.on_tick_unused
        self.t_del.Click += self.on_delete
        self.t_close.Click += lambda s, a: self.Close()

    def reload(self):
        usage = views.template_usage(doc)
        self.rows = [TemplateRow(t, usage) for t in views.collect_templates(doc)]
        for r in self.rows:
            r.add_PropertyChanged(self.on_row_changed)
        coll = ObservableCollection[object]()
        for r in self.rows:
            coll.Add(r)
        self.t_grid.ItemsSource = coll
        self.refresh_status()

    def on_row_changed(self, sender, args):
        self.refresh_status()

    def refresh_status(self):
        ticked = len([r for r in self.rows if r.Selected])
        staged = len([r for r in self.rows if r.is_dirty()])
        self.t_apply.IsEnabled = staged > 0
        self.t_status.Text = "{0} templates   |   {1} ticked{2}".format(
            len(self.rows), ticked,
            "   |   {0} staged".format(staged) if staged else "")

    def checked(self):
        return [r for r in self.rows if r.Selected]

    def selected_single(self):
        sel = self.t_grid.SelectedItem
        if sel is None:
            dialogs.message("Template", "Highlight a single template row first.",
                            owner=self)
        return sel

    def on_apply(self, sender, args):
        errors = apply_renames(self.rows, "rename templates")
        with revit.Transaction("View Manager: template scale"):
            for r in self.rows:
                if r.Scale != views.scale_of(r.template):
                    try:
                        r.template.Scale = int(r.Scale)
                    except Exception as ex:
                        errors.append(u"'{0}' scale: {1}".format(r.Name, ex))
        self.reload()
        if errors:
            dialogs.message("Apply edits", "\n".join(errors), owner=self)

    def on_includes(self, sender, args):
        row = self.selected_single()
        if row is None:
            return
        t = row.template
        ids = list(t.GetTemplateParameterIds())
        labels, label_to_id = [], {}
        for eid in ids:
            lbl = views.param_label(doc, eid)
            base, n = lbl, 2
            while lbl in label_to_id:
                lbl = u"{0} ({1})".format(base, n); n += 1
            labels.append(lbl)
            label_to_id[lbl] = eid
        controlled = views.controlled_param_ids(t)
        pre = [lbl for lbl in labels if label_to_id[lbl].Value in controlled]
        picked = dialogs.pick_checks(
            "Included parameters - {0}".format(row.Name), sorted(labels),
            checked=pre, owner=self,
            message="Ticked parameters are controlled by this template.")
        if picked is None:
            return
        picked_ids = set(label_to_id[lbl].Value for lbl in picked)
        non_controlled = List[DB.ElementId](
            [label_to_id[lbl] for lbl in labels
             if label_to_id[lbl].Value not in picked_ids])
        try:
            with revit.Transaction("View Manager: template includes"):
                t.SetNonControlledTemplateParameterIds(non_controlled)
            dialogs.message("Includes",
                            "{0} now controls {1} parameter(s).".format(
                                row.Name, len(picked)), owner=self)
        except Exception as ex:
            dialogs.message("Includes", "Could not update: {0}".format(ex),
                            owner=self)

    def on_categories(self, sender, args):
        row = self.selected_single()
        if row is None:
            return
        t = row.template
        vis = views.category_visibility(doc, t)
        names = sorted(vis.keys())
        shown = [n for n in names if not vis[n]]   # ticked = visible
        picked = dialogs.pick_checks(
            "Visible categories - {0}".format(row.Name), names,
            checked=shown, owner=self,
            message="Ticked categories are shown; unticked are hidden.")
        if picked is None:
            return
        picked_set = set(picked)
        cats = views.model_categories(doc)
        errors = 0
        with revit.Transaction("View Manager: template categories"):
            for name in names:
                cat = cats.get(name)
                if cat is None:
                    continue
                want_hidden = name not in picked_set
                try:
                    if t.GetCategoryHidden(cat.Id) != want_hidden:
                        t.SetCategoryHidden(cat.Id, want_hidden)
                except Exception:
                    errors += 1
        dialogs.message("Categories",
                        "Updated visibility for '{0}'.{1}".format(
                            row.Name,
                            "" if not errors else
                            "\n{0} category(ies) could not be changed.".format(errors)),
                        owner=self)

    def on_export(self, sender, args):
        targets = [r.template for r in self.checked()] or [r.template for r in self.rows]
        if not targets:
            dialogs.message("Export", "No templates to export.", owner=self)
            return
        path = forms.save_file(file_ext="json")
        if not path:
            return
        payload = views.export_templates(doc, targets)
        views.write_json(path, payload)
        dialogs.message("Export",
                        "Exported {0} template(s) to:\n{1}".format(
                            len(targets), path), owner=self)

    def on_import(self, sender, args):
        path = forms.pick_file(file_ext="json")
        if not path:
            return
        try:
            payload = views.read_json(path)
            incoming = payload.get("sendit_templates", [])
        except Exception as ex:
            dialogs.message("Import", "Could not read file:\n{0}".format(ex),
                            owner=self)
            return
        if not incoming:
            dialogs.message("Import", "No Sendit templates found in that file.",
                            owner=self)
            return

        by_name = dict((views._safe_name(r.template), r.template) for r in self.rows)
        target_index = self._build_param_index()
        target_cat_names = set(views.model_categories(doc).keys())

        reports, applied, skipped = [], 0, []
        with revit.Transaction("View Manager: import templates"):
            for spec in incoming:
                name = spec.get("name", "")
                target = by_name.get(name)
                if target is None:
                    skipped.append(name)
                    continue
                matched, unmatched = views.match_controlled(
                    spec.get("controlled", []), target_index)
                appliable, missing = views.match_categories(
                    spec.get("categories", {}), target_cat_names)
                self._apply_to_template(target, spec, matched, appliable)
                applied += 1
                reports.append(views.merge_report(
                    name, matched, unmatched, appliable, missing))
        self.reload()

        msg = "Applied {0} template(s).".format(applied)
        if reports:
            msg += "\n\n" + "\n".join(reports)
        if skipped:
            msg += ("\n\nNo matching template in this model (skipped):\n"
                    + ", ".join(skipped)
                    + "\n\nCreate a template of the same name first, then "
                      "re-import to receive its settings.")
        dialogs.message("Import results", msg, owner=self)

    def _build_param_index(self):
        """{'builtins': set(neg ids), 'by_name': {name: id_int}} for matching."""
        builtins, by_name = set(), {}
        for r in self.rows:
            try:
                for eid in r.template.GetTemplateParameterIds():
                    if eid.Value < 0:
                        builtins.add(eid.Value)
                    else:
                        by_name[views.param_label(doc, eid)] = eid.Value
            except Exception:
                pass
        return {"builtins": builtins, "by_name": by_name}

    def _apply_to_template(self, target, spec, matched_param_ids, categories):
        # scale
        try:
            if spec.get("scale"):
                target.Scale = int(spec["scale"])
        except Exception:
            pass
        # controlled-parameter set
        try:
            all_ids = list(target.GetTemplateParameterIds())
            keep = set(matched_param_ids)
            non_controlled = List[DB.ElementId](
                [eid for eid in all_ids if eid.Value not in keep])
            target.SetNonControlledTemplateParameterIds(non_controlled)
        except Exception:
            pass
        # category visibility
        cats = views.model_categories(doc)
        for cname, hidden in categories.items():
            cat = cats.get(cname)
            if cat is None:
                continue
            try:
                if target.GetCategoryHidden(cat.Id) != bool(hidden):
                    target.SetCategoryHidden(cat.Id, bool(hidden))
            except Exception:
                pass

    def on_tick_unused(self, sender, args):
        for r in self.rows:
            r.Selected = (r.Used == 0)

    def on_delete(self, sender, args):
        rows = self.checked()
        if not rows:
            dialogs.message("Delete", "Tick the templates to delete.", owner=self)
            return
        in_use = [r for r in rows if r.Used > 0]
        warn = ("\n\n{0} of these are in use; views using them will revert to "
                "<None>.".format(len(in_use)) if in_use else "")
        if not dialogs.confirm("Delete templates",
                               "Delete {0} template(s)?{1}".format(len(rows), warn),
                               owner=self, ok_label="Delete"):
            return
        with revit.Transaction("View Manager: delete templates"):
            for r in rows:
                try:
                    doc.Delete(r.template.Id)
                except Exception as ex:
                    logger.error("delete template %s: %s", r.Name, ex)
        self.reload()


# ---------------------------------------------------------------------------
# Main view manager window
# ---------------------------------------------------------------------------
XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="View Manager" Height="680" Width="1040"
        MinHeight="500" MinWidth="900"
        WindowStartupLocation="CenterScreen"
        UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
  <DockPanel Margin="10">

    <WrapPanel DockPanel.Dock="Top" Margin="0,0,0,8">
      <Button x:Name="apply_btn" Content="Apply edits" Margin="0,0,6,4"
              Style="{DynamicResource AccentButton}" IsEnabled="False"
              ToolTip="Write staged name edits to the model"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="tmpl_btn" Content="Apply template" Margin="0,0,6,4"
              ToolTip="Apply a view template to the ticked views"/>
      <Button x:Name="scale_btn" Content="Set scale" Margin="0,0,6,4"
              ToolTip="Set the scale of the ticked views"/>
      <Button x:Name="detail_btn" Content="Set detail" Margin="0,0,6,4"
              ToolTip="Set the detail level of the ticked views"/>
      <Button x:Name="disc_btn" Content="Set discipline" Margin="0,0,6,4"
              ToolTip="Set the discipline of the ticked views"/>
      <Button x:Name="frep_btn" Content="Find / Replace" Margin="0,0,6,4"
              ToolTip="Stage a find &amp; replace on view names"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="templates_btn" Content="Templates..." Margin="0,0,6,4"
              ToolTip="Open the view-template power editor"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="unplaced_btn" Content="Tick unplaced" Margin="0,0,6,4"
              ToolTip="Tick every view not placed on a sheet (purge candidates)"/>
      <Button x:Name="open_btn" Content="Open in Revit" Margin="0,0,6,4"
              ToolTip="Close and activate the highlighted view"/>
      <Button x:Name="del_btn" Content="Delete" Margin="0,0,6,4"
              ToolTip="Delete the ticked views"/>
    </WrapPanel>

    <DockPanel DockPanel.Dock="Top" Margin="0,0,0,8">
      <TextBlock Text="Filter:" VerticalAlignment="Center" Margin="0,0,6,0"/>
      <CheckBox x:Name="select_all" Content="Select all (visible)"
                DockPanel.Dock="Right" Margin="12,0,0,0"/>
      <TextBox x:Name="search_box" MaxWidth="280" HorizontalAlignment="Left"
               MinWidth="220"/>
    </DockPanel>

    <Border DockPanel.Dock="Bottom" BorderBrush="{DynamicResource Border}"
            BorderThickness="0,1,0,0" Padding="0,7,0,0" Margin="0,8,0,0">
      <DockPanel>
        <TextBlock x:Name="staged_note" DockPanel.Dock="Right"
                   Foreground="{DynamicResource FgMuted}"/>
        <TextBlock x:Name="status" Foreground="{DynamicResource FgMuted}"/>
      </DockPanel>
    </Border>

    <DataGrid x:Name="views_grid" AutoGenerateColumns="False"
              SelectionMode="Extended">
      <DataGrid.Columns>
        <DataGridTemplateColumn Width="34">
          <DataGridTemplateColumn.CellTemplate>
            <DataTemplate>
              <CheckBox IsChecked="{Binding Selected, Mode=TwoWay,
                        UpdateSourceTrigger=PropertyChanged}"
                        HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </DataTemplate>
          </DataGridTemplateColumn.CellTemplate>
        </DataGridTemplateColumn>
        <DataGridTextColumn Header="View Name" Binding="{Binding Name}" Width="*"/>
        <DataGridTextColumn Header="Type" Binding="{Binding Type}" Width="120" IsReadOnly="True"/>
        <DataGridTextColumn Header="Scale 1:" Binding="{Binding Scale}" Width="64" IsReadOnly="True"/>
        <DataGridTextColumn Header="Detail" Binding="{Binding Detail}" Width="64" IsReadOnly="True"/>
        <DataGridTextColumn Header="Discipline" Binding="{Binding Discipline}" Width="92" IsReadOnly="True"/>
        <DataGridTextColumn Header="Template" Binding="{Binding Template}" Width="160" IsReadOnly="True"/>
        <DataGridTextColumn Header="On Sheet" Binding="{Binding OnSheet}" Width="80" IsReadOnly="True"/>
      </DataGrid.Columns>
    </DataGrid>
  </DockPanel>
</Window>
"""


class ViewManagerWindow(forms.WPFWindow):
    def __init__(self, xaml):
        forms.WPFWindow.__init__(self, xaml, literal_string=True)
        theme.style_window(self)
        self.all_rows = []
        self.reload()
        self.search_box.TextChanged += lambda s, a: self.apply_filter()
        self.select_all.Click += self.on_select_all
        self.apply_btn.Click += self.on_apply
        self.tmpl_btn.Click += self.on_apply_template
        self.scale_btn.Click += self.on_set_scale
        self.detail_btn.Click += self.on_set_detail
        self.disc_btn.Click += self.on_set_discipline
        self.frep_btn.Click += self.on_find_replace
        self.templates_btn.Click += self.on_templates
        self.unplaced_btn.Click += self.on_tick_unplaced
        self.open_btn.Click += self.on_open
        self.del_btn.Click += self.on_delete

    # ---- data ----
    def reload(self):
        sheet_map = views.sheet_of_view(doc)
        self.all_rows = [ViewRow(v, sheet_map) for v in views.collect_views(doc)]
        for r in self.all_rows:
            r.add_PropertyChanged(self.on_row_changed)
        self.apply_filter()

    def apply_filter(self):
        q = (self.search_box.Text or "").lower() if hasattr(self, "search_box") else ""
        coll = ObservableCollection[object]()
        for r in self.all_rows:
            if (not q or q in (r.Name or "").lower()
                    or q in (r.Type or "").lower()
                    or q in (r.Template or "").lower()):
                coll.Add(r)
        self.views_grid.ItemsSource = coll
        self.refresh_status()

    def visible_rows(self):
        return list(self.views_grid.ItemsSource or [])

    def selected_rows(self):
        return [r for r in self.all_rows if r.Selected]

    def staged_rows(self):
        return [r for r in self.all_rows if r.is_dirty()]

    def refresh_status(self):
        self.status.Text = "{0} views   |   {1} shown   |   {2} ticked".format(
            len(self.all_rows), len(self.visible_rows()),
            len(self.selected_rows()))
        staged = len(self.staged_rows())
        self.apply_btn.IsEnabled = staged > 0
        self.staged_note.Text = ("{0} staged rename(s) - click Apply edits".format(
            staged) if staged else "")

    def on_row_changed(self, sender, args):
        if args.PropertyName in ("Selected", "Name"):
            self.refresh_status()

    def on_select_all(self, sender, args):
        state = bool(self.select_all.IsChecked)
        for r in self.visible_rows():
            r.Selected = state

    # ---- edits ----
    def on_apply(self, sender, args):
        errors = apply_renames(self.all_rows, "rename views")
        self.reload()
        if errors:
            dialogs.message("Apply edits",
                            "Applied with {0} issue(s):\n\n{1}".format(
                                len(errors), "\n".join(errors)), owner=self)

    def on_apply_template(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Apply template", "Tick some views first.", owner=self)
            return
        templates = views.collect_templates(doc)
        if not templates:
            dialogs.message("Apply template", "This model has no view templates.",
                            owner=self)
            return
        labels = {}
        for t in templates:
            labels[u"{0}  ({1})".format(views._safe_name(t),
                                        views.view_type_name(t))] = t
        labels[u"<None> - remove template"] = None
        choice = dialogs.pick_item("Apply template to {0} view(s)".format(len(rows)),
                                   sorted(labels.keys()), owner=self)
        if choice is None:
            return
        target = labels[choice]
        tid = target.Id if target is not None else DB.ElementId.InvalidElementId
        ok, fail = 0, []
        with revit.Transaction("View Manager: apply template"):
            for r in rows:
                try:
                    r.view.ViewTemplateId = tid
                    ok += 1
                except Exception as ex:
                    fail.append(u"{0}: {1}".format(r.Name, ex))
        self.reload()
        msg = "Applied to {0} view(s).".format(ok)
        if fail:
            msg += "\n\nIncompatible / failed ({0}):\n{1}".format(
                len(fail), "\n".join(fail[:10]))
        dialogs.message("Apply template", msg, owner=self)

    def _bulk_field(self, label, getter_setter, value):
        """Run a bulk field set over ticked views, warning where templated."""
        rows = self.selected_rows()
        if not rows:
            dialogs.message(label, "Tick some views first.", owner=self)
            return
        done, templated, fail = 0, [], []
        with revit.Transaction("View Manager: " + label):
            for r in rows:
                governed, ok, err = getter_setter(r.view, value)
                if governed:
                    templated.append(r.Name)
                elif ok:
                    done += 1
                elif err:
                    fail.append(u"{0}: {1}".format(r.Name, err))
        self.reload()
        msg = "{0}: updated {1} view(s).".format(label, done)
        if templated:
            msg += ("\n\n{0} skipped - their template controls this value:\n{1}"
                    .format(len(templated), ", ".join(templated[:10])))
        if fail:
            msg += "\n\nFailed ({0}):\n{1}".format(len(fail), "\n".join(fail[:8]))
        dialogs.message(label, msg, owner=self)

    def on_set_scale(self, sender, args):
        vals = dialogs.ask_form("Set scale",
                                [{"key": "scale", "label": "Scale 1:",
                                  "type": "int", "default": 100}],
                                owner=self)
        if not vals:
            return

        def setter(view, value):
            if views.template_controls_param(doc, view, views.P_SCALE):
                return True, False, None
            try:
                view.Scale = int(value)
                return False, True, None
            except Exception as ex:
                return False, False, ex

        self._bulk_field("Set scale", setter, vals["scale"])

    def on_set_detail(self, sender, args):
        choice = dialogs.pick_item("Set detail level", views.DETAIL_LEVELS,
                                   owner=self, allow_filter=False)
        if not choice:
            return

        def setter(view, value):
            if views.template_controls_param(doc, view, views.P_DETAIL):
                return True, False, None
            try:
                view.DetailLevel = getattr(DB.ViewDetailLevel, value)
                return False, True, None
            except Exception as ex:
                return False, False, ex

        self._bulk_field("Set detail", setter, choice)

    def on_set_discipline(self, sender, args):
        choice = dialogs.pick_item("Set discipline", views.DISCIPLINES,
                                   owner=self, allow_filter=False)
        if not choice:
            return

        def setter(view, value):
            if views.template_controls_param(doc, view, views.P_DISCIPLINE):
                return True, False, None
            try:
                view.Discipline = getattr(DB.ViewDiscipline, value)
                return False, True, None
            except Exception as ex:
                return False, False, ex

        self._bulk_field("Set discipline", setter, choice)

    def on_find_replace(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Find / Replace", "Tick some views first.", owner=self)
            return
        vals = dialogs.ask_form(
            "Find / Replace in {0} view name(s)".format(len(rows)),
            [{"key": "find", "label": "Find"},
             {"key": "repl", "label": "Replace with"}],
            owner=self,
            message="Staged in the grid - nothing is written until 'Apply edits'.")
        if not vals or not vals["find"]:
            return
        for r in rows:
            r.Name = r.Name.replace(vals["find"], vals["repl"])

    def on_templates(self, sender, args):
        TemplateEditor(TEMPLATES_XAML, self).show_dialog()
        self.reload()   # names / applied templates may have changed

    def on_tick_unplaced(self, sender, args):
        for r in self.all_rows:
            r.Selected = (not r.OnSheet)

    def on_open(self, sender, args):
        sel = self.views_grid.SelectedItem
        if sel is None:
            dialogs.message("Open view", "Highlight a single view row first.",
                            owner=self)
            return
        PENDING_OPEN["view"] = sel.view
        self.Close()

    def on_delete(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Delete", "Tick some views first.", owner=self)
            return
        preview = "\n".join(u"{0}  ({1})".format(r.Name, r.Type) for r in rows[:8])
        if len(rows) > 8:
            preview += "\n... and {0} more".format(len(rows) - 8)
        if not dialogs.confirm("Delete views",
                               "Delete {0} view(s)?\n\n{1}".format(len(rows), preview),
                               owner=self, ok_label="Delete"):
            return
        with revit.Transaction("View Manager: delete views"):
            for r in rows:
                try:
                    doc.Delete(r.view.Id)
                except Exception as ex:
                    logger.error("delete view %s: %s", r.Name, ex)
        self.reload()


if not doc:
    forms.alert("Open a Revit document first.", exitscript=True)

ViewManagerWindow(XAML).show_dialog()

# activating a view can't happen while the modal window is up, so do it after
if PENDING_OPEN["view"] is not None:
    try:
        uidoc.ActiveView = PENDING_OPEN["view"]
    except Exception as ex:
        forms.alert("Could not open that view:\n{0}".format(ex), exitscript=False)
