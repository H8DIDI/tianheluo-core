# ---- compat（天河落共用片段，打包时内联进脚本）---------------------------------
# 让同一份插件脚本在 Revit 2020 和 Revit 2026 里都能跑（中间的 2021–2025 大体按功能探测，同样适用）。
# 原则：不用「年份」判断，用「这个 API 在不在」判断（hasattr），Autodesk 以后再改名也不用来改版本表。
#   thl_eid(x)      ElementId → 整数（2024 起新增 .Value，更早只有 .IntegerValue；2026 已删 .IntegerValue）
#   thl_make_eid(n) 整数 → ElementId（2024 起要 Int64）
#   thl_bic(cat)    Category → BuiltInCategory 枚举（Category.BuiltInCategory 是 2022 起才有的）
#   THL_HAS_*       有没有某个新功能，没有的时候脚本走降级写法，别硬调（硬调会 AttributeError）
# 名字都带 thl_ 前缀，是为了不和插件里常见的局部变量名 eid / bic 撞上（局部变量会盖住同名函数）。
import clr as _cp_clr
import System as _cp_System
from Autodesk.Revit import DB as _cpDB

THL_HAS_ID_VALUE = hasattr(_cpDB.ElementId, 'Value')                       # 2024+
THL_HAS_CAT_BIC = hasattr(_cpDB.Category, 'BuiltInCategory')               # 2022+
THL_HAS_FORGE_TYPES = hasattr(_cpDB, 'SpecTypeId') and hasattr(_cpDB, 'GroupTypeId')   # 2022+ / 2024+
THL_HAS_PDF_EXPORT = hasattr(_cpDB, 'PDFExportOptions')                    # 2022+
THL_HAS_TOPOSOLID = hasattr(_cpDB.BuiltInCategory, 'OST_Toposolid')        # 2024+
THL_HAS_SHEET_COLLECTION = hasattr(_cpDB, 'SheetCollection')               # 2025+


def thl_eid(x):
    """ElementId → 整数；None 或不是 ElementId 返回 -1。"""
    if x is None:
        return -1
    if THL_HAS_ID_VALUE:
        try:
            return int(x.Value)
        except AttributeError:
            pass
    try:
        return int(x.IntegerValue)
    except AttributeError:
        return int(x)


def thl_make_eid(n):
    """整数 → ElementId。"""
    n = int(n)
    if THL_HAS_ID_VALUE:
        return _cpDB.ElementId(_cp_System.Int64(n))
    return _cpDB.ElementId(n)


def thl_bic(cat):
    """Category → DB.BuiltInCategory 枚举值；自定义类别（ID 为正数）返回 None。"""
    if cat is None:
        return None
    if THL_HAS_CAT_BIC:
        try:
            return cat.BuiltInCategory
        except Exception:
            return None
    v = thl_eid(cat.Id)
    if v >= 0:
        return None
    try:
        return _cp_System.Enum.ToObject(_cpDB.BuiltInCategory, v)
    except Exception:
        return None


def thl_bic_by_name(name):
    """按名字取 BuiltInCategory（如 'OST_Toposolid'）；这个版本没有就返回 None，不报错。"""
    return getattr(_cpDB.BuiltInCategory, name, None)
