# ---------------------------------------------------------------------------
# 可调规则（保存在 Documents\KaifaTools\井展开图规则.json，默认值与原来写死的数值一致）
# 单位都是毫米；“图纸mm”指打印到图纸上的毫米。
# ---------------------------------------------------------------------------
import json as _json
import os as _os

FT_MM = 304.8
RULES_PATH = _os.path.join(
    System.Environment.GetFolderPath(System.Environment.SpecialFolder.MyDocuments), "KaifaTools", "井展开图规则.json")
DEFAULT_RULES = {
    "view_scale": 50,               # 视图比例 1:N
    "crop_margin_mm": 1371.6,       # 平面裁剪框在井外的余量（原 4.5 ft）
    "face_out_mm": 100,             # 剖切面在井外墙外移的距离
    "face_depth_mm": 1100,          # 剖面框沿视线方向的厚度
    "spacing_x_mm": 228.6,          # 左右剖面视图离平面的距离（图纸mm，原 0.75 ft）
    "spacing_y_mm": 167.64,         # 前后剖面视图离平面的距离（图纸mm，原 0.55 ft）
    "sheet_shift_x_mm": 76.2,       # 整组视图相对图框中心的左移（图纸mm，原 0.25 ft）
    "hide_grids_levels": True,      # 隐藏轴网和标高
}
RULE_LABELS = [
    ("view_scale", "视图比例 1:", 1, 1000),
    ("crop_margin_mm", "平面裁剪余量 (mm)", 0, 20000),
    ("face_out_mm", "剖切面外移 (mm)", 0, 5000),
    ("face_depth_mm", "剖面框厚度 (mm)", 1, 20000),
    ("spacing_x_mm", "左右视图间距 (图纸mm)", 0, 2000),
    ("spacing_y_mm", "前后视图间距 (图纸mm)", 0, 2000),
    ("sheet_shift_x_mm", "整组左移 (图纸mm)", -2000, 2000),
]


def load_rules():
    rules = dict(DEFAULT_RULES)
    try:
        if _os.path.exists(RULES_PATH):
            with open(RULES_PATH, "r") as f:
                data = _json.load(f)
            for k in DEFAULT_RULES:
                if k in data:
                    rules[k] = data[k]
    except Exception:
        rules = dict(DEFAULT_RULES)
    return rules


def save_rules(rules):
    folder = _os.path.dirname(RULES_PATH)
    if not _os.path.exists(folder):
        _os.makedirs(folder)
    with open(RULES_PATH, "w") as f:
        _json.dump(rules, f, indent=2, ensure_ascii=False)


def layout_rects(sample, rules):
    """各视图在图纸上的矩形（图纸mm，平面中心为原点）。预览用，与生成时的几何一致。
    返回 ({名称: (cx, cy, 宽, 高)}, (半宽, 半长, 半高, 剖面框厚) 毫米)。"""
    bb = sample.get_BoundingBox(None)
    hx = (bb.Max.X - bb.Min.X) / 2.0 * FT_MM
    hy = (bb.Max.Y - bb.Min.Y) / 2.0 * FT_MM
    hz = (bb.Max.Z - bb.Min.Z) / 2.0 * FT_MM
    sc = float(rules["view_scale"])
    crop = rules["crop_margin_mm"]
    fo = rules["face_out_mm"]
    fd = rules["face_depth_mm"]
    sx, sy = rules["spacing_x_mm"], rules["spacing_y_mm"]
    rects = {}
    rects["平面"] = (0.0, 0.0, 2 * (hx + crop) / sc, 2 * (hy + crop) / sc)
    rects["南"] = (0.0, -sy, 2 * (hx + fo) / sc, 2 * (hz + fo) / sc)
    rects["北"] = (0.0, sy, 2 * (hx + fo) / sc, 2 * (hz + fo) / sc)
    rects["西"] = (-sx, 0.0, 2 * (hy + fo) / sc, 2 * (hz + fo) / sc)
    rects["东"] = (sx, 0.0, 2 * (hy + fo) / sc, 2 * (hz + fo) / sc)
    return rects, (hx, hy, hz, fd)


