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

from sendit import theme, dialogs, bulk
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
    return dict((u"{0} : {1}".format(s.FamilyName, get_name(s)), s) for s in syms)


def ask_titleblock(owner):
    """Pick a title block. Returns (symbol_or_None, cancelled)."""
    syms = get_titleblock_symbols()
    items = [NO_TITLEBLOCK] + sorted(syms.keys())
    choice = dialogs.pick_item("Title block", items, owner=owner,
                               message=u"新图纸使用的图框：")
    if choice is None:
        return None, True
    return syms.get(choice), False


def ensure_active(symbol):
    if symbol and not symbol.IsActive:
        symbol.Activate()
        doc.Regenerate()


def new_sheet(tbid, number, name):
    """Create one sheet with the given number and name.

    If Revit rejects either value, the half-made sheet is deleted again so no
    sheet with a wrong number is left behind. Call inside a transaction.
    Returns (sheet, None) or (None, exception)."""
    sheet = None
    try:
        sheet = DB.ViewSheet.Create(doc, tbid)
        sheet.SheetNumber = number
        sheet.Name = name
        return sheet, None
    except Exception as ex:
        if sheet is not None:
            try:
                doc.Delete(sheet.Id)
            except Exception:
                pass
        return None, ex


def staged_edits(rows):
    """Staged number/name values that differ from the model.

    Returns {sheet id: {"Number": ..., "Name": ...}} with only the changed fields."""
    out = {}
    for r in rows:
        try:
            d = {}
            if r.Number != r.sheet.SheetNumber:
                d["Number"] = r.Number
            if r.Name != r.sheet.Name:
                d["Name"] = r.Name
            if d:
                out[r.sheet.Id.Value] = d
        except Exception:
            pass  # sheet no longer exists (deleted in this session)
    return out


