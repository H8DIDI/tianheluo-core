using Autodesk.Revit.DB;

namespace KaifaTools.TagCheck;

/// <summary>
/// Tag layout: detect overlapping tags and arrange tags neatly beside their elements.
/// Only tag heads move (leaders are kept on); all changes are one transaction, so Ctrl+Z undoes them.
/// </summary>
public static class TagLayout
{
    /// <summary>Distance from the element to the tag head, first try (mm). Grows when a tag still overlaps.</summary>
    public const double FirstOffsetMm = 1200;
    public const double StepMm = 400;
    public const int MaxTries = 4;

    static double Ft(double mm) => UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters);

    /// <summary>Element ids whose tag overlaps another tag in the view (bounding boxes in plan).</summary>
    public static HashSet<long> OverlappingElements(Document doc, Autodesk.Revit.DB.View view)
    {
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
                if (!Overlaps(boxes[i].Box, boxes[j].Box)) continue;
                foreach (var id in boxes[i].Tag.GetTaggedLocalElementIds()) result.Add(id.Value);
                foreach (var id in boxes[j].Tag.GetTaggedLocalElementIds()) result.Add(id.Value);
            }
        return result;
    }

    static bool Overlaps(BoundingBoxXYZ a, BoundingBoxXYZ b) =>
        a.Min.X < b.Max.X && b.Min.X < a.Max.X && a.Min.Y < b.Max.Y && b.Min.Y < a.Max.Y;

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

    /// <summary>
    /// Moves each single-element tag beside its element, on the side it is already on (default: up in plan),
    /// keeps the leader on, and pushes it further out if it still overlaps another tag.
    /// Returns how many tags were moved. Call inside a Transaction.
    /// </summary>
    public static int Arrange(Document doc, Autodesk.Revit.DB.View view)
    {
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
            var dir = new XYZ(head.X - centre.X, head.Y - centre.Y, 0);
            if (dir.GetLength() < 1e-6) dir = new XYZ(0, 1, 0);   // no side yet: go up in plan
            dir = dir.Normalize();

            tag.HasLeader = true;
            XYZ target = centre + dir * Ft(FirstOffsetMm);
            tag.TagHeadPosition = new XYZ(target.X, target.Y, head.Z);
            moved++;
        }

        // push tags that still overlap further out (a few tries)
        for (int attempt = 1; attempt < MaxTries; attempt++)
        {
            var overlaps = OverlappingElements(doc, view);
            if (overlaps.Count == 0) break;
            foreach (var tag in tags)
            {
                var ids = tag.GetTaggedLocalElementIds().ToList();
                if (ids.Count != 1 || !overlaps.Contains(ids[0].Value)) continue;
                var el = doc.GetElement(ids[0]);
                if (el == null) continue;
                var centre = CentreOf(el, view);
                var head = tag.TagHeadPosition;
                var dir = new XYZ(head.X - centre.X, head.Y - centre.Y, 0);
                if (dir.GetLength() < 1e-6) continue;
                dir = dir.Normalize();
                var target = head + dir * Ft(StepMm);
                tag.TagHeadPosition = new XYZ(target.X, target.Y, head.Z);
            }
        }
        return moved;
    }
}
