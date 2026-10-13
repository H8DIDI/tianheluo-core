# -*- coding: utf-8 -*-
__title__  = "Sheet Manager"
__author__ = "Sendit"
__doc__    = """Sheet Manager

Editable sheet grid with bulk renumber/rename, find & replace, batch create
from CSV, duplicate, delete, revision manager, sheet sets, and PDF/CSV export
with filename templating.

The window follows Revit's Dark/Light theme (Revit 2024+)."""

import codecs

from pyrevit import revit, DB, forms, script

from System import DateTime
from System.Collections.ObjectModel import ObservableCollection
from System.Collections.Generic import List

from sendit import theme, dialogs
from sendit.bindable import Notifiable
from sendit.shell import open_path
from sendit.sheets import (collect_sheets, get_current_rev, get_name,
                             sanitize, view_count, export_pdf)

doc    = revit.doc
logger = script.get_logger()

NO_TITLEBLOCK = u"« No title block »"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def get_titleblock_symbols():
    syms = (DB.FilteredElementCollector(doc)
              .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
              .WhereElementIsElementType()
              .ToElements())
    return dict(("{0} : {1}".format(s.FamilyName, get_name(s)), s) for s in syms)


def ask_titleblock(owner):
    """Pick a title block. Returns (symbol_or_None, cancelled)."""
    syms = get_titleblock_symbols()
    items = [NO_TITLEBLOCK] + sorted(syms.keys())
    choice = dialogs.pick_item("Title block", items, owner=owner,
                               message="Title block for the new sheet(s):")
    if choice is None:
        return None, True
    return syms.get(choice), False


def ensure_active(symbol):
    if symbol and not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()


# ---------------------------------------------------------------------------
# Sheet-set operations (PrintManager / ViewSheetSetting workflow)
# ---------------------------------------------------------------------------
def list_sheet_sets():
    sets = (DB.FilteredElementCollector(doc)
              .OfClass(DB.ViewSheetSet)
              .ToElements())
    return dict((get_name(s), s) for s in sets)


def save_sheet_set(name, sheets):
    vset = DB.ViewSet()
    for s in sheets:
        vset.Insert(s)
    pm = doc.PrintManager
    pm.PrintRange = DB.PrintRange.Select
    vss = pm.ViewSheetSetting
    vss.CurrentViewSheetSet.Views = vset
    with revit.Transaction("Sheet Manager: save sheet set"):
        vss.SaveAs(name)  # throws if the name already exists


def sheets_in_set(sheet_set):
    ids = []
    it = sheet_set.Views.ForwardIterator()
    it.Reset()
    while it.MoveNext():
        v = it.Current
        if isinstance(v, DB.ViewSheet):
            ids.append(v.Id.Value)  # ElementId.Value (Int64) is the 2024+ accessor
    return set(ids)


# ---------------------------------------------------------------------------
# Row models
# ---------------------------------------------------------------------------
class SheetRow(Notifiable):
    def __init__(self, sheet):
        Notifiable.__init__(self)
        self.sheet = sheet
        self._selected = False
        self._number = sheet.SheetNumber
        self._name = sheet.Name
        self.CurrentRev = get_current_rev(sheet)
        self.ViewCount = view_count(sheet)
        self.IsPlaceholder = sheet.IsPlaceholder

    def is_dirty(self):
        return (self._number != self.sheet.SheetNumber
                or self._name != self.sheet.Name)

    @property
    def Selected(self):
        return self._selected

    @Selected.setter
    def Selected(self, v):
        self._selected = bool(v); self._notify("Selected")

    @property
    def Number(self):
        return self._number

    @Number.setter
    def Number(self, v):
        self._number = v; self._notify("Number")

    @property
    def Name(self):
        return self._name

    @Name.setter
    def Name(self, v):
        self._name = v; self._notify("Name")


