# -*- coding: utf-8 -*-
__title__  = "Issue Manager"
__author__ = "Sendit"
__doc__    = """Issue Manager

Issue and archive drawings:
- exports issued sheets as PDFs into a dated folder inside a per-project
  archive location
- optionally exports matching DWG files for every issued sheet (same
  filenames, using a saved DWG export setup or Revit defaults)
- maintains a running issue register CSV (one row per sheet per issue)
- shows the issue history as a sheets-vs-issues matrix
- creates a 'Drawing Issue Register' sheet-list schedule in Revit and can
  export it to PDF

The window follows Revit's Dark/Light theme (Revit 2024+)."""

import os
import json
import codecs

import clr
clr.AddReference("System.Data")

from pyrevit import revit, DB, forms, script

from System import DateTime
from System.Collections.ObjectModel import ObservableCollection
from System.Collections.Generic import List
from System.Data import DataTable

from sendit import theme, dialogs, register
from sendit.bindable import Notifiable
from sendit.shell import open_path
from sendit.sheets import (collect_sheets, get_current_rev, sanitize,
                             export_pdf, export_dwg)

doc    = revit.doc
logger = script.get_logger()

SCHEDULE_NAME = "Drawing Issue Register"
SCHEDULE_FIELDS = ["Sheet Number", "Sheet Name", "Current Revision",
                   "Current Revision Date", "Current Revision Description",
                   "Current Revision Issued By", "Current Revision Issued To"]
TRANSMITTAL_FILENAME = "_issued_documents.csv"
DWG_DEFAULT_LABEL = u"« Revit defaults »"


def list_dwg_setups():
    """Saved DWG export setups in this model (name -> ExportDWGSettings)."""
    setups = {}
    try:
        for s in (DB.FilteredElementCollector(doc)
                    .OfClass(DB.ExportDWGSettings)
                    .ToElements()):
            try:
                setups[s.Name] = s
            except Exception:
                pass
    except Exception:
        pass
    return setups


def map_results(rows, done, fail):
    """Map per-sheet export results back onto rows.

    done/fail come from export_pdf/export_dwg in row order; fail entries
    are '<number>: <error>'. Returns dict[number] = filename or ''.
    """
    failed = set(f.split(":")[0] for f in fail)
    mapping, di = {}, 0
    for r in rows:
        if r.Number in failed:
            mapping[r.Number] = u""
        elif di < len(done):
            mapping[r.Number] = done[di]
            di += 1
    return mapping


# ---------------------------------------------------------------------------
# Per-document settings (archive path, last-used values)
# ---------------------------------------------------------------------------
def config_path():
    return script.get_document_data_file("issuemgr", "json")


