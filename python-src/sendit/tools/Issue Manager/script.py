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
                             export_pdf, export_dwg, render_template)

doc    = revit.doc
logger = script.get_logger()

SCHEDULE_NAME = "Drawing Issue Register"
SCHEDULE_FIELDS = ["Sheet Number", "Sheet Name", "Current Revision",
                   "Current Revision Date", "Current Revision Description",
                   "Current Revision Issued By", "Current Revision Issued To"]
TRANSMITTAL_FILENAME = "_issued_documents.csv"
TRANSMITTAL_HEADER = ["SheetNumber", "SheetName", "Revision", "File", "Formats"]
DWG_DEFAULT_LABEL = u"« Revit defaults »"
DEFAULT_TEMPLATE = "{number} - {name} - {rev}"
MAX_LISTED = 20  # long lists in dialogs are cut here; the full list is logged


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


# ---------------------------------------------------------------------------
# Naming and export helpers (shared by the PDF and DWG paths)
# ---------------------------------------------------------------------------
def today_str():
    return DateTime.Now.ToString("yyyy-MM-dd")


def err_text(ex):
    """Exception text as unicode (Revit messages may contain non-ASCII)."""
    try:
        return u"{0}".format(ex)
    except Exception:
        return u"{0}".format(repr(ex))


def issue_folder_name(date_str, issue_name):
    """Dated issue folder under the archive, e.g. '2026-10-13 - Planning'."""
    return sanitize(u"{0} - {1}".format(date_str, issue_name))


def combined_pdf_name(project, issue_name, date_str):
    """Single combined PDF, e.g. 'Project - Planning - 2026-10-13'."""
    return sanitize(u"{0} - {1} - {2}".format(project, issue_name, date_str))


def unique(items):
    out = []
    for it in items:
        if it not in out:
            out.append(it)
    return out


def listed(lines):
    """First MAX_LISTED lines, indented, with a count for the rest."""
    out = [u"  " + line for line in lines[:MAX_LISTED]]
    if len(lines) > MAX_LISTED:
        out.append(u"  ……另有 {0} 项，完整列表见日志".format(
            len(lines) - MAX_LISTED))
    return out


def export_per_sheet(exporter, rows, folder, **kwargs):
    """Run one sheets.py exporter (export_pdf or export_dwg) per row.

    One call per sheet means every file name and every error belongs to
    exactly one row, so nothing is matched back by position. A sheet that
    raises is recorded as a failure and the loop carries on.

    Returns:
        (file_for, done, reasons)
        file_for[number] = created file name, or u"" if none
        done             = created file names
        reasons[number]  = failure line, only for sheets with no file
    """
    file_for, done, reasons = {}, [], {}
    for r in rows:
        try:
            ok, bad = exporter(doc, [r.sheet], folder, **kwargs)
        except Exception as ex:
            ok, bad = [], [u"{0}: {1}".format(r.Number, err_text(ex))]
        if ok:
            file_for[r.Number] = ok[0]
            done.extend(ok)
        else:
            file_for[r.Number] = u""
            reasons[r.Number] = (bad[0] if bad
                                 else u"{0}: 未返回文件".format(r.Number))
    return file_for, done, reasons


def export_combined_pdf(rows, folder, combined_name):
    """One combined PDF for all rows. Same return shape as export_per_sheet.

    If the combined file fails, every row gets the same reason.
    """
    try:
        ok, bad = export_pdf(doc, [r.sheet for r in rows], folder,
                             combine=True, combined_name=combined_name)
    except Exception as ex:
        ok, bad = [], [err_text(ex)]
    if ok:
        return dict((r.Number, ok[0]) for r in rows), list(ok), {}
    reason = u"合并 PDF：{0}".format(bad[0] if bad else u"未返回文件")
    return (dict((r.Number, u"") for r in rows), [],
            dict((r.Number, reason) for r in rows))


