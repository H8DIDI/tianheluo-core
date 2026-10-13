using Autodesk.Revit.DB;

namespace KaifaTools.TagCheck;

/// <summary>
/// Tag layout: find crowded tags, check tag positions, and arrange tags neatly beside their elements.
/// Only tag heads move (leaders are kept on); all changes are one transaction, so Ctrl+Z undoes them.
/// Distances, side and gaps come from the "layout" section of the standard file (TagLayoutSettings).
/// </summary>
public static class TagLayout
{
    /// <summary>Smallest push-out step (mm) for a tag that still overlaps. The step is never smaller than minGapMm.</summary>
    public const double StepMm = 400;

    /// <summary>Placement passes: the first placement plus up to MaxTries - 1 push-outs.</summary>
    public const int MaxTries = 4;

    const string CropNote = "标注超出视图裁剪框";

    static double Ft(double mm) => UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters);
    static double Mm(double ft) => UnitUtils.ConvertFromInternalUnits(ft, UnitTypeId.Millimeters);

    /// <summary>Element ids whose tag is closer than minGapMm to another tag in the view (bounding boxes in plan).</summary>
    public static HashSet<long> OverlappingElements(Document doc, Autodesk.Revit.DB.View view, double minGapMm = 0)
    {
        double gap = Ft(minGapMm);
        var result = new HashSet<long>();
        var tags = new FilteredElementCollector(doc, view.Id).OfClass(typeof(IndependentTag)).Cast<IndependentTag>().ToList();
        var boxes = new List<(IndependentTag Tag, BoundingBoxXYZ Box)>();
        foreach (var t in tags)
        {
            var bb = t.get_BoundingBox(view);
            if (bb != null) boxes.Add((t, bb));
        }
        for (int i = 0; i < boxes.Count; i++)
            for (int j = i + 1; j < boxes.Count; j++)
            {
                if (!Overlaps(boxes[i].Box, boxes[j].Box, gap)) continue;
                foreach (var id in boxes[i].Tag.GetTaggedLocalElementIds()) result.Add(id.Value);
                foreach (var id in boxes[j].Tag.GetTaggedLocalElementIds()) result.Add(id.Value);
            }
        return result;
    }

    /// <summary>True when the two boxes are closer than gap in plan (gap 0 means they really overlap).</summary>
    static bool Overlaps(BoundingBoxXYZ a, BoundingBoxXYZ b, double gap) =>
        a.Min.X < b.Max.X + gap && b.Min.X < a.Max.X + gap && a.Min.Y < b.Max.Y + gap && b.Min.Y < a.Max.Y + gap;

    /// <summary>Centre of a model element in plan (the middle of a linear element, or the box centre).</summary>
    static XYZ CentreOf(Element e, Autodesk.Revit.DB.View view)
    {
        if (e.Location is LocationCurve lc)
            return lc.Curve.Evaluate(0.5, true);
        if (e.Location is LocationPoint lp)
            return lp.Point;
        var bb = e.get_BoundingBox(view) ?? e.get_BoundingBox(null);
        return bb == null ? XYZ.Zero : (bb.Min + bb.Max) / 2.0;
    }

    static double PlanLength(XYZ v) => Math.Sqrt(v.X * v.X + v.Y * v.Y);

    /// <summary>Unit plan direction from one point to another. False when the two points coincide.</summary>
    static bool TryPlanDirection(XYZ from, XYZ to, out XYZ dir)
    {
        var d = new XYZ(to.X - from.X, to.Y - from.Y, 0);
        if (PlanLength(d) < 1e-6) { dir = XYZ.Zero; return false; }
        dir = d.Normalize();
        return true;
    }

    static XYZ PlanOrDefault(XYZ v, XYZ fallback)
    {
        var flat = new XYZ(v.X, v.Y, 0);
        return flat.GetLength() < 1e-9 ? fallback : flat.Normalize();
    }

    /// <summary>The plan direction of a fixed side on the sheet (up / down / left / right). Null for "auto".</summary>
    static XYZ? FixedDirection(Autodesk.Revit.DB.View view, string side)
    {
        if (side == "auto") return null;
        var up = PlanOrDefault(view.UpDirection, new XYZ(0, 1, 0));
        var right = PlanOrDefault(view.RightDirection, new XYZ(1, 0, 0));
        return side switch
        {
            "up" => up,
            "down" => XYZ.Zero - up,
            "left" => XYZ.Zero - right,
            "right" => right,
            _ => null,
        };
    }

    /// <summary>
    /// Moves each single-element tag beside its element and keeps the leader on. With side "auto" a tag stays on
    /// the side it is already on (default: up in plan); with a fixed side it goes to that side of the sheet.
    /// Tags that are still closer than minGapMm to another tag are pushed further out, but never beyond
    /// maxLeaderMm from their element. Returns how many tags were moved. Call inside a Transaction.
    /// </summary>
    public static int Arrange(Document doc, Autodesk.Revit.DB.View view, TagLayoutSettings settings)
    {
        var fixedDir = FixedDirection(view, settings.Side);
        double offset = Ft(settings.OffsetMm);
        double step = Ft(Math.Max(StepMm, settings.MinGapMm));
        double maxLeader = Ft(settings.MaxLeaderMm);

        var tags = new FilteredElementCollector(doc, view.Id).OfClass(typeof(IndependentTag)).Cast<IndependentTag>().ToList();
        int moved = 0;
        foreach (var tag in tags)
        {
            var ids = tag.GetTaggedLocalElementIds().ToList();
            if (ids.Count != 1) continue;                       // multi-element tags are left alone
            var el = doc.GetElement(ids[0]);
            if (el == null) continue;

            var centre = CentreOf(el, view);
            var head = tag.TagHeadPosition;
            XYZ dir;
            if (fixedDir != null) dir = fixedDir;
            else if (TryPlanDirection(centre, head, out var current)) dir = current;   // keep the current side
            else dir = new XYZ(0, 1, 0);                                              // no side yet: go up in plan

            tag.HasLeader = true;
            XYZ target = centre + dir * offset;
            tag.TagHeadPosition = new XYZ(target.X, target.Y, head.Z);
            moved++;
        }

        // push tags that still overlap further out (a few tries), never beyond maxLeaderMm from the element
        for (int attempt = 1; attempt < MaxTries; attempt++)
        {
            var overlaps = OverlappingElements(doc, view, settings.MinGapMm);
            if (overlaps.Count == 0) break;
            foreach (var tag in tags)
            {
                var ids = tag.GetTaggedLocalElementIds().ToList();
                if (ids.Count != 1 || !overlaps.Contains(ids[0].Value)) continue;
                var el = doc.GetElement(ids[0]);
                if (el == null) continue;
                var centre = CentreOf(el, view);
                var head = tag.TagHeadPosition;
                if (!TryPlanDirection(centre, head, out var dir)) continue;
                var target = head + dir * step;
                if (PlanLength(new XYZ(target.X - centre.X, target.Y - centre.Y, 0)) > maxLeader) continue;
                tag.TagHeadPosition = new XYZ(target.X, target.Y, head.Z);
            }
        }
        return moved;
    }

    /// <summary>
    /// Position problems per element (element id -> note): the tag head is farther than maxLeaderMm from the
    /// element (measured in plan to the nearest point of the element), or the tag head is outside the view crop box.
    /// The crop check is skipped when the crop is not active. Only tags in this view are checked.
    /// </summary>
    public static Dictionary<long, string> PositionWarnings(Document doc, Autodesk.Revit.DB.View view, TagLayoutSettings settings)
    {
        var notes = new Dictionary<long, List<string>>();
        BoundingBoxXYZ? crop = view.CropBoxActive ? view.CropBox : null;
        double maxLeader = Ft(settings.MaxLeaderMm);

        var tags = new FilteredElementCollector(doc, view.Id).OfClass(typeof(IndependentTag)).Cast<IndependentTag>().ToList();
        foreach (var tag in tags)
        {
            var head = tag.TagHeadPosition;
            bool outsideCrop = crop != null && !InsideCrop(crop, head);
            foreach (var id in tag.GetTaggedLocalElementIds())
            {
                var el = doc.GetElement(id);
                if (el == null) continue;
                if (!notes.TryGetValue(id.Value, out var list)) notes[id.Value] = list = new List<string>();

                double distFt = DistanceToElement(el, head, view);
                if (distFt > maxLeader)
                    AddOnce(list, $"引线约 {Mm(distFt):0} mm，超过 {settings.MaxLeaderMm:0} mm");
                if (outsideCrop)
                    AddOnce(list, CropNote);
            }
        }
        return notes.Where(kv => kv.Value.Count > 0).ToDictionary(kv => kv.Key, kv => string.Join("；", kv.Value));
    }

    static void AddOnce(List<string> list, string text)
    {
        if (!list.Contains(text)) list.Add(text);
    }

    /// <summary>Plan distance (internal units) from a point to the nearest part of an element.</summary>
    static double DistanceToElement(Element el, XYZ p, Autodesk.Revit.DB.View view)
    {
        if (el.Location is LocationCurve lc)
        {
            var hit = lc.Curve.Project(p);
            if (hit != null) return PlanLength(new XYZ(p.X - hit.XYZPoint.X, p.Y - hit.XYZPoint.Y, 0));
        }
        else if (el.Location is LocationPoint lp)
        {
            return PlanLength(new XYZ(p.X - lp.Point.X, p.Y - lp.Point.Y, 0));
        }
        var bb = el.get_BoundingBox(view) ?? el.get_BoundingBox(null);
        if (bb == null) return 0;
        double dx = Math.Max(0, Math.Max(bb.Min.X - p.X, p.X - bb.Max.X));
        double dy = Math.Max(0, Math.Max(bb.Min.Y - p.Y, p.Y - bb.Max.Y));
        return Math.Sqrt(dx * dx + dy * dy);
    }

    /// <summary>True when the point lies inside the crop box in plan (view X and Y range; the view depth is not checked).</summary>
    static bool InsideCrop(BoundingBoxXYZ box, XYZ p)
    {
        var local = box.Transform.Inverse.OfPoint(p);
        const double eps = 1e-6;
        return local.X >= box.Min.X - eps && local.X <= box.Max.X + eps
            && local.Y >= box.Min.Y - eps && local.Y <= box.Max.Y + eps;
    }
}