def read_batch_rows(path):
    """Read 'number,name' lines from a CSV file. Returns a list of (number, name).

    Only the first comma separates the two, so a name may contain commas.
    utf-8-sig also strips the BOM that Excel writes."""
    items = []
    with codecs.open(path, "r", "utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",", 1)
            number = parts[0].strip()
            name = parts[1].strip() if len(parts) > 1 else "Unnamed"
            items.append((number, name))
    return items


def csv_cell(value):
    """Text for one quoted CSV field (doubles any double quote)."""
    text = u"" if value is None else u"{0}".format(value)
    return text.replace(u'"', u'""')


def listing(lines, limit=12):
    """Join message lines, showing at most `limit` of them."""
    text = u"\n".join(lines[:limit])
    if len(lines) > limit:
        text += u"\n... 另有 {0} 项".format(len(lines) - limit)
    return text


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

    def edits(self):
        """The edited values if any of them differ from the model, else None."""
        rev = self.rev
        try:
            changed = (self._date != rev.RevisionDate
                       or self._desc != rev.Description
                       or self._by != rev.IssuedBy
                       or self._to != rev.IssuedTo
                       or bool(self._issued) != bool(rev.Issued))
        except Exception:
            return None  # revision was deleted
        if not changed:
            return None
        return {"Date": self._date, "Description": self._desc,
                "IssuedBy": self._by, "IssuedTo": self._to,
                "Issued": self._issued}


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
        self.Closing += self.on_closing

    def reload(self, keep=None):
        """Re-read the revisions. Unapplied edits are kept unless `keep` is given."""
        if keep is None:
            keep = {}
            for r in self.rows:
                changed = r.edits()
                if changed:
                    keep[r.rev.Id.Value] = changed
        rev_ids = list(DB.Revision.GetAllRevisionIds(doc))
        self.rows = [RevRow(doc.GetElement(rid)) for rid in rev_ids]
        for r in self.rows:
            for name, value in keep.get(r.rev.Id.Value, {}).items():
                setattr(r, name, value)
        coll = ObservableCollection[object]()
        for r in self.rows:
            coll.Add(r)
        self.rev_grid.ItemsSource = coll

    def checked(self):
        return [r for r in self.rows if r.Selected]

    def on_add(self, sender, args):
        try:
            with revit.Transaction("Add revision"):
                rev = DB.Revision.Create(doc)
                rev.Description = "New Revision"
                rev.RevisionDate = DateTime.Now.ToString("yyyy-MM-dd")
        except Exception as ex:
            dialogs.message("Add revision", u"无法新建修订：{0}".format(ex), owner=self)
        self.reload()

    def on_apply(self, sender, args):
        errs = []
        try:
            with revit.Transaction("Edit revisions"):
                for r in self.rows:
                    try:
                        r.rev.RevisionDate = r.Date
                        r.rev.Description = r.Description
                        r.rev.IssuedBy = r.IssuedBy
                        r.rev.IssuedTo = r.IssuedTo
                        r.rev.Issued = r.Issued
                    except Exception as ex:
                        errs.append(u"修订 Seq {0}：{1}".format(r.Seq, ex))
        except Exception as ex:
            errs.append(u"写入模型失败，修订都未保存：{0}".format(ex))
        if errs:
            # keep the typed values on screen (reload() keeps unapplied edits)
            self.reload()
            dialogs.message("Revision edits",
                            u"以下修订未保存（修改仍保留在表格中，可修正后再次点击 Apply edits）：\n\n{0}".format(
                                listing(errs)),
                            owner=self)
        else:
            self.reload(keep={})

    def move(self, delta):
        sel = self.rev_grid.SelectedItem
        if sel is None:
            dialogs.message("Reorder", u"请先在表格中选中一行修订。", owner=self)
            return
        ids = list(DB.Revision.GetAllRevisionIds(doc))
        idx = next((i for i, rid in enumerate(ids) if rid.Value == sel.rev.Id.Value), -1)
        new = idx + delta
        if idx < 0 or new < 0 or new >= len(ids):
            return
        ids[idx], ids[new] = ids[new], ids[idx]
        try:
            with revit.Transaction("Reorder revisions"):
                DB.Revision.ReorderRevisionSequence(doc, List[DB.ElementId](ids))
        except Exception as ex:
            dialogs.message("Reorder", u"无法调整顺序：{0}".format(ex), owner=self)
        self.reload()

    def on_delete(self, sender, args):
        rows = self.checked()
        if not rows:
            dialogs.message("Delete revisions", u"请先勾选要删除的修订。", owner=self)
            return
        if not dialogs.confirm("Delete revisions",
                               u"删除 {0} 个修订？".format(len(rows)),
                               owner=self, ok_label="Delete"):
            return
        failed = []
        try:
            with revit.Transaction("Delete revisions"):
                for r in rows:
                    try:
                        doc.Delete(r.rev.Id)
                    except Exception as ex:
                        logger.error("delete rev %s: %s", r.Seq, ex)
                        failed.append(u"Seq {0}：{1}".format(r.Seq, ex))
        except Exception as ex:
            failed.append(u"写入模型失败：{0}".format(ex))
        self.reload()
        if failed:
            dialogs.message("Delete revisions",
                            u"以下修订没有删除（仍保留在模型中）：\n\n{0}".format(listing(failed)),
                            owner=self)

    def on_closing(self, sender, args):
        # closing with unapplied edits would silently throw them away
        if any(r.edits() for r in self.rows) and not dialogs.confirm(
                "Revisions", u"有未应用的修订修改，关闭后会丢失。\n\n仍要关闭吗？",
                owner=self, ok_label="Discard", cancel_label="Keep editing"):
            args.Cancel = True


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
    def reload(self, keep=None):
        """Re-read the sheets from the model.

        Staged edits are carried over, so Add / Duplicate / Delete no longer
        drop them. Pass keep={} to start clean, or a dict from staged_edits()
        to keep only some edits (Apply edits uses this for what it could not write)."""
        if keep is None:
            keep = staged_edits(self.all_rows)
        self.all_rows = [SheetRow(s) for s in collect_sheets(doc)]
        for r in self.all_rows:
            r.add_PropertyChanged(self.on_row_changed)
            d = keep.get(r.sheet.Id.Value)
            if d:
                if "Number" in d:
                    r.Number = d["Number"]
                if "Name" in d:
                    r.Name = d["Name"]
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
            u"{0} 项修改未写入模型 - 点击 Apply edits 写入".format(staged)
            if staged else "")

    def say(self, text):
        """One-line result in the status bar. The counts come back on the next refresh."""
        self.status.Text = text

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
        written = 0
        unwritten = {}  # edits that did not reach the model; they stay staged
        try:
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
                    sid = r.sheet.Id.Value
                    if r in originals:
                        try:
                            r.sheet.SheetNumber = r.Number
                            written += 1
                        except Exception as ex:
                            unwritten.setdefault(sid, {})["Number"] = r.Number
                            try:
                                r.sheet.SheetNumber = originals[r]
                                errors.append(u"编号 '{0}' 未写入（原编号不变）：{1}".format(r.Number, ex))
                            except Exception:
                                errors.append(u"编号 '{0}' 未写入，原编号也无法恢复（当前为临时编号），请修正后重试：{1}".format(
                                    r.Number, ex))
                    if r.Name != r.sheet.Name:
                        try:
                            r.sheet.Name = r.Name
                            written += 1
                        except Exception as ex:
                            unwritten.setdefault(sid, {})["Name"] = r.Name
                            errors.append(u"名称 '{0}' 未写入：{1}".format(r.Name, ex))
        except Exception as ex:
            # the whole transaction failed, so nothing from this batch reached the model
            written = 0
            unwritten = staged_edits(changes)
            errors = [u"写入模型失败，本次修改全部未写入：{0}".format(ex)]
        self.reload(keep=unwritten)
        if errors:
            dialogs.message("Apply edits",
                            u"已写入 {0} 项修改，{1} 项未写入（未写入的仍保留在表格中）：\n\n{2}".format(
                                written, len(errors), listing(errors)),
                            owner=self)
        else:
            self.say(u"已写入 {0} 项修改。".format(written))

    def on_renumber(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Renumber", u"请先勾选图纸。", owner=self)
            return
        vals = dialogs.ask_form(
            "Renumber {0} sheet(s)".format(len(rows)),
            [{"key": "prefix", "label": "Prefix", "default": ""},
             {"key": "start", "label": "Start number", "type": "int", "default": 1,
              "hint": u"请输入整数"},
             {"key": "pad", "label": "Zero-pad to N digits (0 = none)",
              "type": "int", "default": "0", "hint": u"请输入整数，0 表示不补零"},
             {"key": "suffix", "label": "Suffix", "default": ""}],
            owner=self,
            message=u"新编号先暂存在表格中，点击 'Apply edits' 后才写入模型。")
        if not vals:
            return
        n = vals["start"]
        for r in sorted(rows, key=lambda x: x.Number):
            r.Number = u"{0}{1}{2}".format(vals["prefix"],
                                          str(n).zfill(vals["pad"]),
                                          vals["suffix"])
            n += 1

    def on_find_replace(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Find / Replace", u"请先勾选图纸。", owner=self)
            return
        vals = bulk.ask_find_replace(rows, "sheet(s)", owner=self,
                                     scopes=["Number", "Name", "Both"],
                                     default_scope="Name")
        if not vals:
            return
        attrs = bulk.scope_attrs(vals["scope"], {"Number": ["Number"],
                                                 "Name": ["Name"],
                                                 "Both": ["Number", "Name"]})
        changed = bulk.replace_in_rows(rows, vals["find"], vals["repl"], attrs)
        if changed:
            dialogs.message("Find / Replace",
                            u"已暂存 {0} 处替换（未写入模型，点击 Apply edits 写入）。".format(changed),
                            owner=self)
        else:
            dialogs.message("Find / Replace",
                            u"选中的 {0} 张图纸中没有找到 '{1}'，没有任何修改。".format(
                                len(rows), vals["find"]),
                            owner=self)

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
        err = None
        try:
            with revit.Transaction("Sheet Manager: add sheet"):
                ensure_active(sym)
                tbid = sym.Id if sym else DB.ElementId.InvalidElementId
                _sheet, err = new_sheet(tbid, vals["number"], vals["name"] or "Unnamed")
        except Exception as ex:
            err = ex
        self.reload()
        if err is not None:
            dialogs.message("Add sheet",
                            u"未创建图纸（编号 '{0}'）：{1}".format(vals["number"], err),
                            owner=self)
        else:
            self.say(u"已添加图纸 '{0}'。".format(vals["number"]))

    def on_batch(self, sender, args):
        path = forms.pick_file(file_ext="csv")
        if not path:
            return
        try:
            items = read_batch_rows(path)
        except UnicodeDecodeError:
            dialogs.message("Batch create",
                            u"无法读取 CSV：文件不是 UTF-8 编码。\n请用 Excel 或记事本另存为 UTF-8 格式后重试。",
                            owner=self)
            return
        except Exception as ex:
            dialogs.message("Batch create", u"无法读取 CSV：{0}".format(ex), owner=self)
            return
        if not items:
            dialogs.message("Batch create", u"CSV 中没有可用的行（每行格式：编号,名称）。",
                            owner=self)
            return
        sym, cancelled = ask_titleblock(self)
        if cancelled:
            return
        created, errors, remaining = 0, [], 0
        try:
            with revit.Transaction("Sheet Manager: batch create"):
                ensure_active(sym)
                tbid = sym.Id if sym else DB.ElementId.InvalidElementId
                with forms.ProgressBar(title=u"正在创建图纸... ({value}/{max_value})",
                                       cancellable=True) as pb:
                    for i, (number, name) in enumerate(items):
                        if pb.cancelled:
                            break
                        _sheet, err = new_sheet(tbid, number, name)
                        if err is None:
                            created += 1
                        else:
                            errors.append(u"{0}: {1}".format(number, err))
                        pb.update_progress(i + 1, len(items))
                remaining = len(items) - created - len(errors)
        except Exception as ex:
            created, errors, remaining = 0, [u"写入模型失败，没有创建任何图纸：{0}".format(ex)], 0
        self.reload()
        msg = u"已创建 {0} 张图纸。".format(created)
        if errors:
            msg += u"\n\n{0} 行失败（这些行没有创建图纸）：\n{1}".format(len(errors), listing(errors))
        if remaining:
            msg += u"\n\n已取消，剩余 {0} 行未处理。".format(remaining)
        dialogs.message("Batch create", msg, owner=self)

    def on_duplicate(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Duplicate", u"请先勾选图纸。", owner=self)
            return
        if not dialogs.confirm(
                "Duplicate",
                u"复制 {0} 张图纸？\n\n新图纸沿用原图纸的图框，不复制视口。".format(len(rows)),
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

        made, errors = 0, []
        try:
            with revit.Transaction("Sheet Manager: duplicate sheets"):
                for r in rows:
                    tbid = sheet_titleblock_typeid(r.sheet)
                    base = r.sheet.SheetNumber + "-COPY"
                    num, i = base, 1
                    while num in existing:
                        i += 1
                        num = u"{0}{1}".format(base, i)
                    existing.add(num)
                    _sheet, err = new_sheet(tbid, num, r.sheet.Name)
                    if err is None:
                        made += 1
                    else:
                        errors.append(u"{0}: {1}".format(num, err))
        except Exception as ex:
            made, errors = 0, [u"写入模型失败，没有复制任何图纸：{0}".format(ex)]
        self.reload()
        if errors:
            dialogs.message("Duplicate",
                            u"已复制 {0} 张图纸，{1} 张失败（失败的没有创建）：\n\n{2}".format(
                                made, len(errors), listing(errors)),
                            owner=self)
        else:
            self.say(u"已复制 {0} 张图纸（编号加 -COPY 后缀，视口不复制）。".format(made))

    def on_delete(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Delete", u"请先勾选要删除的图纸。", owner=self)
            return
        preview = u"\n".join(u"{0} - {1}".format(r.Number, r.Name)
                             for r in rows[:8])
        if len(rows) > 8:
            preview += u"\n... 另有 {0} 张".format(len(rows) - 8)
        if not dialogs.confirm("Delete sheets",
                               u"删除 {0} 张图纸？\n\n{1}".format(len(rows), preview),
                               owner=self, ok_label="Delete"):
            return
        deleted, errors, remaining = 0, [], 0
        try:
            with revit.Transaction("Sheet Manager: delete sheets"):
                with forms.ProgressBar(title=u"正在删除图纸... ({value}/{max_value})",
                                       cancellable=True) as pb:
                    for i, r in enumerate(rows):
                        if pb.cancelled:
                            break
                        try:
                            doc.Delete(r.sheet.Id)
                            deleted += 1
                        except Exception as ex:
                            logger.error("delete %s: %s", r.Number, ex)
                            errors.append(u"{0} - {1}：{2}".format(r.Number, r.Name, ex))
                        pb.update_progress(i + 1, len(rows))
                remaining = len(rows) - deleted - len(errors)
        except Exception as ex:
            deleted, errors, remaining = 0, [u"写入模型失败，没有删除任何图纸：{0}".format(ex)], 0
        self.reload()
        if errors or remaining:
            msg = u"已删除 {0} 张图纸。".format(deleted)
            if errors:
                msg += u"\n\n{0} 张没有删除（仍保留在模型中）：\n{1}".format(
                    len(errors), listing(errors))
            if remaining:
                msg += u"\n\n已取消，剩余 {0} 张未删除。".format(remaining)
            dialogs.message("Delete sheets", msg, owner=self)
        else:
            self.say(u"已删除 {0} 张图纸。".format(deleted))

    def on_add_revision(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Add revision", u"请先勾选图纸。", owner=self)
            return
        rev_ids = list(DB.Revision.GetAllRevisionIds(doc))
        if not rev_ids:
            dialogs.message("Add revision",
                            u"还没有任何修订，请先点击 'Revisions...' 创建。",
                            owner=self)
            return
        labels = {}
        for rid in rev_ids:
            rev = doc.GetElement(rid)
            labels[u"Seq {0} | {1} | {2}".format(
                rev.SequenceNumber, rev.RevisionDate, rev.Description)] = rid
        choice = dialogs.pick_item("Add revision to {0} sheet(s)".format(len(rows)),
                                   sorted(labels.keys()), owner=self)
        if not choice:
            return
        rev_id = labels[choice]
        added, skipped, errors = 0, 0, []
        try:
            with revit.Transaction("Sheet Manager: add revision"):
                for r in rows:
                    try:
                        addl = list(r.sheet.GetAdditionalRevisionIds())
                        if any(a.Value == rev_id.Value for a in addl):
                            skipped += 1
                            continue
                        addl.append(rev_id)
                        r.sheet.SetAdditionalRevisionIds(List[DB.ElementId](addl))
                        added += 1
                    except Exception as ex:
                        errors.append(u"{0}: {1}".format(r.Number, ex))
        except Exception as ex:
            added, skipped, errors = 0, 0, [u"写入模型失败，没有添加修订：{0}".format(ex)]
        self.reload()
        if errors:
            dialogs.message("Add revision",
                            u"已为 {0} 张图纸添加修订，{1} 张失败：\n\n{2}".format(
                                added, len(errors), listing(errors)),
                            owner=self)
        else:
            self.say(u"已为 {0} 张图纸添加修订（{1} 张已有该修订）。".format(added, skipped))

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
                dialogs.message("Sheet sets", u"请先勾选要放入图纸集的图纸。",
                                owner=self)
                return
            name = dialogs.ask_string("Sheet set", "Set name", default="New Set",
                                      owner=self)
            if not name:
                return
            try:
                save_sheet_set(name, [r.sheet for r in rows])
                dialogs.message("Sheet sets",
                                u"已保存图纸集 '{0}'，共 {1} 张图纸。".format(
                                    name, len(rows)), owner=self)
            except Exception as ex:
                dialogs.message("Sheet sets",
                                u"未能保存图纸集（名称可能已存在）：\n{0}".format(ex),
                                owner=self)

        elif action == "Tick sheets from a set":
            sets = list_sheet_sets()
            if not sets:
                dialogs.message("Sheet sets", u"模型中没有图纸集。", owner=self)
                return
            choice = dialogs.pick_item("Pick a set", sorted(sets.keys()),
                                       owner=self)
            if not choice:
                return
            ids = sheets_in_set(sets[choice])
            for r in self.all_rows:
                r.Selected = (r.sheet.Id.Value in ids)
            self.say(u"已按图纸集 '{0}' 勾选 {1} 张图纸。".format(
                choice, len(self.selected_rows())))

        elif action == "Delete a set":
            sets = list_sheet_sets()
            if not sets:
                dialogs.message("Sheet sets", u"模型中没有图纸集。", owner=self)
                return
            choice = dialogs.pick_item("Delete which set?", sorted(sets.keys()),
                                       owner=self)
            if not choice:
                return
            try:
                with revit.Transaction("Sheet Manager: delete sheet set"):
                    doc.Delete(sets[choice].Id)
            except Exception as ex:
                dialogs.message("Sheet sets",
                                u"无法删除图纸集 '{0}'：{1}".format(choice, ex),
                                owner=self)
                return
            self.say(u"已删除图纸集 '{0}'。".format(choice))

    def on_export(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Export PDF", u"请先勾选要导出的图纸。", owner=self)
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
              "default": u"{0}_{1}".format(sanitize(doc.Title),
                                           DateTime.Now.ToString("yyyy-MM-dd"))}],
            owner=self, ok_label="Export")
        if not vals:
            return
        folder = forms.pick_folder(title="Choose export folder")
        if not folder:
            return

        sheets = [r.sheet for r in rows]
        done, fail = [], []
        processed, cancelled = 0, False
        try:
            if vals["mode"] == "One PDF per sheet":
                template = vals["template"] or "{number} - {name}"
                with forms.ProgressBar(title=u"正在导出 PDF... ({value}/{max_value})",
                                       cancellable=True) as pb:
                    for s in sheets:
                        if pb.cancelled:
                            cancelled = True
                            break
                        ok_files, bad = export_pdf(doc, [s], folder, template=template)
                        done.extend(ok_files)
                        fail.extend(bad)
                        processed += 1
                        pb.update_progress(processed, len(sheets))
            else:
                # one Revit call for the whole set, so it cannot be cancelled part way
                with forms.ProgressBar(title=u"正在导出合并 PDF，请稍候（无法中途取消）...",
                                       cancellable=False):
                    done, fail = export_pdf(doc, sheets, folder, combine=True,
                                            combined_name=vals["combined"])
        except Exception as ex:
            fail.append(u"导出中断：{0}".format(ex))

        msg = u"已导出 {0} 个文件到:\n{1}".format(len(done), folder)
        if cancelled:
            msg += u"\n\n已取消，未导出 {0} 张图纸。".format(len(sheets) - processed)
        if fail:
            msg += u"\n\n失败 {0} 项:\n{1}".format(len(fail), listing(fail))
        if dialogs.confirm("Export PDF", msg + u"\n\n打开文件夹？",
                           owner=self, ok_label="Open folder",
                           cancel_label="Close"):
            open_path(folder)

    def on_export_csv(self, sender, args):
        path = forms.save_file(file_ext="csv")
        if not path:
            return
        staged = len(self.staged_rows())
        try:
            with codecs.open(path, "w", "utf-8-sig") as f:
                f.write(u"Number,Name,CurrentRev,Views,Placeholder\r\n")
                for r in self.all_rows:
                    f.write(u'"{0}","{1}","{2}",{3},{4}\r\n'.format(
                        csv_cell(r.Number), csv_cell(r.Name), csv_cell(r.CurrentRev),
                        r.ViewCount, r.IsPlaceholder))
        except Exception as ex:
            dialogs.message("Export CSV",
                            u"无法写入 CSV（文件可能正被 Excel 打开）：\n{0}".format(ex),
                            owner=self)
            return
        msg = u"已导出 {0} 张图纸到:\n{1}".format(len(self.all_rows), path)
        if staged:
            msg += u"\n\n注意：有 {0} 项修改尚未写入模型，CSV 中是表格里的新值。".format(staged)
        dialogs.message("Export CSV", msg, owner=self)


if not doc:
    forms.alert(u"请先打开一个 Revit 文档。", exitscript=True)

SheetManagerWindow(XAML).show_dialog()
