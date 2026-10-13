"""Checks the well-sheets rules helpers used by the live preview (no Revit needed).

The helper block is read straight from python-src/well-sheets/script.py (from the
"可调规则" header up to the Revit `doc = revit.doc` line), so the tests always
run the code that ships. It is executed with a stub `System` and a fake well."""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
text = (ROOT / "python-src" / "well-sheets" / "script.py").read_text(encoding="utf-8")
start = text.rfind("# ----", 0, text.index("# 可调规则"))
end = text.index("\ndoc = revit.doc")
src = text[start:end]

# stub the .NET bits the block touches at import time
System = types.SimpleNamespace(
    Environment=types.SimpleNamespace(
        GetFolderPath=lambda _: str(ROOT / "_deploytest" / "rules_test"),
        SpecialFolder=types.SimpleNamespace(MyDocuments=1)))
ns = {"System": System}
exec(compile(src, "script.py(rules block)", "exec"), ns)

FT = 304.8


class P:
    def __init__(self, x, y, z):
        self.X, self.Y, self.Z = x, y, z


class BB:
    def __init__(self, hx, hy, hz):
        self.Min = P(-hx, -hy, -hz)
        self.Max = P(hx, hy, hz)


class Well:
    def __init__(self, hx_mm, hy_mm, hz_mm):
        self._bb = BB(hx_mm / FT, hy_mm / FT, hz_mm / FT)

    def get_BoundingBox(self, _):
        return self._bb


fails = 0


def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name)
    if not ok:
        fails += 1


rules = dict(ns["DEFAULT_RULES"])
rects, dims = ns["layout_rects"](Well(600, 600, 750), rules)
check("default rules: 4 section views + plan", set(rects) == {"平面", "南", "北", "西", "东"})

# sizes follow the formulas in the script: plan crop = half + margin, section = half + face_out, all / scale
hx, hy, hz = 600, 600, 750
check("plan width = 2*(half+crop)/scale", abs(rects["平面"][2] - 2 * (hx + 1371.6) / 50) < 1e-6)
check("front height = 2*(halfZ+face_out)/scale", abs(rects["南"][3] - 2 * (hz + 100) / 50) < 1e-6)


def overlaps(a, b):
    return abs(a[0] - b[0]) < (a[2] + b[2]) / 2.0 and abs(a[1] - b[1]) < (a[3] + b[3]) / 2.0


names = list(rects)
clash = [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))
         if overlaps(rects[names[i]], rects[names[j]])]
check("default rules: no overlapping views for a 1.2 m well", clash == [])

# a tiny spacing must be flagged as overlapping
tight = dict(rules, spacing_y_mm=5)
rects2, _ = ns["layout_rects"](Well(600, 600, 750), tight)
clash2 = [(a, b) for a in rects2 for b in rects2 if a < b and overlaps(rects2[a], rects2[b])]
check("tight spacing is detected as overlap", len(clash2) > 0)

# defaults equal the numbers that were hard-coded in the original script
check("default crop = 4.5 ft", abs(ns["DEFAULT_RULES"]["crop_margin_mm"] - 4.5 * FT) < 0.05)
check("default face_out = 100 mm", ns["DEFAULT_RULES"]["face_out_mm"] == 100)
check("default face_depth = 1100 mm", ns["DEFAULT_RULES"]["face_depth_mm"] == 1100)
check("default spacing_x = 0.75 ft", abs(ns["DEFAULT_RULES"]["spacing_x_mm"] - 0.75 * FT) < 0.05)
check("default spacing_y = 0.55 ft", abs(ns["DEFAULT_RULES"]["spacing_y_mm"] - 0.55 * FT) < 0.05)
check("default shift = 0.25 ft", abs(ns["DEFAULT_RULES"]["sheet_shift_x_mm"] - 0.25 * FT) < 0.05)

# ---- input parsing: empty / non-numeric / negative fall back to defaults, and are reported
parse = ns["parse_rules"]
DR = ns["DEFAULT_RULES"]
texts_default = dict((k, str(v)) for k, v in DR.items() if k != "hide_grids_levels")

r0, iss0 = parse(texts_default, True)
check("parse: defaults round-trip with no issues",
      iss0 == [] and all(r0[k] == DR[k] for k in texts_default) and r0["hide_grids_levels"] is True)
check("parse: unchecked hide flag is kept", parse(texts_default, False)[0]["hide_grids_levels"] is False)

r1, iss1 = parse(dict(texts_default, crop_margin_mm=""), True)
check("parse: empty field -> default + reported",
      r1["crop_margin_mm"] == 1371.6 and len(iss1) == 1 and "平面裁剪余量" in iss1[0])
r2, iss2 = parse(dict(texts_default, face_out_mm="-20"), True)
check("parse: negative face_out -> default + reported",
      r2["face_out_mm"] == 100 and len(iss2) == 1 and "剖切面外移" in iss2[0])