class RevRow(Notifiable):
    def __init__(self, rev):
        Notifiable.__init__(self)
        self.rev = rev
        self._selected = False
        self.Seq = rev.SequenceNumber
        self._date = rev.RevisionDate
        self._desc = rev.Description
        self._by = rev.IssuedBy
        self._to = rev.IssuedTo
        self._issued = rev.Issued

    @property
    def Selected(self): return self._selected
    @Selected.setter
    def Selected(self, v): self._selected = bool(v); self._notify("Selected")

    @property
    def Date(self): return self._date
    @Date.setter
    def Date(self, v): self._date = v; self._notify("Date")

    @property
    def Description(self): return self._desc
    @Description.setter
    def Description(self, v): self._desc = v; self._notify("Description")

    @property
    def IssuedBy(self): return self._by
    @IssuedBy.setter
    def IssuedBy(self, v): self._by = v; self._notify("IssuedBy")

    @property
    def IssuedTo(self): return self._to
    @IssuedTo.setter
    def IssuedTo(self, v): self._to = v; self._notify("IssuedTo")

    @property
    def Issued(self): return self._issued
    @Issued.setter
    def Issued(self, v): self._issued = bool(v); self._notify("Issued")


# ---------------------------------------------------------------------------
# Revision manager window
# ---------------------------------------------------------------------------
REV_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Revision Manager" Height="500" Width="780"
        MinHeight="320" MinWidth="640"
        WindowStartupLocation="CenterScreen"
        UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
  <DockPanel Margin="10">
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="r_close" Content="Close" Width="80" DockPanel.Dock="Right"/>
      <StackPanel Orientation="Horizontal">
        <Button x:Name="r_add"   Content="Add"         Width="74" Margin="0,0,6,0"
                ToolTip="Create a new revision"/>
        <Button x:Name="r_apply" Content="Apply edits" Width="92" Margin="0,0,6,0"
                Style="{DynamicResource AccentButton}"
                ToolTip="Write the edited dates/descriptions back to the model"/>
        <Button x:Name="r_up"    Content="Move up"     Width="80" Margin="0,0,6,0"/>
        <Button x:Name="r_down"  Content="Move down"   Width="86" Margin="0,0,6,0"/>
        <Button x:Name="r_del"   Content="Delete"      Width="74" Margin="0,0,6,0"
                ToolTip="Delete the ticked revision(s)"/>
      </StackPanel>
    </DockPanel>
    <DataGrid x:Name="rev_grid" AutoGenerateColumns="False"
              SelectionMode="Single">
      <DataGrid.Columns>
        <DataGridTemplateColumn Header="" Width="34">
          <DataGridTemplateColumn.CellTemplate>
            <DataTemplate>
              <CheckBox IsChecked="{Binding Selected, Mode=TwoWay,
                        UpdateSourceTrigger=PropertyChanged}"
                        HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </DataTemplate>
          </DataGridTemplateColumn.CellTemplate>
        </DataGridTemplateColumn>
        <DataGridTextColumn Header="Seq" Binding="{Binding Seq}" Width="44" IsReadOnly="True"/>
        <DataGridTextColumn Header="Date" Binding="{Binding Date}" Width="110"/>
        <DataGridTextColumn Header="Description" Binding="{Binding Description}" Width="*"/>
        <DataGridTextColumn Header="Issued By" Binding="{Binding IssuedBy}" Width="100"/>
        <DataGridTextColumn Header="Issued To" Binding="{Binding IssuedTo}" Width="100"/>
        <DataGridTemplateColumn Header="Issued" Width="56">
          <DataGridTemplateColumn.CellTemplate>
            <DataTemplate>
              <CheckBox IsChecked="{Binding Issued, Mode=TwoWay,
                        UpdateSourceTrigger=PropertyChanged}"
                        HorizontalAlignment="Center" VerticalAlignment="Center"/>
            </DataTemplate>
          </DataGridTemplateColumn.CellTemplate>
        </DataGridTemplateColumn>
      </DataGrid.Columns>
    </DataGrid>
  </DockPanel>
