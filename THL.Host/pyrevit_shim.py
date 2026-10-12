# -*- coding: utf-8 -*-
"""天河落 THL 内置的 pyrevit 兼容层：只实现 Antigravity 脚本用到的那一小部分接口。
这样原样的 pyRevit 脚本可以在没有安装 pyRevit 的电脑上运行。"""
import clr
import sys
import types

clr.AddReference('RevitAPI')
clr.AddReference('THL.Host')
from Autodesk.Revit import DB
try:
    clr.AddReference('RevitAPIUI')
    from Autodesk.Revit import UI
except Exception:   # 仅在 Revit 之外做离线测试时可能没有 UI 程序集
    UI = None
from THL.Host import PyBridge
from System import Array


def _strs(seq):
    """Python 列表 -> .NET string[]（.NET 方法不接受 Python list）"""
    return Array[str]([str(x) for x in seq])


def _uiapp():
    return PyBridge.UIApp


# ---------------------------------------------------------------- revit
revit = types.ModuleType('pyrevit.revit')


class _Tx(object):
    """with revit.Transaction("名称"): ..."""
    def __init__(self, name="天河落", doc=None, **kw):
        self._doc = doc or _uiapp().ActiveUIDocument.Document
        self._name = name
        self._tx = None

    def __enter__(self):
        self._tx = DB.Transaction(self._doc, self._name)
        self._tx.Start()
        return self._tx

    def __exit__(self, et, ev, tb):
        if et is None:
            self._tx.Commit()
        else:
            try:
                self._tx.RollBack()
            except Exception:
                pass
        return False


class _Query(object):
    @staticmethod
    def get_name(elem):
        try:
            return elem.Name
        except Exception:
            return DB.Element.Name.GetValue(elem)


class _RevitModule(types.ModuleType):
    @property
    def uidoc(self):
        return _uiapp().ActiveUIDocument

    @property
    def doc(self):
        return _uiapp().ActiveUIDocument.Document

    @property
    def uiapp(self):
        return _uiapp()


revit = _RevitModule('pyrevit.revit')
revit.Transaction = _Tx
revit.query = _Query
revit.DB = DB
revit.UI = UI

# ---------------------------------------------------------------- forms
forms = types.ModuleType('pyrevit.forms')


class TemplateListItem(object):
    """可子类化：重写 name 属性来自定义列表里显示的文字。"""
    def __init__(self, orig_item, checked=False, **kw):
        self.item = orig_item
        self.checked = checked

    @property
    def name(self):
        return str(self.item)

    def __str__(self):
        return self.name


def _label(item):
    if isinstance(item, TemplateListItem):
        return item.name
    return str(item)


def _unwrap(item):
    return item.item if isinstance(item, TemplateListItem) else item


def alert(msg, title=None, sub_msg=None, expanded=None, footer='', ok=True, cancel=False,
          yes=False, no=False, retry=False, warn_icon=True, options=None, exitscript=False, **kw):
    text = str(msg)
    if sub_msg:
        text += "\n\n" + str(sub_msg)
    res = PyBridge.Alert(text, title or "天河落", bool(yes), bool(no), bool(cancel))
    if exitscript and not res:
        sys.exit()
    return res


class SelectFromList(object):
    @staticmethod
    def show(items, title='选择', button_name='确定', multiselect=False, name_attr=None, width=None,
             height=None, **kw):
        items = list(items)
        labels = [(_label(i) if name_attr is None else str(getattr(i, name_attr))) for i in items]
        idx = PyBridge.Select(_strs(labels), str(title), str(button_name), bool(multiselect))
        if idx is None:
            return None
        picked = [_unwrap(items[i]) for i in idx]
        if multiselect:
            return picked
        return picked[0] if picked else None


class CommandSwitchWindow(object):
    @staticmethod
    def show(options, message='', **kw):
        labels = [str(o) for o in options]
        i = PyBridge.Choose(_strs(labels), str(message))
        if i < 0:
            return None
        return labels[i]


def ask_for_string(default='', prompt='', title='', **kw):
    return PyBridge.AskString(str(prompt), str(title), str(default))