def duplicate_file_names(rows, template, extra):
    """Describe sheets whose rendered file name equals an earlier sheet's.

    Those would overwrite each other in the same issue folder.
    """
    seen, dupes = {}, []
    for r in rows:
        try:
            name = render_template(doc, r.sheet, template, extra)
        except Exception:
            continue
        if name in seen:
            dupes.append(u"{0} 与 {1}：文件名均为 {2}".format(
                seen[name], r.Number, name))
        else:
            seen[name] = r.Number
    return dupes


def append_csv(path, header, rows):
    """Append rows to a CSV, writing the header first if the file is new.

    Same layout as register.append_rows (utf-8-sig for new files so Excel
    opens them with the right encoding).
    """
    is_new = not os.path.exists(path)
    with codecs.open(path, "w" if is_new else "a",
                     "utf-8-sig" if is_new else "utf-8") as f:
        if is_new:
            f.write(register.csv_line(header) + u"\r\n")
        for row in rows:
            f.write(register.csv_line(row) + u"\r\n")


def issue_result_lines(headline, issue_name, issue_folder, combine, do_dwg,
                       pdf_count, dwg_count, recorded, total, skipped,
                       failures):
    """Result dialog text: what was exported, what was skipped, failures last."""
    lines = [headline, u"",
             u"签发名称：{0}".format(issue_name),
             u"保存位置：{0}".format(issue_folder)]
    exported = (u"合并 PDF {0} 个".format(pdf_count) if combine
                else u"PDF {0} 个".format(pdf_count))
    if do_dwg:
        exported += u"，DWG {0} 个".format(dwg_count)
    lines.append(u"已导出：{0}".format(exported))
    lines.append(u"已登记签发记录：{0} / {1} 张".format(recorded, total))
    if skipped:
        lines.append(u"")
        lines.append(u"已跳过、未登记（{0} 张）：".format(len(skipped)))
        lines.extend(listed(skipped))
    if failures:
        lines.append(u"")
        lines.append(u"失败详情（{0} 项）：".format(len(failures)))
        lines.extend(listed(failures))
    return lines


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
    fname = sanitize(u"{0} {1}".format(SCHEDULE_NAME, today_str()))
    opts.FileName = fname
    ids = List[DB.ElementId]([schedule.Id])
    if not doc.Export(folder, ids, opts):
        raise Exception(u"Revit PDF export returned False")
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
        self._last_date = v; self._notify("LastIssued"); self._notify("IssueStatus")

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
        self._last_rev = v; self._notify("LastRevIssued"); self._notify("IssueStatus")

    @property
    def IssueStatus(self):
        """Grid state: 'Not issued', 'Issued' (the current revision is the
        one last issued) or 'Rev changed' (revised since the last issue)."""
        if not self._last_date:
            return u"Not issued"
        current = u"{0}".format(self.CurrentRev or u"")
        issued = u"{0}".format(self._last_rev or u"")
        return u"Issued" if current == issued else u"Rev changed"


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

    def matrix_rows(self):
        """Matrix body shared by the grid and the CSV export.

        One list per sheet: [number, name, cell per issue]. A cell is the
        revision issued, u"✓" if issued without a revision, u"" if not issued.
        """
        out = []
        for skey in self.sheets:
            sheet_cells = self.cells.get(skey, {})
            vals = [skey[0], skey[1]]
            for ikey in self.issues:
                if ikey in sheet_cells:
                    rev = sheet_cells[ikey]
                    vals.append(rev if rev else u"✓")
                else:
                    vals.append(u"")
            out.append(vals)
        return out

    def populate(self):
        dt = DataTable("Issues")
        dt.Columns.Add("Number")
        dt.Columns.Add("Name")
        captions = self.column_captions()
        for cap in captions:
            dt.Columns.Add(cap)
        for values in self.matrix_rows():
            row = dt.NewRow()
            for i, v in enumerate(values):
                row[i] = v
            dt.Rows.Add(row)
        self.hist_grid.ItemsSource = dt.DefaultView
        self.h_status.Text = u"共 {0} 张图纸，{1} 次签发".format(
            len(self.sheets), len(self.issues))

    def on_export(self, sender, args):
        path = forms.save_file(file_ext="csv")
        if not path:
            return
        captions = self.column_captions()
        with codecs.open(path, "w", "utf-8-sig") as f:
            f.write(register.csv_line(["Number", "Name"] + captions) + u"\r\n")
            for vals in self.matrix_rows():
                f.write(register.csv_line(vals) + u"\r\n")
        dialogs.message(u"导出矩阵",
                        u"已保存签发矩阵：\n{0}".format(path), owner=self)


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
        <DataGridTextColumn Header="Issue status" Binding="{Binding IssueStatus}" Width="100" IsReadOnly="True">
          <DataGridTextColumn.ElementStyle>
            <Style TargetType="TextBlock">
              <Setter Property="Foreground" Value="{DynamicResource FgMuted}"/>
              <Style.Triggers>
                <DataTrigger Binding="{Binding IssueStatus}" Value="Issued">
                  <Setter Property="Foreground" Value="{DynamicResource AccentBg}"/>
                  <Setter Property="FontWeight" Value="SemiBold"/>
                </DataTrigger>
                <DataTrigger Binding="{Binding IssueStatus}" Value="Rev changed">
                  <Setter Property="Foreground" Value="{DynamicResource Fg}"/>
                  <Setter Property="FontWeight" Value="Bold"/>
                </DataTrigger>
              </Style.Triggers>
            </Style>
          </DataGridTextColumn.ElementStyle>
        </DataGridTextColumn>
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
        self.template_box.Text = self.cfg.get("template", DEFAULT_TEMPLATE)
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
        self.status.Text = u"共 {0} 张图纸   |   显示 {1} 张   |   已勾选 {2} 张".format(
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
        folder = forms.pick_folder(title=u"选择本项目的签发归档文件夹")
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
        dialogs.message(u"归档文件夹",
                        u"请先选择归档文件夹（点击 Browse...）。",
                        owner=self)
        return None

    def on_open_archive(self, sender, args):
        folder = self.archive_folder()
        if folder and os.path.isdir(folder):
            open_path(folder)
        else:
            dialogs.message(u"归档文件夹", u"尚未设置归档文件夹。", owner=self)

    # ---- DWG export setup ----
    def refresh_dwg_button(self):
        name = self.cfg.get("dwg_setup") or ""
        self.dwg_setup_btn.Content = "DWG setup: {0}".format(
            name if name else "Revit defaults")
        self.dwg_setup_btn.IsEnabled = bool(self.dwg_check.IsChecked)

    def on_pick_dwg_setup(self, sender, args):
        setups = sorted(list_dwg_setups().keys())
        choice = dialogs.pick_item(
            u"DWG 导出设置", [DWG_DEFAULT_LABEL] + setups, owner=self,
            message=u"已保存的设置来自 Revit 的 DWG 导出对话框（File > Export > "
                    u"CAD Formats > Modify Export Setups）。"
                    u"「Revit defaults」会把每张图纸的视图合并为一个 DWG。")
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
            dialogs.message(u"签发", u"请先勾选要签发的图纸。", owner=self)
            return
        folder = self.require_archive()
        if not folder:
            return
        issue_name = (self.issue_name.Text or "").strip()
        if not issue_name:
            dialogs.message(u"签发", u"请填写签发名称，例如 'Planning'、'Tender'。",
                            owner=self)
            return
        issued_to = (self.issued_to.Text or "").strip()
        template = (self.template_box.Text or "").strip() or DEFAULT_TEMPLATE
        combine = bool(self.mode_combined.IsChecked)
        do_dwg = bool(self.dwg_check.IsChecked)
        extra = {"{issue}": issue_name}

        date_str = today_str()
        time_str = DateTime.Now.ToString("HH:mm")
        issue_folder = os.path.join(folder,
                                    issue_folder_name(date_str, issue_name))
        if os.path.isdir(issue_folder):
            if not dialogs.confirm(
                    u"签发",
                    u"该签发文件夹已存在：\n{0}\n\n同名文件会被覆盖，"
                    u"签发记录会追加新行。".format(issue_folder),
                    owner=self, ok_label=u"继续", cancel_label=u"取消"):
                return
        else:
            try:
                os.makedirs(issue_folder)
            except Exception as ex:
                dialogs.message(u"签发", u"无法创建文件夹：\n{0}\n\n{1}"
                                .format(issue_folder, err_text(ex)), owner=self)
                return

        # sheets whose file names would overwrite each other in this run
        warn = u""
        dupes = [] if combine else duplicate_file_names(rows, template, extra)
        if dupes:
            warn = (u"\n\n注意：以下图纸的文件名相同，后导出的会覆盖先导出的：\n"
                    + u"\n".join(listed(dupes)))
        fmt_desc = u"PDF 和 DWG" if do_dwg else u"PDF"
        if not dialogs.confirm(
                u"签发并归档",
                u"签发 {0} 张图纸，签发名称 '{1}'？\n\n{2} 将保存到：\n{3}{4}"
                .format(len(rows), issue_name, fmt_desc, issue_folder, warn),
                owner=self, ok_label=u"签发", cancel_label=u"取消"):
            return

        # 1. export: PDF (one combined file, or one per sheet), then DWG
        if combine:
            pdf_file_for, pdf_done, pdf_reasons = export_combined_pdf(
                rows, issue_folder,
                combined_pdf_name(doc.Title, issue_name, date_str))
        else:
            pdf_file_for, pdf_done, pdf_reasons = export_per_sheet(
                export_pdf, rows, issue_folder,
                template=template, extra_tokens=extra)
        dwg_file_for, dwg_done, dwg_reasons = {}, [], {}
        if do_dwg:
            dwg_file_for, dwg_done, dwg_reasons = export_per_sheet(
                export_dwg, rows, issue_folder, template=template,
                extra_tokens=extra, options=self.current_dwg_options())

        # 2. a sheet is recorded only when every requested format exported.
        #    Any other sheet is skipped and its reason is listed at the end.
        complete, skipped, failures = [], [], []
        for r in rows:
            pdf_name = pdf_file_for.get(r.Number, u"")
            dwg_name = dwg_file_for.get(r.Number, u"")
            missing = []
            if not pdf_name:
                missing.append(u"PDF")
                failures.append(pdf_reasons.get(
                    r.Number, u"{0}: 未导出 PDF".format(r.Number)))
            if do_dwg and not dwg_name:
                missing.append(u"DWG")
                failures.append(dwg_reasons.get(
                    r.Number, u"{0}: 未导出 DWG".format(r.Number)))
            if missing:
                skipped.append(u"{0}（缺 {1}）".format(
                    r.Number, u"、".join(missing)))
            else:
                complete.append((r, pdf_name, dwg_name))

        items = []  # (sheet number, register row, transmittal row)
        for r, pdf_name, dwg_name in complete:
            rev = get_current_rev(r.sheet)
            formats = u", ".join(
                fmt for fmt, name in (("PDF", pdf_name), ("DWG", dwg_name))
                if name)
            reg_row = [date_str, time_str, issue_name, issued_to,
                       doc.Title, r.Number, r.Name, rev, pdf_name, formats]
            items.append((r.Number, reg_row,
                          [r.Number, r.Name, rev, pdf_name, formats]))

        # 3. register: one row per write, so a failure names the sheets that
        #    were not recorded. Nothing is written when nothing exported.
        reg_path = register.register_path(folder)
        recorded, reg_errors = [], []
        for number, reg_row, _trans in items:
            try:
                register.append_rows(reg_path, [reg_row])
                recorded.append(number)
            except Exception as ex:
                reg_errors.append(u"未写入签发记录 {0}：{1}".format(
                    number, err_text(ex)))

        trans_error = None
        trans_rows = [trans for number, _reg, trans in items
                      if number in recorded]
        if trans_rows:
            try:
                append_csv(os.path.join(issue_folder, TRANSMITTAL_FILENAME),
                           TRANSMITTAL_HEADER, trans_rows)
            except Exception as ex:
                trans_error = err_text(ex)

        self.cfg["issued_to"] = issued_to
        self.cfg["template"] = template
        self.cfg["dwg"] = do_dwg
        save_config(self.cfg)
        read_errors = []
        try:
            self.refresh_last_issued()
        except Exception as ex:
            # the grid status may be stale, but the report below must still show
            read_errors.append(u"签发记录无法读取，状态列可能未更新：{0}".format(
                err_text(ex)))

        # 4. report. A partial run is never described as a success.
        if reg_errors:
            headline = u"部分图纸未能写入签发记录，请查看下方失败详情后重试。"
        elif not recorded:
            headline = u"没有图纸导出成功，签发记录未写入。"
        elif trans_error:
            headline = u"签发记录已写入，但签发清单写入失败。"
        elif skipped:
            headline = u"部分图纸未导出，已跳过，未登记。"
        else:
            headline = u"签发完成，全部图纸已导出并登记。"
        if trans_error:
            reg_errors.append(u"签发清单 {0} 写入失败：{1}".format(
                TRANSMITTAL_FILENAME, trans_error))
        failures = unique(failures) + reg_errors + read_errors
        for line in failures:
            logger.error(u"Issue Manager: %s", line)

        text = u"\n".join(issue_result_lines(
            headline, issue_name, issue_folder, combine, do_dwg,
            len(pdf_done), len(dwg_done), len(recorded), len(rows),
            skipped, failures)) + u"\n\n打开文件夹？"
        if dialogs.confirm(u"签发结果", text, owner=self,
                           ok_label=u"打开文件夹", cancel_label=u"关闭"):
            open_path(issue_folder)

    # ---- history / register ----
    def on_history(self, sender, args):
        rows = self.register_rows()
        if not rows:
            dialogs.message(u"签发记录", u"本归档文件夹还没有签发记录。",
                            owner=self)
            return
        HistoryWindow(HISTORY_XAML, rows).show_dialog()

    def on_open_register(self, sender, args):
        folder = self.archive_folder()
        path = register.register_path(folder) if folder else None
        if path and os.path.exists(path):
            open_path(path)
        else:
            dialogs.message(u"签发记录",
                            u"还没有签发记录，首次签发后会自动创建。",
                            owner=self)

    # ---- Revit schedule ----
    def on_update_schedule(self, sender, args):
        try:
            vs, created = ensure_schedule()
        except Exception as ex:
            dialogs.message(u"明细表", u"无法创建明细表：\n{0}".format(err_text(ex)),
                            owner=self)
            return
        dialogs.message(
            u"明细表",
            (u"已创建" if created else u"已找到") +
            u"明细表 '{0}'。\n\n列出每张图纸及其当前修订信息，"
            u"可在项目浏览器的 Schedules/Quantities 中找到。".format(SCHEDULE_NAME),
            owner=self)

    def on_export_schedule(self, sender, args):
        try:
            vs, created = ensure_schedule()
        except Exception as ex:
            dialogs.message(u"明细表", u"无法创建明细表：\n{0}".format(err_text(ex)),
                            owner=self)
            return
        folder = self.archive_folder()
        if not folder or not os.path.isdir(folder):
            folder = forms.pick_folder(title=u"选择明细表 PDF 的保存文件夹")
        if not folder:
            return
        try:
            fname = export_schedule_pdf(vs, folder)
        except Exception as ex:
            dialogs.message(
                u"明细表 PDF",
                u"无法导出明细表 PDF：\n{0}\n\n"
                u"您仍可在 Revit 中打开项目浏览器里的 '{1}'，"
                u"然后使用 File > Print 打印。".format(err_text(ex), SCHEDULE_NAME),
                owner=self)
            return
        if dialogs.confirm(u"明细表 PDF",
                           u"已导出：\n{0}\\{1}\n\n打开文件夹？".format(
                               folder, fname),
                           owner=self, ok_label=u"打开文件夹",
                           cancel_label=u"关闭"):
            open_path(folder)


if not doc:
    forms.alert(u"请先打开一个 Revit 文档。", exitscript=True)

IssueManagerWindow(XAML).show_dialog()