def rules_preview_dialog(sample):
    """规则与预览窗口：改参数，右边立刻画出按这些规则排版的图纸效果。
    保存并返回规则；取消返回 None。"""
    import clr
    clr.AddReference("System.Windows.Forms")
    clr.AddReference("System.Drawing")
    from System.Windows.Forms import (Form, Label, TextBox, Button, Panel, CheckBox,
                                      DialogResult, FormBorderStyle, FormStartPosition)
    from System.Drawing import Size, Point, Font, Color, Pen, SolidBrush, PointF
    from System.Drawing.Drawing2D import SmoothingMode

    rules = load_rules()
    form = Form()
    form.Text = "一键井展开图 · 规则与预览"
    form.Font = Font("Microsoft YaHei UI", 9.0)
    form.StartPosition = FormStartPosition.CenterScreen
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.MaximizeBox = False
    form.ClientSize = Size(980, 600)

    boxes = {}
    y = 16
    for key, label, lo, hi in RULE_LABELS:
        lb = Label()
        lb.Text = label
        lb.Location = Point(16, y + 4)
        lb.AutoSize = True
        tb = TextBox()
        tb.Location = Point(200, y)
        tb.Width = 110
        tb.Text = str(rules[key])
        form.Controls.Add(lb)
        form.Controls.Add(tb)
        boxes[key] = tb
        y += 36
    chk = CheckBox()
    chk.Text = "隐藏轴网和标高"
    chk.Checked = bool(rules["hide_grids_levels"])
    chk.Location = Point(16, y + 4)
    chk.AutoSize = True
    form.Controls.Add(chk)
    y += 40

    note = Label()
    note.Location = Point(16, y)
    note.Size = Size(310, 170)
    note.ForeColor = Color.FromArgb(70, 70, 70)
    form.Controls.Add(note)

    panel = Panel()
    panel.Location = Point(340, 16)
    panel.Size = Size(620, 520)
    panel.BackColor = Color.White
    form.Controls.Add(panel)

    state = {"rects": {}, "warn": "", "rules": dict(DEFAULT_RULES)}

    def read_rules():
        out = dict(DEFAULT_RULES)
        for key, label, lo, hi in RULE_LABELS:
            try:
                v = float(boxes[key].Text.strip())
                if key == "view_scale":
                    v = int(round(v))
                out[key] = max(lo, min(hi, v))
            except Exception:
                out[key] = DEFAULT_RULES[key]
        out["hide_grids_levels"] = bool(chk.Checked)
        return out

    def recompute():
        r = read_rules()
        rects, dims = layout_rects(sample, r)
        names = list(rects.keys())
        warn = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = rects[names[i]], rects[names[j]]
                if abs(a[0] - b[0]) < (a[2] + b[2]) / 2.0 and abs(a[1] - b[1]) < (a[3] + b[3]) / 2.0:
                    warn.append(names[i] + " 与 " + names[j] + " 重叠")
        state["rects"] = rects
        state["warn"] = "；".join(warn)
        state["rules"] = r
        hx, hy, hz, fd = dims
        text = ("预览尺寸取自你选中的井：半宽 %.0f / 半长 %.0f / 半高 %.0f mm\n"
                "视图比例 1:%d，剖面框厚 %.0f mm\n") % (hx, hy, hz, r["view_scale"], fd)
        if warn:
            text += "⚠ " + state["warn"] + "（请加大间距）"
            note.ForeColor = Color.Firebrick
        else:
            text += "✓ 各视图没有重叠，可以生成"
            note.ForeColor = Color.FromArgb(40, 120, 60)
        note.Text = text
        panel.Invalidate()

    def on_paint(sender, e):
        g = e.Graphics
        g.SmoothingMode = SmoothingMode.AntiAlias
        rects = state["rects"]
        if not rects:
            return
        minx = min(r[0] - r[2] / 2.0 for r in rects.values())
        maxx = max(r[0] + r[2] / 2.0 for r in rects.values())
        miny = min(r[1] - r[3] / 2.0 for r in rects.values())
        maxy = max(r[1] + r[3] / 2.0 for r in rects.values())
        W = panel.Width - 40.0
        H = panel.Height - 40.0
        k = min(W / max(maxx - minx, 1.0), H / max(maxy - miny, 1.0))
        ox = 20 + (W - (maxx - minx) * k) / 2.0 - minx * k
        oy = 20 + (H - (maxy - miny) * k) / 2.0 + maxy * k

        bad = set()
        for nm in rects:
            if "与 " + nm in state["warn"] or nm + " 与" in state["warn"]:
                bad.add(nm)
        pen_ok = Pen(Color.FromArgb(47, 140, 255), 2.0)
        pen_bad = Pen(Color.Firebrick, 2.0)
        pen_plan = Pen(Color.FromArgb(139, 92, 246), 2.0)
        brush = SolidBrush(Color.FromArgb(40, 60, 90))
        font = Font("Microsoft YaHei UI", 9.0)
        for nm, (cx, cy, w, h) in rects.items():
            x0 = ox + (cx - w / 2.0) * k
            y0 = oy - (cy + h / 2.0) * k
            pen = pen_plan if nm == "平面" else (pen_bad if nm in bad else pen_ok)
            g.DrawRectangle(pen, x0, y0, w * k, h * k)
            g.DrawString(nm, font, brush, PointF(x0 + 4, y0 + 2))
        px, py = ox, oy
        g.DrawLine(Pen(Color.Gray, 1.0), px - 6, py, px + 6, py)
        g.DrawLine(Pen(Color.Gray, 1.0), px, py - 6, px, py + 6)

    panel.Paint += on_paint

    def changed(sender, args):
        recompute()

    for tb in boxes.values():
        tb.TextChanged += changed
    chk.CheckedChanged += changed

    result = {"rules": None}

    def on_ok(sender, args):
        r = read_rules()
        save_rules(r)
        result["rules"] = r
        form.DialogResult = DialogResult.OK
        form.Close()

    def on_default(sender, args):
        for key, label, lo, hi in RULE_LABELS:
            boxes[key].Text = str(DEFAULT_RULES[key])
        chk.Checked = bool(DEFAULT_RULES["hide_grids_levels"])
        recompute()

    def on_cancel(sender, args):
        form.Close()

    ok = Button()
    ok.Text = "保存规则并生成"
    ok.Location = Point(340, 548)
    ok.Size = Size(200, 36)
    ok.Click += on_ok
    cancel = Button()
    cancel.Text = "取消"
    cancel.Location = Point(556, 548)
    cancel.Size = Size(120, 36)
    cancel.Click += on_cancel
    default = Button()
    default.Text = "恢复默认"
    default.Location = Point(16, 548)
    default.Size = Size(120, 36)
    default.Click += on_default
    form.Controls.Add(ok)
    form.Controls.Add(cancel)
    form.Controls.Add(default)
    form.AcceptButton = ok
    form.CancelButton = cancel

    recompute()
    form.ShowDialog()
    return result["rules"]