def pick_folder(title=None, **kw):
    return PyBridge.PickFolder(title or "选择文件夹")


def pick_file(file_ext='', files_filter='', init_dir='', restore_dir=True, multi_file=False, unc_paths=False, title=None, **kw):
    return PyBridge.PickFile(str(file_ext or ''), title or '')


def save_file(file_ext='', files_filter='', init_dir='', default_name='', restore_dir=True, unc_paths=False, title=None, **kw):
    return PyBridge.SaveFile(str(file_ext or ''), str(default_name or ''), title or '')


clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')
clr.AddReference('IronPython.Wpf')
from System.Windows import Window as _Window


class WPFWindow(_Window):
    """XAML 窗口基类（只实现常用部分）：把 XAML 里带 Name 的控件变成实例属性。
    用法与 pyRevit 相同：
        class MyWin(forms.WPFWindow):
            def __init__(self, xaml): forms.WPFWindow.__init__(self, xaml, literal_string=True)"""
    def __init__(self, xaml_source, literal_string=False, handle_esc=True, set_owner=True, **kw):
        import wpf
        from System.IO import StringReader
        _Window.__init__(self)
        if literal_string:
            wpf.LoadComponent(self, StringReader(xaml_source))
        else:
            wpf.LoadComponent(self, xaml_source)
        if handle_esc:
            def _esc(sender, args):
                from System.Windows.Input import Key
                if args.Key == Key.Escape:
                    self.Close()
            self.PreviewKeyDown += _esc

    def show_dialog(self):
        return self.ShowDialog()

    def show(self, modal=False):
        return self.ShowDialog() if modal else self.Show()


class ProgressBar(object):
    def __init__(self, title='', cancellable=False, step=1, **kw):
        self._p = PyBridge.OpenProgress(title, bool(cancellable))

    @property
    def cancelled(self):
        return self._p.Cancelled

    def update_progress(self, new_value, max_value=100):
        self._p.Update(int(new_value), int(max_value))

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.close()
        return False

    def close(self):
        self._p.Close()


forms.TemplateListItem = TemplateListItem
forms.alert = alert
forms.SelectFromList = SelectFromList
forms.CommandSwitchWindow = CommandSwitchWindow
forms.ask_for_string = ask_for_string
forms.pick_folder = pick_folder
forms.ProgressBar = ProgressBar
forms.pick_file = pick_file
forms.save_file = save_file
forms.WPFWindow = WPFWindow

# ---------------------------------------------------------------- script
script = types.ModuleType('pyrevit.script')


class _Logger(object):
    def _w(self, level, msg, *a):
        try:
            text = (str(msg) % a) if a else str(msg)
        except Exception:
            text = str(msg)
        print('[%s] %s' % (level, text))
    def debug(self, m, *a): self._w('DEBUG', m, *a)
    def info(self, m, *a): self._w('INFO', m, *a)
    def warning(self, m, *a): self._w('WARN', m, *a)
    def error(self, m, *a): self._w('ERROR', m, *a)
    def critical(self, m, *a): self._w('CRIT', m, *a)
    def set_level(self, *a): pass


def _get_logger(*a, **k):
    return _Logger()


def _get_document_data_file(file_id, file_ext, add_cmd_name=False):
    d = _uiapp().ActiveUIDocument.Document
    return PyBridge.DataFile(d.Title, str(file_id), str(file_ext))


script.get_logger = _get_logger
script.get_document_data_file = _get_document_data_file
script.get_universal_data_file = lambda file_id, file_ext='dat', **k: PyBridge.DataFile('universal', str(file_id), str(file_ext))
sys.modules['pyrevit.script'] = script

# 让 `from pyrevit import revit, forms` 以及 `import pyrevit.forms` 都能工作
sys.modules['pyrevit.revit'] = revit
sys.modules['pyrevit.forms'] = forms
# pyrevit 包顶层也要能 `from pyrevit import script`
import sys as _sys
_this = _sys.modules[__name__]
_this.script = script
