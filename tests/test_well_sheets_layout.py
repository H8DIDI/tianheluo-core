"""Checks the well-sheets layout math used by the live preview (no Revit needed).
Runs the pure functions from python-src/well-sheets/rules_block.py with a stub `System` and a fake well."""
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent
src = (ROOT / "python-src" / "well-sheets" / "rules_block.py").read_text(encoding="utf-8")

# stub the .NET bits the block touches at import time
System = types.SimpleNamespace(
    Environment=types.SimpleNamespace(
        GetFolderPath=lambda _: str(ROOT / "_deploytest" / "rules_test"),
        SpecialFolder=types.SimpleNamespace(MyDocuments=1)))
ns = {"System": System}
exec(compile(src, "rules_block.py", "exec"), ns)

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

print("ALL PASS" if fails == 0 else "%d FAILED" % fails)
sys.exit(fails)