</Window>
"""


class RevisionManager(forms.WPFWindow):
    def __init__(self, xaml):
        forms.WPFWindow.__init__(self, xaml, literal_string=True)
        theme.style_window(self)
        self.rows = []
        self.reload()
        self.r_add.Click += self.on_add
        self.r_apply.Click += self.on_apply
        self.r_up.Click += lambda s, a: self.move(-1)
        self.r_down.Click += lambda s, a: self.move(1)
        self.r_del.Click += self.on_delete
        self.r_close.Click += lambda s, a: self.Close()

    def reload(self):
        rev_ids = list(DB.Revision.GetAllRevisionIds(doc))
        self.rows = [RevRow(doc.GetElement(rid)) for rid in rev_ids]
        coll = ObservableCollection[object]()
        for r in self.rows:
            coll.Add(r)
        self.rev_grid.ItemsSource = coll

    def checked(self):
        return [r for r in self.rows if r.Selected]

    def on_add(self, sender, args):
        with revit.Transaction("Add revision"):
            rev = DB.Revision.Create(doc)
            rev.Description = "New Revision"
            rev.RevisionDate = DateTime.Now.ToString("yyyy-MM-dd")
        self.reload()

    def on_apply(self, sender, args):
        errs = []
        with revit.Transaction("Edit revisions"):
            for r in self.rows:
                try:
                    r.rev.RevisionDate = r.Date
                    r.rev.Description = r.Description
                    r.rev.IssuedBy = r.IssuedBy
                    r.rev.IssuedTo = r.IssuedTo
                    r.rev.Issued = r.Issued
                except Exception as ex:
                    errs.append("Seq {0}: {1}".format(r.Seq, ex))
        self.reload()
        if errs:
            dialogs.message("Revision edits", "\n".join(errs), owner=self)

    def move(self, delta):
        sel = self.rev_grid.SelectedItem
        if sel is None:
            dialogs.message("Reorder", "Select (highlight) a revision row first.",
                            owner=self)
            return
        ids = list(DB.Revision.GetAllRevisionIds(doc))
        idx = next((i for i, rid in enumerate(ids) if rid.Value == sel.rev.Id.Value), -1)
        new = idx + delta
        if idx < 0 or new < 0 or new >= len(ids):
            return
        ids[idx], ids[new] = ids[new], ids[idx]
        with revit.Transaction("Reorder revisions"):
            DB.Revision.ReorderRevisionSequence(doc, List[DB.ElementId](ids))
        self.reload()

    def on_delete(self, sender, args):
        rows = self.checked()
        if not rows:
            dialogs.message("Delete revisions", "Tick the revision(s) to delete.",
                            owner=self)
            return
        if not dialogs.confirm("Delete revisions",
                               "Delete {0} revision(s)?".format(len(rows)),
                               owner=self, ok_label="Delete"):
            return
        with revit.Transaction("Delete revisions"):
            for r in rows:
                try:
                    doc.Delete(r.rev.Id)
                except Exception as ex:
                    logger.error("delete rev %s: %s", r.Seq, ex)
        self.reload()


# ---------------------------------------------------------------------------
# Main sheet manager window
# ---------------------------------------------------------------------------
XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Sheet Manager" Height="680" Width="1000"
        MinHeight="500" MinWidth="880"
        WindowStartupLocation="CenterScreen"
        UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
  <DockPanel Margin="10">

    <WrapPanel DockPanel.Dock="Top" Margin="0,0,0,8">
      <Button x:Name="apply_btn" Content="Apply edits" Margin="0,0,6,4"
              Style="{DynamicResource AccentButton}" IsEnabled="False"
              ToolTip="Write staged number/name edits to the model"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="add_btn"   Content="Add"        Margin="0,0,6,4"
              ToolTip="Create a single new sheet"/>
      <Button x:Name="batch_btn" Content="Batch CSV"  Margin="0,0,6,4"
              ToolTip="Create sheets from a CSV file (number,name per line)"/>
      <Button x:Name="dup_btn"   Content="Duplicate"  Margin="0,0,6,4"
              ToolTip="Duplicate the ticked sheets (titleblock only)"/>
      <Button x:Name="del_btn"   Content="Delete"     Margin="0,0,6,4"
              ToolTip="Delete the ticked sheets"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="renum_btn" Content="Renumber"     Margin="0,0,6,4"
              ToolTip="Stage sequential numbers onto the ticked sheets"/>
      <Button x:Name="frep_btn"  Content="Find / Replace" Margin="0,0,6,4"
              ToolTip="Stage a find &amp; replace on numbers and/or names"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="rev_btn"    Content="Add revision" Margin="0,0,6,4"
              ToolTip="Add an existing revision to the ticked sheets"/>
      <Button x:Name="revmgr_btn" Content="Revisions..." Margin="0,0,6,4"
              ToolTip="Create, edit, reorder and delete revisions"/>
      <Button x:Name="sets_btn"   Content="Sheet sets..." Margin="0,0,6,4"
              ToolTip="Save / load / delete sheet sets"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="export_btn" Content="Export PDF..." Margin="0,0,6,4"
              ToolTip="Export the ticked sheets to PDF"/>
      <Button x:Name="csv_btn"    Content="Export CSV"    Margin="0,0,6,4"
              ToolTip="Export the sheet list to a CSV file"/>
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

    <DataGrid x:Name="sheets_grid" AutoGenerateColumns="False"
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
        <DataGridTextColumn Header="Number" Binding="{Binding Number}" Width="130"/>
        <DataGridTextColumn Header="Sheet Name" Binding="{Binding Name}" Width="*"/>
        <DataGridTextColumn Header="Cur Rev" Binding="{Binding CurrentRev}" Width="70" IsReadOnly="True"/>
        <DataGridTextColumn Header="Views" Binding="{Binding ViewCount}" Width="54" IsReadOnly="True"/>
        <DataGridCheckBoxColumn Header="PH" Binding="{Binding IsPlaceholder}" Width="40" IsReadOnly="True"/>
      </DataGrid.Columns>
    </DataGrid>
  </DockPanel>
</Window>
"""