r3, iss3 = parse(dict(texts_default, sheet_shift_x_mm="-50"), True)
check("parse: negative sheet shift is allowed (no reset)", r3["sheet_shift_x_mm"] == -50 and iss3 == [])
r4, iss4 = parse(dict(texts_default, face_depth_mm="abc"), True)
check("parse: non-numeric -> default + reported", r4["face_depth_mm"] == 1100 and len(iss4) == 1)
r5, iss5 = parse(dict(texts_default, view_scale="0"), True)
check("parse: view scale 0 -> default 50", r5["view_scale"] == 50 and len(iss5) == 1)
r6, iss6 = parse(dict(texts_default, view_scale="99999"), True)
check("parse: too large is clamped to 1000 and reported", r6["view_scale"] == 1000 and len(iss6) == 1)
r7, iss7 = parse(dict(texts_default, view_scale="20.4"), True)
check("parse: view scale rounds to integer", r7["view_scale"] == 20 and iss7 == [])
r8, iss8 = parse(dict(texts_default, face_out_mm="nan"), True)
check("parse: nan -> default", r8["face_out_mm"] == 100 and len(iss8) == 1)
r9, iss9 = parse(dict(texts_default, face_out_mm="", spacing_x_mm="-1"), True)
check("parse: two bad fields -> two reports", len(iss9) == 2)
r10, iss10 = parse(dict(texts_default, sheet_shift_x_mm="-5000"), True)
check("parse: shift below -2000 is clamped to -2000 (kept, reported)",
      r10["sheet_shift_x_mm"] == -2000 and len(iss10) == 1)

# ---- overlap pairs: one entry per pair, fixed order
check("pairs: defaults have none", ns["overlap_pairs"](rects) == [])
tight_both = ns["layout_rects"](Well(600, 600, 750), dict(rules, spacing_x_mm=5, spacing_y_mm=5))[0]
pairs = ns["overlap_pairs"](tight_both)
check("pairs: all 10 pairs listed when spacing is tiny", len(pairs) == 10)
check("pairs: order follows VIEW_ORDER", pairs[0] == ("平面", "南") and pairs[-1] == ("西", "东"))
only_y = ns["layout_rects"](Well(600, 600, 750), dict(rules, spacing_y_mm=5))[0]
check("pairs: small front/back spacing only hits plan/north/south pairs",
      ns["overlap_pairs"](only_y) == [("平面", "南"), ("平面", "北"), ("南", "北")])

# ---- which spacing to increase
hint = ns["pair_hint"]
check("hint: plane vs south is front-back", hint("平面", "南") == "前后视图间距")
check("hint: plane vs east is left-right", hint("东", "平面") == "左右视图间距")
check("hint: north vs south is front-back", hint("北", "南") == "前后视图间距")
check("hint: west vs east is left-right", hint("西", "东") == "左右视图间距")
check("hint: diagonal pair allows either", hint("南", "西") == "左右或前后视图间距")

# ---- section sketch geometry (pixels, not to scale): every part must sit inside the strip
for hx_, hy_, fo_, fd_ in [(600, 600, 100, 1100), (10, 5000, 100, 20000), (5000, 10, 0, 1), (600, 600, 5000, 0)]:
    g = ns["section_sketch"](hx_, hy_, fo_, fd_)
    wx, wy, ww, wh = g["well"]
    bx, by, bw, bh = g["box"]
    tag = "(%s,%s,%s,%s)" % (hx_, hy_, fo_, fd_)
    check("sketch %s: cut plane is below the well south wall" % tag, g["cut_y"] > wy + wh)
    check("sketch %s: section box ends exactly at the cut plane" % tag, abs((by + bh) - g["cut_y"]) < 1e-9)
    check("sketch %s: box top is not above the drawing" % tag, by >= -1e-9)
    check("sketch %s: fits the 170 px strip" % tag, g["cut_y"] <= 130)
    d1x, d1a, d1b = g["d1"]
    d2x, d2a, d2b = g["d2"]
    check("sketch %s: outside dimension sits left of the well" % tag, d1x < wx and d1a == wy + wh)
    check("sketch %s: depth dimension spans the box, right of the well" % tag,
          d2x > wx + ww and abs(d2a - by) < 1e-9 and abs(d2b - g["cut_y"]) < 1e-9)

# ---- saved-rules file: read back as-is, broken file is not restored
tmpdir = tempfile.mkdtemp()
try:
    ns["RULES_PATH"] = os.path.join(tmpdir, "井展开图规则.json")
    data, msg = ns["read_saved_rules"]()
    check("saved: no file -> None", data is None and msg != "")
    ns["save_rules"](dict(DR, view_scale=20))
    data, msg = ns["read_saved_rules"]()
    check("saved: save then read back", data is not None and data["view_scale"] == 20)
    check("load_rules: reads saved value over defaults",
          ns["load_rules"]()["view_scale"] == 20 and ns["load_rules"]()["face_out_mm"] == 100)
    with open(ns["RULES_PATH"], "w") as f:
        f.write("{not json")
    data, msg = ns["read_saved_rules"]()
    check("saved: broken json -> None", data is None and msg != "")
    check("load_rules: broken json -> defaults", ns["load_rules"]() == DR)
    with open(ns["RULES_PATH"], "w") as f:
        json.dump([1, 2], f)
    data, msg = ns["read_saved_rules"]()
    check("saved: non-object json -> None", data is None)
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("ALL PASS" if fails == 0 else "%d FAILED" % fails)
sys.exit(fails)