def load_config():
    try:
        with codecs.open(config_path(), "r", "utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with codecs.open(config_path(), "w", "utf-8") as f:
            f.write(json.dumps(cfg))
    except Exception as ex:
        logger.error("could not save issue manager config: %s", ex)


# ---------------------------------------------------------------------------
# Revit sheet-list schedule ("Drawing Issue Register")
# ---------------------------------------------------------------------------
def find_schedule():
    for vs in (DB.FilteredElementCollector(doc)
                 .OfClass(DB.ViewSchedule)
                 .ToElements()):
        try:
            if not vs.IsTemplate and vs.Name == SCHEDULE_NAME:
                return vs
        except Exception:
            pass
    return None


def ensure_schedule():
    """Find or create the issue register sheet-list schedule.

    Returns:
        (schedule, created)
    """
    vs = find_schedule()
    if vs:
        return vs, False
    with revit.Transaction("Issue Manager: create issue schedule"):
        vs = DB.ViewSchedule.CreateSheetList(doc)
        try:
            vs.Name = SCHEDULE_NAME
        except Exception:
            pass
        defn = vs.Definition
        existing = set()
        for i in range(defn.GetFieldCount()):
            try:
                existing.add(defn.GetField(i).GetName())
            except Exception:
                pass
        available = {}
        for sf in defn.GetSchedulableFields():
            try:
                available[sf.GetName(doc)] = sf
            except Exception:
                pass
        for name in SCHEDULE_FIELDS:
            if name in existing:
                continue
            sf = available.get(name)
            if sf is not None:
                try:
                    defn.AddField(sf)
                except Exception:
                    pass
        for i in range(defn.GetFieldCount()):
            fld = defn.GetField(i)
            try:
                if fld.GetName() == "Sheet Number":
                    defn.AddSortGroupField(
                        DB.ScheduleSortGroupField(fld.FieldId))
                    break
            except Exception:
                pass
    return vs, True


def export_schedule_pdf(schedule, folder):
    """Export the schedule view to PDF. Returns the created filename."""
    opts = DB.PDFExportOptions()
    opts.Combine = True
    fname = sanitize(u"{0} {1}".format(SCHEDULE_NAME,
                                       DateTime.Now.ToString("yyyy-MM-dd")))
    opts.FileName = fname
    ids = List[DB.ElementId]([schedule.Id])
    if not doc.Export(folder, ids, opts):
        raise Exception("Revit PDF export returned False")
    return fname + ".pdf"


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------
class IssueRow(Notifiable):
    def __init__(self, sheet):
        Notifiable.__init__(self)
        self.sheet = sheet
        self._selected = False
        self.Number = sheet.SheetNumber
        self.Name = sheet.Name
        self.CurrentRev = get_current_rev(sheet)
        self._last_date = u""
        self._last_issue = u""
        self._last_rev = u""

    @property
    def Selected(self):
        return self._selected

    @Selected.setter
    def Selected(self, v):
        self._selected = bool(v); self._notify("Selected")

    @property
    def LastIssued(self):
        return self._last_date

    @LastIssued.setter
    def LastIssued(self, v):
        self._last_date = v; self._notify("LastIssued")

    @property
    def LastIssue(self):
        return self._last_issue

    @LastIssue.setter
    def LastIssue(self, v):
        self._last_issue = v; self._notify("LastIssue")

    @property
    def LastRevIssued(self):
        return self._last_rev

    @LastRevIssued.setter
    def LastRevIssued(self, v):
        self._last_rev = v; self._notify("LastRevIssued")


# ---------------------------------------------------------------------------
# Issue history (matrix) window
# ---------------------------------------------------------------------------
HISTORY_XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Issue History" Height="560" Width="940"
        MinHeight="360" MinWidth="640"
        WindowStartupLocation="CenterScreen"
        UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
  <DockPanel Margin="10">
    <DockPanel DockPanel.Dock="Bottom" Margin="0,10,0,0">
      <Button x:Name="h_close" Content="Close" Width="80" DockPanel.Dock="Right"/>
      <Button x:Name="h_export" Content="Export matrix CSV..." Width="150"
              DockPanel.Dock="Right" Margin="0,0,8,0"
              ToolTip="Save the sheets-vs-issues matrix as a CSV file"/>
      <TextBlock x:Name="h_status" VerticalAlignment="Center"
                 Foreground="{DynamicResource FgMuted}"/>
    </DockPanel>
    <DataGrid x:Name="hist_grid" AutoGenerateColumns="True" IsReadOnly="True"
              CanUserSortColumns="True"/>
  </DockPanel>
</Window>
"""


class HistoryWindow(forms.WPFWindow):
    def __init__(self, xaml, rows):
        forms.WPFWindow.__init__(self, xaml, literal_string=True)
        theme.style_window(self)
        self.issues, self.sheets, self.cells = register.pivot(rows)
        self.populate()
        self.h_export.Click += self.on_export
        self.h_close.Click += lambda s, a: self.Close()

    @staticmethod
    def _safe_caption(text):
        # DataGrid auto-generated columns bind by column name; strip the
        # characters WPF binding paths treat as operators
        for ch in u"./[](),":
            text = text.replace(ch, u" ")
        return u" ".join(text.split())

    def column_captions(self):
        captions, used = [], set()
        for (date, issue) in self.issues:
            cap = self._safe_caption(u"{0}  {1}".format(date, issue))
            base, n = cap, 2
            while cap in used or cap in ("Number", "Name"):
                cap = u"{0} ({1})".format(base, n)
                n += 1
            used.add(cap)
            captions.append(cap)
        return captions

    def populate(self):
        dt = DataTable("Issues")
        dt.Columns.Add("Number")
        dt.Columns.Add("Name")
        captions = self.column_captions()
        for cap in captions:
            dt.Columns.Add(cap)
        for skey in self.sheets:
            values = [skey[0], skey[1]]
            sheet_cells = self.cells.get(skey, {})
            for ikey in self.issues:
                if ikey in sheet_cells:
                    rev = sheet_cells[ikey]
                    values.append(rev if rev else u"✓")
                else:
                    values.append(u"")
            row = dt.NewRow()
            for i, v in enumerate(values):
                row[i] = v
            dt.Rows.Add(row)
        self.hist_grid.ItemsSource = dt.DefaultView
        self.h_status.Text = "{0} sheet(s) across {1} issue(s)".format(
            len(self.sheets), len(self.issues))

    def on_export(self, sender, args):
        path = forms.save_file(file_ext="csv")
        if not path:
            return
        captions = self.column_captions()
        with codecs.open(path, "w", "utf-8-sig") as f:
            f.write(register.csv_line(["Number", "Name"] + captions) + u"\r\n")
            for skey in self.sheets:
                sheet_cells = self.cells.get(skey, {})
                vals = [skey[0], skey[1]]
                for ikey in self.issues:
                    if ikey in sheet_cells:
                        rev = sheet_cells[ikey]
                        vals.append(rev if rev else u"✓")
                    else:
                        vals.append(u"")
                f.write(register.csv_line(vals) + u"\r\n")
        dialogs.message("Export matrix",
                        "Saved issue matrix to:\n{0}".format(path), owner=self)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------
XAML = """
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Issue Manager" Height="720" Width="1020"
        MinHeight="560" MinWidth="900"
        WindowStartupLocation="CenterScreen"
        UseLayoutRounding="True" TextOptions.TextFormattingMode="Display">
  <DockPanel Margin="10">

    <Border DockPanel.Dock="Top" BorderBrush="{DynamicResource Border}"
            Background="{DynamicResource PanelBg}" BorderThickness="1"
            CornerRadius="4" Padding="10" Margin="0,0,0,8">
      <DockPanel>
        <TextBlock Text="Archive folder" FontWeight="SemiBold"
                   VerticalAlignment="Center" Margin="0,0,12,0"/>
        <Button x:Name="open_btn" Content="Open" DockPanel.Dock="Right"
                Margin="6,0,0,0" ToolTip="Open the archive folder in Explorer"/>
        <Button x:Name="browse_btn" Content="Browse..." DockPanel.Dock="Right"
                Margin="8,0,0,0"
                ToolTip="Choose where issues for this project are archived"/>
        <TextBox x:Name="archive_box" IsReadOnly="True"
                 Foreground="{DynamicResource FgMuted}"/>
      </DockPanel>
    </Border>

    <Border DockPanel.Dock="Top" BorderBrush="{DynamicResource Border}"
            Background="{DynamicResource PanelBg}" BorderThickness="1"
            CornerRadius="4" Padding="10" Margin="0,0,0,8">
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
          <ColumnDefinition Width="*"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
        </Grid.RowDefinitions>
        <TextBlock Grid.Row="0" Grid.Column="0" Text="Issue name"
                   VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="issue_name" Grid.Row="0" Grid.Column="1"
                 Margin="0,0,16,6"
                 ToolTip="e.g. 'Planning', 'Tender', 'Construction Rev B'"/>
        <TextBlock Grid.Row="0" Grid.Column="2" Text="Issued to"
                   VerticalAlignment="Center" Margin="0,0,8,6"/>
        <TextBox x:Name="issued_to" Grid.Row="0" Grid.Column="3" Margin="0,0,0,6"/>
        <TextBlock Grid.Row="1" Grid.Column="0" Text="Filename template"
                   VerticalAlignment="Center" Margin="0,0,8,0"/>
        <TextBox x:Name="template_box" Grid.Row="1" Grid.Column="1"
                 Margin="0,0,16,0"
                 ToolTip="Tokens: {number} {name} {rev} {project} {date} {issue}"/>
        <StackPanel Grid.Row="1" Grid.Column="3" Orientation="Horizontal">
          <RadioButton x:Name="mode_persheet" Content="One PDF per sheet"
                       GroupName="outmode" IsChecked="True" Margin="0,0,14,0"/>
          <RadioButton x:Name="mode_combined" Content="Single combined PDF"
                       GroupName="outmode"/>
        </StackPanel>
        <CheckBox x:Name="dwg_check" Grid.Row="2" Grid.Column="1"
                  Content="Also export DWG files" Margin="0,10,16,0"
                  ToolTip="Export a DWG of every issued sheet alongside the PDFs, using the same filenames"/>
        <Button x:Name="dwg_setup_btn" Grid.Row="2" Grid.Column="3"
                Content="DWG setup: Revit defaults" HorizontalAlignment="Left"
                Margin="0,7,0,0"
                ToolTip="Pick a saved DWG export setup from this model (managed via Revit's 'Export &gt; CAD Formats' dialog)"/>
      </Grid>
    </Border>

    <WrapPanel DockPanel.Dock="Top" Margin="0,0,0,8">
      <Button x:Name="issue_btn" Content="Issue &amp; archive" Margin="0,0,6,4"
              Style="{DynamicResource AccentButton}"
              ToolTip="Export the ticked sheets as PDFs into a dated archive folder and record them in the issue register"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="hist_btn" Content="History..." Margin="0,0,6,4"
              ToolTip="Show the issue register as a sheets-vs-issues matrix"/>
      <Button x:Name="reg_btn" Content="Open register CSV" Margin="0,0,6,4"
              ToolTip="Open issue_register.csv from the archive folder"/>
      <Border Width="1" Height="22" Background="{DynamicResource Border}" Margin="4,3,10,7"/>
      <Button x:Name="sched_btn" Content="Update Revit schedule" Margin="0,0,6,4"
              ToolTip="Create the 'Drawing Issue Register' sheet-list schedule in this model"/>
      <Button x:Name="schedpdf_btn" Content="Export schedule PDF..." Margin="0,0,6,4"
              ToolTip="Export the 'Drawing Issue Register' schedule to PDF"/>
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
      <TextBlock x:Name="status" Foreground="{DynamicResource FgMuted}"/>
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
        <DataGridTextColumn Header="Number" Binding="{Binding Number}" Width="120" IsReadOnly="True"/>
        <DataGridTextColumn Header="Sheet Name" Binding="{Binding Name}" Width="*" IsReadOnly="True"/>
        <DataGridTextColumn Header="Cur Rev" Binding="{Binding CurrentRev}" Width="66" IsReadOnly="True"/>
        <DataGridTextColumn Header="Last Issued" Binding="{Binding LastIssued}" Width="96" IsReadOnly="True"/>
        <DataGridTextColumn Header="Last Issue" Binding="{Binding LastIssue}" Width="150" IsReadOnly="True"/>
        <DataGridTextColumn Header="Rev Issued" Binding="{Binding LastRevIssued}" Width="80" IsReadOnly="True"/>
      </DataGrid.Columns>
    </DataGrid>
  </DockPanel>
</Window>
"""


class IssueManagerWindow(forms.WPFWindow):
    def __init__(self, xaml):
        forms.WPFWindow.__init__(self, xaml, literal_string=True)
        theme.style_window(self)
        self.cfg = load_config()
        self.all_rows = []

        self.archive_box.Text = self.cfg.get("archive", "")
        self.issued_to.Text = self.cfg.get("issued_to", "")
        self.template_box.Text = self.cfg.get(
            "template", "{number} - {name} - {rev}")
        self.dwg_check.IsChecked = bool(self.cfg.get("dwg"))
        self.refresh_dwg_button()

        self.reload()

        self.dwg_check.Click += lambda s, a: self.refresh_dwg_button()
        self.dwg_setup_btn.Click += self.on_pick_dwg_setup
        self.browse_btn.Click += self.on_browse
        self.open_btn.Click += self.on_open_archive
        self.issue_btn.Click += self.on_issue
        self.hist_btn.Click += self.on_history
        self.reg_btn.Click += self.on_open_register
        self.sched_btn.Click += self.on_update_schedule
        self.schedpdf_btn.Click += self.on_export_schedule
        self.search_box.TextChanged += lambda s, a: self.apply_filter()
        self.select_all.Click += self.on_select_all

    # ---- data ----
    def archive_folder(self):
        return (self.archive_box.Text or "").strip()

    def register_rows(self):
        folder = self.archive_folder()
        if not folder:
            return []
        return register.read_rows(register.register_path(folder))

    def reload(self):
        self.all_rows = [IssueRow(s) for s in collect_sheets(doc)]
        for r in self.all_rows:
            r.add_PropertyChanged(self.on_row_changed)
        self.refresh_last_issued()
        self.apply_filter()

    def refresh_last_issued(self):
        latest = register.last_issued(self.register_rows())
        for r in self.all_rows:
            info = latest.get(r.Number)
            r.LastIssued = info["date"] if info else u""
            r.LastIssue = info["issue"] if info else u""
            r.LastRevIssued = info["revision"] if info else u""

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

    def refresh_status(self):
        self.status.Text = "{0} sheets   |   {1} shown   |   {2} ticked".format(
            len(self.all_rows), len(self.visible_rows()),
            len(self.selected_rows()))

    def on_row_changed(self, sender, args):
        if args.PropertyName == "Selected":
            self.refresh_status()

    def on_select_all(self, sender, args):
        state = bool(self.select_all.IsChecked)
        for r in self.visible_rows():
            r.Selected = state

    # ---- archive folder ----
    def on_browse(self, sender, args):
        folder = forms.pick_folder(title="Choose the issue archive folder "
                                         "for this project")
        if not folder:
            return
        self.archive_box.Text = folder
        self.cfg["archive"] = folder
        save_config(self.cfg)
        self.refresh_last_issued()

    def require_archive(self):
        folder = self.archive_folder()
        if folder and os.path.isdir(folder):
            return folder
        if folder and not os.path.isdir(folder):
            try:
                os.makedirs(folder)
                return folder
            except Exception:
                pass
        dialogs.message("Archive folder",
                        "Choose an archive folder first (Browse...).",
                        owner=self)
        return None

    def on_open_archive(self, sender, args):
        folder = self.archive_folder()
        if folder and os.path.isdir(folder):
            open_path(folder)
        else:
            dialogs.message("Archive folder", "No archive folder set yet.",
                            owner=self)

    # ---- DWG export setup ----
    def refresh_dwg_button(self):
        name = self.cfg.get("dwg_setup") or ""
        self.dwg_setup_btn.Content = "DWG setup: {0}".format(
            name if name else "Revit defaults")
        self.dwg_setup_btn.IsEnabled = bool(self.dwg_check.IsChecked)

    def on_pick_dwg_setup(self, sender, args):
        setups = sorted(list_dwg_setups().keys())
        choice = dialogs.pick_item(
            "DWG export setup", [DWG_DEFAULT_LABEL] + setups, owner=self,
            message="Saved setups come from Revit's DWG export dialog "
                    "(File > Export > CAD Formats > Modify Export Setups). "
                    "'Revit defaults' merges each sheet's views into a "
                    "single DWG.")
        if choice is None:
            return
        self.cfg["dwg_setup"] = "" if choice == DWG_DEFAULT_LABEL else choice
        save_config(self.cfg)
        self.refresh_dwg_button()

    def current_dwg_options(self):
        name = self.cfg.get("dwg_setup") or ""
        if name:
            settings = list_dwg_setups().get(name)
            if settings is not None:
                try:
                    return settings.GetDWGExportOptions()
                except Exception as ex:
                    logger.error("DWG setup '%s' unusable, using defaults: %s",
                                 name, ex)
        return None  # export_dwg falls back to merged-view defaults

    # ---- issuing ----
    def on_issue(self, sender, args):
        rows = self.selected_rows()
        if not rows:
            dialogs.message("Issue", "Tick the sheets to issue.", owner=self)
            return
        folder = self.require_archive()
        if not folder:
            return
        issue_name = (self.issue_name.Text or "").strip()
        if not issue_name:
            dialogs.message("Issue", "Enter an issue name "
                            "(e.g. 'Planning', 'Tender').", owner=self)
            return
        issued_to = (self.issued_to.Text or "").strip()
        template = (self.template_box.Text or "").strip() or "{number} - {name} - {rev}"
        combine = bool(self.mode_combined.IsChecked)

        date_str = DateTime.Now.ToString("yyyy-MM-dd")
        time_str = DateTime.Now.ToString("HH:mm")
        issue_folder = os.path.join(
            folder, sanitize(u"{0} - {1}".format(date_str, issue_name)))
        if os.path.isdir(issue_folder):
            if not dialogs.confirm(
                    "Issue",
                    "An archive folder for this issue already exists:\n{0}\n\n"
                    "Existing PDFs with the same names will be overwritten "
                    "and the register will gain new rows.".format(issue_folder),
                    owner=self, ok_label="Continue"):
                return
        else:
            try:
                os.makedirs(issue_folder)
            except Exception as ex:
                dialogs.message("Issue", "Could not create folder:\n{0}\n\n{1}"
                                .format(issue_folder, ex), owner=self)
                return

        do_dwg = bool(self.dwg_check.IsChecked)
        fmt_desc = "PDFs and DWGs" if do_dwg else "PDFs"
        if not dialogs.confirm(
                "Issue & archive",
                "Issue {0} sheet(s) as '{1}'?\n\n{2} will be saved to:\n{3}"
                .format(len(rows), issue_name, fmt_desc, issue_folder),
                owner=self, ok_label="Issue"):
            return

        sheets = [r.sheet for r in rows]
        extra = {"{issue}": issue_name}

        if combine:
            combined_name = sanitize(u"{0} - {1} - {2}".format(
                doc.Title, issue_name, date_str))
            pdf_done, pdf_fail = export_pdf(doc, sheets, issue_folder,
                                            combine=True,
                                            combined_name=combined_name)
            pdf_file_for = dict((r.Number, pdf_done[0] if pdf_done else u"")
                                for r in rows)
        else:
            pdf_done, pdf_fail = export_pdf(doc, sheets, issue_folder,
                                            template=template,
                                            extra_tokens=extra)
            pdf_file_for = map_results(rows, pdf_done, pdf_fail)

        dwg_file_for, dwg_done, dwg_fail = {}, [], []
        if do_dwg:
            dwg_done, dwg_fail = export_dwg(doc, sheets, issue_folder,
                                            template=template,
                                            extra_tokens=extra,
                                            options=self.current_dwg_options())
            dwg_file_for = map_results(rows, dwg_done, dwg_fail)

        # register rows + per-issue transmittal (only sheets that exported
        # in at least one format)
        reg_rows, trans_rows = [], []
        for r in rows:
            pdf_name = pdf_file_for.get(r.Number, u"")
            dwg_name = dwg_file_for.get(r.Number, u"")
            if not pdf_name and not dwg_name:
                continue
            formats = u", ".join(
                fmt for fmt, name in (("PDF", pdf_name), ("DWG", dwg_name))
                if name)
            rev = get_current_rev(r.sheet)
            fname = pdf_name or dwg_name
            reg_rows.append([date_str, time_str, issue_name, issued_to,
                             doc.Title, r.Number, r.Name, rev, fname, formats])
            trans_rows.append([r.Number, r.Name, rev, fname, formats])

        if reg_rows:
            register.append_rows(register.register_path(folder), reg_rows)
            trans_path = os.path.join(issue_folder, TRANSMITTAL_FILENAME)
            is_new = not os.path.exists(trans_path)
            with codecs.open(trans_path, "a" if not is_new else "w",
                             "utf-8-sig" if is_new else "utf-8") as f:
                if is_new:
                    f.write(register.csv_line(
                        ["SheetNumber", "SheetName", "Revision", "File",
                         "Formats"]) + u"\r\n")
                for tr in trans_rows:
                    f.write(register.csv_line(tr) + u"\r\n")

        self.cfg["issued_to"] = issued_to
        self.cfg["template"] = template
        self.cfg["dwg"] = do_dwg
        save_config(self.cfg)
        self.refresh_last_issued()

        msg = "Issued {0} of {1} sheet(s) to:\n{2}".format(
            len(reg_rows), len(rows), issue_folder)
        msg += "\n\nPDF files: {0}".format(len(pdf_done))
        if do_dwg:
            msg += "\nDWG files: {0}".format(len(dwg_done))
        if pdf_fail:
            msg += "\n\nPDF failures ({0}):\n{1}".format(
                len(pdf_fail), "\n".join(pdf_fail))
        if dwg_fail:
            msg += "\n\nDWG failures ({0}):\n{1}".format(
                len(dwg_fail), "\n".join(dwg_fail))
        if dialogs.confirm("Issue & archive", msg + "\n\nOpen the folder?",
                           owner=self, ok_label="Open folder",
                           cancel_label="Close"):
            open_path(issue_folder)

    # ---- history / register ----
    def on_history(self, sender, args):
        rows = self.register_rows()
        if not rows:
            dialogs.message("History",
                            "No issues recorded yet for this archive folder.",
                            owner=self)
            return
        HistoryWindow(HISTORY_XAML, rows).show_dialog()

    def on_open_register(self, sender, args):
        folder = self.archive_folder()
        path = register.register_path(folder) if folder else None
        if path and os.path.exists(path):
            open_path(path)
        else:
            dialogs.message("Register",
                            "No issue register yet - it is created on the "
                            "first issue.", owner=self)

    # ---- Revit schedule ----
    def on_update_schedule(self, sender, args):
        try:
            vs, created = ensure_schedule()
        except Exception as ex:
            dialogs.message("Schedule", "Could not create the schedule:\n{0}"
                            .format(ex), owner=self)
            return
        dialogs.message(
            "Schedule",
            ("Created" if created else "Found") +
            " schedule '{0}'.\n\nIt lists every sheet with its current "
            "revision data. Find it in the Project Browser under "
            "Schedules/Quantities.".format(SCHEDULE_NAME),
            owner=self)

    def on_export_schedule(self, sender, args):
        try:
            vs, created = ensure_schedule()
        except Exception as ex:
            dialogs.message("Schedule", "Could not create the schedule:\n{0}"
                            .format(ex), owner=self)
            return
        folder = self.archive_folder()
        if not folder or not os.path.isdir(folder):
            folder = forms.pick_folder(title="Choose a folder for the "
                                             "schedule PDF")
        if not folder:
            return
        try:
            fname = export_schedule_pdf(vs, folder)
        except Exception as ex:
            dialogs.message(
                "Schedule PDF",
                "Could not export the schedule to PDF:\n{0}\n\n"
                "You can still print it from Revit: open '{1}' in the "
                "Project Browser and use File > Print.".format(ex, SCHEDULE_NAME),
                owner=self)
            return
        if dialogs.confirm("Schedule PDF",
                           "Exported:\n{0}\\{1}\n\nOpen the folder?".format(
                               folder, fname),
                           owner=self, ok_label="Open folder",
                           cancel_label="Close"):
            open_path(folder)


if not doc:
    forms.alert("Open a Revit document first.", exitscript=True)

IssueManagerWindow(XAML).show_dialog()