class SheetManagerWindow(forms.WPFWindow):
    def __init__(self, xaml):
        forms.WPFWindow.__init__(self, xaml, literal_string=True)
        theme.style_window(self)
        self.all_rows = []
        self.reload()
        self.search_box.TextChanged += self.on_filter
        self.select_all.Click += self.on_select_all
        self.apply_btn.Click += self.on_apply
        self.renum_btn.Click += self.on_renumber
        self.frep_btn.Click += self.on_find_replace
        self.add_btn.Click += self.on_add
        self.batch_btn.Click += self.on_batch
        self.dup_btn.Click += self.on_duplicate
        self.del_btn.Click += self.on_delete
        self.rev_btn.Click += self.on_add_revision
        self.revmgr_btn.Click += self.on_revision_manager
        self.sets_btn.Click += self.on_sheet_sets
        self.export_btn.Click += self.on_export
        self.csv_btn.Click += self.on_export_csv

    # ---- data ----
    def reload(self):
        self.all_rows = [SheetRow(s) for s in collect_sheets(doc)]
        for r in self.all_rows:
            r.add_PropertyChanged(self.on_row_changed)
        self.apply_filter()
        self.refresh_status()

    def apply_filter(self):
        q = (self.search_box.Text or "").lower() if hasattr(self, "search_box") else ""
        coll = ObservableCollection[object]()
        for r in self.all_rows:
            if not q or q in (r.Number or "").lower() or q in (r.Name or "").lower():
                coll.Add(r)
        self.sheets_grid.ItemsSource = coll
        self.refresh_status()

    def visible_rows(self):
        return list(self.sheets_grid.ItemsSource or [])

    def selected_rows(self):
        return [r for r in self.all_rows if r.Selected]

    def staged_rows(self):
        return [r for r in self.all_rows if r.is_dirty()]

    def refresh_status(self):
        shown = len(self.visible_rows())
        ticked = len(self.selected_rows())
        self.status.Text = "{0} sheets   |   {1} shown   |   {2} ticked".format(
            len(self.all_rows), shown, ticked)
        staged = len(self.staged_rows())
        self.apply_btn.IsEnabled = staged > 0
        self.staged_note.Text = (
            "{0} staged edit(s) - click Apply edits".format(staged)
            if staged else "")

    def on_row_changed(self, sender, args):
        if args.PropertyName in ("Selected", "Number", "Name"):
            self.refresh_status()

    # ---- editing ----
    def on_filter(self, sender, args):
        self.apply_filter()

    def on_select_all(self, sender, args):
        state = bool(self.select_all.IsChecked)
        for r in self.visible_rows():
            r.Selected = state

    def on_apply(self, sender, args):
        changes = self.staged_rows()
        if not changes:
            return
        errors = []
        with revit.Transaction("Sheet Manager: edit numbers/names"):
            originals = {}
            for i, r in enumerate(changes):
                if r.Number != r.sheet.SheetNumber:
                    originals[r] = r.sheet.SheetNumber
                    try:
                        r.sheet.SheetNumber = "~TMP~{0}".format(i)
                    except Exception:
                        pass
            for r in changes:
                if r in originals:
                    try:
                        r.sheet.SheetNumber = r.Number
                    except Exception as ex:
                        try:
                            r.sheet.SheetNumber = originals[r]
                        except Exception:
                            pass
                        errors.append("number '{0}': {1}".format(r.Number, ex))
                if r.Name != r.sheet.Name:
                    try:
                        r.sheet.Name = r.Name
                    except Exception as ex:
                        errors.append("name '{0}': {1}".format(r.Name, ex))
        self.reload()
        if errors:
            dialogs.message("Apply edits",
                            "Applied with {0} issue(s):\n\n{1}".format(
                                len(errors), "\n".join(errors)), owner=self)

    def on_renumber(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Renumber", "Tick some sheets first.", owner=self)
            return
        vals = dialogs.ask_form(
            "Renumber {0} sheet(s)".format(len(rows)),
            [{"key": "prefix", "label": "Prefix", "default": ""},
             {"key": "start", "label": "Start number", "type": "int", "default": 1},
             {"key": "pad", "label": "Zero-pad to N digits (0 = none)",
              "type": "int", "default": 0},
             {"key": "suffix", "label": "Suffix", "default": ""}],
            owner=self,
            message="New numbers are staged in the grid - nothing is written "
                    "until you click 'Apply edits'.")
        if not vals:
            return
        n = vals["start"]
        for r in sorted(rows, key=lambda x: x.Number):
            r.Number = "{0}{1}{2}".format(vals["prefix"],
                                          str(n).zfill(vals["pad"]),
                                          vals["suffix"])
            n += 1

    def on_find_replace(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Find / Replace", "Tick some sheets first.", owner=self)
            return
        vals = dialogs.ask_form(
            "Find / Replace in {0} sheet(s)".format(len(rows)),
            [{"key": "find", "label": "Find"},
             {"key": "repl", "label": "Replace with"},
             {"key": "scope", "label": "Apply to", "type": "choice",
              "choices": ["Number", "Name", "Both"], "default": "Name"}],
            owner=self,
            message="Changes are staged in the grid - nothing is written "
                    "until you click 'Apply edits'.")
        if not vals or not vals["find"]:
            return
        for r in rows:
            if vals["scope"] in ("Number", "Both"):
                r.Number = r.Number.replace(vals["find"], vals["repl"])
            if vals["scope"] in ("Name", "Both"):
                r.Name = r.Name.replace(vals["find"], vals["repl"])

    def on_add(self, sender, args):
        vals = dialogs.ask_form(
            "Add sheet",
            [{"key": "number", "label": "Sheet number"},
             {"key": "name", "label": "Sheet name", "default": "Unnamed"}],
            owner=self)
        if not vals:
            return
        sym, cancelled = ask_titleblock(self)
        if cancelled:
            return
        with revit.Transaction("Sheet Manager: add sheet"):
            ensure_active(sym)
            tbid = sym.Id if sym else DB.ElementId.InvalidElementId
            sheet = DB.ViewSheet.Create(doc, tbid)
            try:
                sheet.SheetNumber = vals["number"]
            except Exception as ex:
                dialogs.message("Add sheet",
                                "Could not set number: {0}".format(ex), owner=self)
            sheet.Name = vals["name"] or "Unnamed"
        self.reload()

    def on_batch(self, sender, args):
        path = forms.pick_file(file_ext="csv")
        if not path:
            return
        sym, cancelled = ask_titleblock(self)
        if cancelled:
            return
        created, errors = 0, []
        with revit.Transaction("Sheet Manager: batch create"):
            ensure_active(sym)
            tbid = sym.Id if sym else DB.ElementId.InvalidElementId
            with codecs.open(path, "r", "utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split(",")
                    number = parts[0].strip()
                    name = parts[1].strip() if len(parts) > 1 else "Unnamed"
                    try:
                        sheet = DB.ViewSheet.Create(doc, tbid)
                        sheet.SheetNumber = number
                        sheet.Name = name
                        created += 1
                    except Exception as ex:
                        errors.append("{0}: {1}".format(number, ex))
        self.reload()
        msg = "Created {0} sheet(s).".format(created)
        if errors:
            msg += "\n\nSkipped:\n" + "\n".join(errors)
        dialogs.message("Batch create", msg, owner=self)

    def on_duplicate(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Duplicate", "Tick some sheets first.", owner=self)
            return
        if not dialogs.confirm(
                "Duplicate",
                "Duplicate {0} sheet(s)?\n\nThe new sheets reuse each "
                "sheet's title block; viewports are not copied.".format(len(rows)),
                owner=self, ok_label="Duplicate"):
            return
        existing = set(r.sheet.SheetNumber for r in self.all_rows)

        def sheet_titleblock_typeid(sheet):
            tbs = (DB.FilteredElementCollector(doc, sheet.Id)
                     .OfCategory(DB.BuiltInCategory.OST_TitleBlocks)
                     .WhereElementIsNotElementType()
                     .ToElements())
            for tb in tbs:
                return tb.GetTypeId()
            return DB.ElementId.InvalidElementId

        with revit.Transaction("Sheet Manager: duplicate sheets"):
            for r in rows:
                tbid = sheet_titleblock_typeid(r.sheet)
                new = DB.ViewSheet.Create(doc, tbid)
                base = r.sheet.SheetNumber + "-COPY"
                num, i = base, 1
                while num in existing:
                    i += 1
                    num = "{0}{1}".format(base, i)
                existing.add(num)
                new.SheetNumber = num
                new.Name = r.sheet.Name
        self.reload()

    def on_delete(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Delete", "Tick some sheets first.", owner=self)
            return
        preview = "\n".join("{0} - {1}".format(r.Number, r.Name)
                            for r in rows[:8])
        if len(rows) > 8:
            preview += "\n... and {0} more".format(len(rows) - 8)
        if not dialogs.confirm("Delete sheets",
                               "Delete {0} sheet(s)?\n\n{1}".format(
                                   len(rows), preview),
                               owner=self, ok_label="Delete"):
            return
        with revit.Transaction("Sheet Manager: delete sheets"):
            for r in rows:
                try:
                    doc.Delete(r.sheet.Id)
                except Exception as ex:
                    logger.error("delete %s: %s", r.Number, ex)
        self.reload()

    def on_add_revision(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Add revision", "Tick some sheets first.", owner=self)
            return
        rev_ids = list(DB.Revision.GetAllRevisionIds(doc))
        if not rev_ids:
            dialogs.message("Add revision",
                            "No revisions exist. Use 'Revisions...' to create one.",
                            owner=self)
            return
        labels = {}
        for rid in rev_ids:
            rev = doc.GetElement(rid)
            labels["Seq {0} | {1} | {2}".format(
                rev.SequenceNumber, rev.RevisionDate, rev.Description)] = rid
        choice = dialogs.pick_item("Add revision to {0} sheet(s)".format(len(rows)),
                                   sorted(labels.keys()), owner=self)
        if not choice:
            return
        rev_id = labels[choice]
        with revit.Transaction("Sheet Manager: add revision"):
            for r in rows:
                addl = list(r.sheet.GetAdditionalRevisionIds())
                if not any(a.Value == rev_id.Value for a in addl):
                    addl.append(rev_id)
                    r.sheet.SetAdditionalRevisionIds(List[DB.ElementId](addl))
        self.reload()

    def on_revision_manager(self, sender, args):
        RevisionManager(REV_XAML).show_dialog()
        self.reload()  # Cur Rev column may have changed

    def on_sheet_sets(self, sender, args):
        action = dialogs.pick_item(
            "Sheet sets",
            ["Save ticked sheets as a set",
             "Tick sheets from a set",
             "Delete a set"],
            owner=self, allow_filter=False)
        if not action:
            return

        if action == "Save ticked sheets as a set":
            rows = self.selected_rows()
            if not rows:
                dialogs.message("Sheet sets", "Tick the sheets to include.",
                                owner=self)
                return
            name = dialogs.ask_string("Sheet set", "Set name", default="New Set",
                                      owner=self)
            if not name:
                return
            try:
                save_sheet_set(name, [r.sheet for r in rows])
                dialogs.message("Sheet sets",
                                "Saved set '{0}' with {1} sheet(s).".format(
                                    name, len(rows)), owner=self)
            except Exception as ex:
                dialogs.message("Sheet sets",
                                "Could not save set (name may already exist):"
                                "\n{0}".format(ex), owner=self)

        elif action == "Tick sheets from a set":
            sets = list_sheet_sets()
            if not sets:
                dialogs.message("Sheet sets", "No sheet sets in this model.",
                                owner=self)
                return
            choice = dialogs.pick_item("Pick a set", sorted(sets.keys()),
                                       owner=self)
            if not choice:
                return
            ids = sheets_in_set(sets[choice])
            for r in self.all_rows:
                r.Selected = (r.sheet.Id.Value in ids)

        elif action == "Delete a set":
            sets = list_sheet_sets()
            if not sets:
                dialogs.message("Sheet sets", "No sheet sets in this model.",
                                owner=self)
                return
            choice = dialogs.pick_item("Delete which set?", sorted(sets.keys()),
                                       owner=self)
            if not choice:
                return
            with revit.Transaction("Sheet Manager: delete sheet set"):
                doc.Delete(sets[choice].Id)

    def on_export(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Export PDF", "Tick the sheets to export.", owner=self)
            return
        vals = dialogs.ask_form(
            "Export {0} sheet(s) to PDF".format(len(rows)),
            [{"key": "mode", "label": "Mode", "type": "choice",
              "choices": ["One PDF per sheet", "Single combined PDF"],
              "default": "One PDF per sheet"},
             {"key": "template", "label": "Per-sheet filename template",
              "default": "{number} - {name}",
              "hint": "Tokens: {number} {name} {rev} {project} {date}"},
             {"key": "combined", "label": "Combined PDF filename",
              "default": "{0}_{1}".format(sanitize(doc.Title),
                                          DateTime.Now.ToString("yyyy-MM-dd"))}],
            owner=self, ok_label="Export")
        if not vals:
            return
        folder = forms.pick_folder(title="Choose export folder")
        if not folder:
            return

        sheets = [r.sheet for r in rows]
        if vals["mode"] == "One PDF per sheet":
            done, fail = export_pdf(doc, sheets, folder,
                                    template=vals["template"] or "{number} - {name}")
        else:
            done, fail = export_pdf(doc, sheets, folder, combine=True,
                                    combined_name=vals["combined"])

        msg = "Exported {0} file(s) to:\n{1}".format(len(done), folder)
        if fail:
            msg += "\n\nFailures ({0}):\n{1}".format(len(fail), "\n".join(fail))
        if dialogs.confirm("Export PDF", msg + "\n\nOpen the folder?",
                           owner=self, ok_label="Open folder",
                           cancel_label="Close"):
            open_path(folder)

    def on_export_csv(self, sender, args):
        path = forms.save_file(file_ext="csv")
        if not path:
            return
        with codecs.open(path, "w", "utf-8-sig") as f:
            f.write(u"Number,Name,CurrentRev,Views,Placeholder\r\n")
            for r in self.all_rows:
                f.write(u'"{0}","{1}","{2}",{3},{4}\r\n'.format(
                    r.Number, r.Name, r.CurrentRev, r.ViewCount, r.IsPlaceholder))
        dialogs.message("Export CSV",
                        "Exported {0} sheets to:\n{1}".format(
                            len(self.all_rows), path), owner=self)


if not doc:
    forms.alert("Open a Revit document first.", exitscript=True)

SheetManagerWindow(XAML).show_dialog()
