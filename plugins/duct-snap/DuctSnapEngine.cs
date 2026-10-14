using Autodesk.Revit.DB;

namespace KaifaTools.DuctSnap;

/// <summary>A bank whose endpoints change. Start/End are in Revit internal units (feet); Z is the bank's original elevation.</summary>
public sealed record BankEdit(long BankId, XYZ Start, XYZ End);

/// <summary>Result of one analysis pass. Nothing in the model is changed until <see cref="DuctSnapEngine.Apply"/>.</summary>
public sealed class SnapReport
{
    public List<SnapRow> Rows { get; } = new();
    public List<BankEdit> Edits { get; } = new();
    public int BanksScanned;
    public int BanksNearWells;
    public int EndsChanged;
    public bool SelectionUsed;
    public string? RulesError;
    public List<string> Notes { get; } = new();

    public int Ready => Rows.Count(r => r.Status == "可修改");
    public int Fit => Rows.Count(r => r.Status == "已贴合");
    public int Manual => Rows.Count(r => r.Status == "需人工");

    public string Summary
    {
        get
        {
            string scope = SelectionUsed ? "只看选中的排管" : "整个模型";
            return $"{scope}：排管 {BanksScanned} 根，靠近井的端点 {Rows.Count} 个 → " +
                   $"可修改 {Ready} 个（延伸 {Rows.Count(r => r.Status == "可修改" && r.Kind == SnapKind.Extend)}、" +
                   $"修剪 {Rows.Count(r => r.Status == "可修改" && r.Kind == SnapKind.Trim)}），" +
                   $"已贴合 {Fit} 个，需人工 {Manual} 个。本次将改 {EndsChanged} 个端点、{Edits.Count} 根排管（只改平面位置，标高不变）。";
        }
    }
}

public sealed record ApplyResult(int BanksChanged, List<string> Failed);

/// <summary>Revit side of 排管贴井内壁: collects banks and wells, reads the inner walls, classifies ends, writes curves.</summary>
public static class DuctSnapEngine
{
    private const double MmPerFoot = 304.8;
    private const double M2PerFt2 = 0.09290304;

    private sealed record WellBox(long Id, string Family, double MinX, double MaxX, double MinY, double MaxY, Element Element);
    private sealed record BankRec(long Id, string Family, XYZ A, XYZ B);

    public static SnapReport Analyse(Document doc, ICollection<ElementId>? selection)
    {
        var rep = new SnapReport();
        var rules = DuctSnapRules.Load(out var err);
        rep.RulesError = err;

        var wells = CollectWells(doc, rules);
        var banks = CollectBanks(doc, rules, selection, rep);
        rep.BanksScanned = banks.Count;

        var cache = new Dictionary<long, Cavity?>();
        foreach (var bank in banks)
        {
            var pts = new[] { ToMm(bank.A), ToMm(bank.B) };
            var picked = new (WellBox? Well, SnapDecision Dec)[2];
            bool anyNear = false;
            for (int k = 0; k < 2; k++)
            {
                picked[k] = PickWell(pts[k], pts[1 - k], wells, cache, rules);
                anyNear |= picked[k].Well is not null;
            }
            if (!anyNear) continue;
            rep.BanksNearWells++;

            // Decide the new endpoints. Only Extend / Trim move an end, and only when the move is above tolerance.
            var moved = new[] { false, false };
            var newMm = new[] { pts[0], pts[1] };
            for (int k = 0; k < 2; k++)
            {
                var d = picked[k].Dec;
                if (picked[k].Well is null) continue;
                if (d.Kind is SnapKind.Extend or SnapKind.Trim && Math.Abs(d.T) >= rules.ToleranceMm)
                {
                    moved[k] = true;
                    newMm[k] = DuctSnapMath.Move(pts[k], pts[1 - k], d.T);
                }
            }

            string? shortNote = null;
            bool canEdit = moved[0] || moved[1];
            if (canEdit)
            {
                double newLen = Dist(newMm[0], newMm[1]);
                if (newLen < rules.MinLengthMm)
                {
                    canEdit = false;
                    shortNote = $"改后整根只剩 {newLen:0} mm，短于下限 {rules.MinLengthMm:0} mm，未改";
                }
            }

            if (canEdit)
            {
                var start = moved[0] ? new XYZ(newMm[0].X / MmPerFoot, newMm[0].Y / MmPerFoot, bank.A.Z) : bank.A;
                var end = moved[1] ? new XYZ(newMm[1].X / MmPerFoot, newMm[1].Y / MmPerFoot, bank.B.Z) : bank.B;
                rep.Edits.Add(new BankEdit(bank.Id, start, end));
            }

            for (int k = 0; k < 2; k++)
            {
                if (picked[k].Well is not { } w) continue;
                bool willMove = moved[k] && canEdit;
                rep.Rows.Add(MakeRow(bank, k, w, picked[k].Dec, willMove, shortNote, rules));
                if (willMove) rep.EndsChanged++;
            }
        }

        if (rep.BanksNearWells == 0) rep.Notes.Add("没有找到靠近井的排管端点（检查井族前缀设置）。");
        int noCav = rep.Rows.Count(r => r.Kind == SnapKind.NoCavity);
        if (noCav > 0) rep.Notes.Add($"有 {noCav} 个端点靠近的井无法识别内壁（已列为需人工）。");
        return rep;
    }

    /// <summary>Changes the bank curves in one undoable transaction. Failures are listed, the rest still applies.</summary>
    public static ApplyResult Apply(Document doc, IEnumerable<BankEdit> edits)
    {
        var failed = new List<string>();
        int ok = 0;
        using var tx = new Transaction(doc, "KAIFA 排管贴井内壁");
        tx.Start();
        var fo = tx.GetFailureHandlingOptions();
        fo.SetFailuresPreprocessor(new WarningsOnly());
        tx.SetFailureHandlingOptions(fo);
        foreach (var edit in edits)
        {
            try
            {
                var el = doc.GetElement(new ElementId(edit.BankId));
                if (el?.Location is not LocationCurve lc) throw new InvalidOperationException("不是线状构件");
                lc.Curve = Line.CreateBound(edit.Start, edit.End);
                ok++;
            }
            catch (Exception ex)
            {
                failed.Add($"{edit.BankId}：{ex.Message}");
            }
        }
        if (ok == 0)
        {
            tx.RollBack();
            return new ApplyResult(0, failed);
        }
        var status = tx.Commit();
        if (status != TransactionStatus.Committed)
        {
            failed.Add("事务未提交：" + status);
            return new ApplyResult(0, failed);
        }
        return new ApplyResult(ok, failed);
    }

    // ---- collection ---------------------------------------------------------------

    private static List<WellBox> CollectWells(Document doc, DuctSnapRules rules)
    {
        var list = new List<WellBox>();
        foreach (var e in new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_GenericModel).WhereElementIsNotElementType())
        {
            if (e is not FamilyInstance fi) continue;
            string fam = fi.Symbol.FamilyName;
            if (rules.Exclude.Any(k => fam.Contains(k, StringComparison.Ordinal))) continue;
            if (!rules.WellPrefixes.Any(p => fam.StartsWith(p, StringComparison.Ordinal))) continue;
            var bb = fi.get_BoundingBox(null);
            if (bb is null) continue;
            list.Add(new WellBox(fi.Id.Value, fam,
                bb.Min.X * MmPerFoot, bb.Max.X * MmPerFoot, bb.Min.Y * MmPerFoot, bb.Max.Y * MmPerFoot, fi));
        }
        return list;
    }

    private static List<BankRec> CollectBanks(Document doc, DuctSnapRules rules, ICollection<ElementId>? selection, SnapReport rep)
    {
        var all = new List<(long Id, BankRec Rec)>();
        foreach (var e in new FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_StructuralFraming).WhereElementIsNotElementType())
        {
            string fam = (doc.GetElement(e.GetTypeId()) as ElementType)?.FamilyName ?? "";
            if (rules.Exclude.Any(k => fam.Contains(k, StringComparison.Ordinal))) continue;
            if (!rules.DuctKeywords.Any(k => fam.Contains(k, StringComparison.Ordinal))) continue;
            if (e.Location is not LocationCurve lc || lc.Curve is not Line line || !line.IsBound) continue;
            all.Add((e.Id.Value, new BankRec(e.Id.Value, fam, line.GetEndPoint(0), line.GetEndPoint(1))));
        }

        // If the user selected some banks, check only those. A selection without banks means the whole model.
        if (selection is { Count: > 0 })
        {
            var picked = all.Where(t => selection.Any(id => id.Value == t.Id)).Select(t => t.Rec).ToList();
            if (picked.Count > 0)
            {
                rep.SelectionUsed = true;
                return picked;
            }
        }
        return all.Select(t => t.Rec).ToList();
    }

    // ---- per-end decision -----------------------------------------------------------

    // Lower rank = better. Actionable results first, then results for a person to read, then unknown cavities.
    private static int Rank(SnapKind k) => k switch
    {
        SnapKind.Fit or SnapKind.Extend or SnapKind.Trim => 0,
        SnapKind.NoCavity => 2,
        _ => 1,
    };

    private static (WellBox? Well, SnapDecision Dec) PickWell(Pt2 end, Pt2 other, List<WellBox> wells,
        Dictionary<long, Cavity?> cache, DuctSnapRules rules)
    {
        WellBox? best = null;
        var bestDec = new SnapDecision(SnapKind.Unrelated, 0);
        int bestRank = int.MaxValue;
        double bestAbs = double.MaxValue;
        double margin = rules.MaxExtendMm;
        foreach (var w in wells)
        {
            if (end.X < w.MinX - margin || end.X > w.MaxX + margin || end.Y < w.MinY - margin || end.Y > w.MaxY + margin) continue;
            var cav = CavityOf(w, rules, cache);
            var dec = cav is null
                ? new SnapDecision(SnapKind.NoCavity, 0)
                : DuctSnapMath.Classify(end, other, cav.Value, rules.Limits);
            if (dec.Kind == SnapKind.Unrelated) continue;
            int rank = Rank(dec.Kind);
            double abs = Math.Abs(dec.T);
            if (rank < bestRank || (rank == bestRank && abs < bestAbs))
            {
                best = w;
                bestDec = dec;
                bestRank = rank;
                bestAbs = abs;
            }
        }
        return (best, bestDec);
    }

    private static Cavity? CavityOf(WellBox w, DuctSnapRules rules, Dictionary<long, Cavity?> cache)
    {
        if (cache.TryGetValue(w.Id, out var known)) return known;
        var faces = new List<WallFace>();
        var geom = w.Element.get_Geometry(new Options { DetailLevel = ViewDetailLevel.Fine });
        if (geom is not null)
        {
            foreach (var solid in Solids(geom))
            {
                foreach (Face f in solid.Faces)
                {
                    if (f is not PlanarFace pf) continue;
                    var n = pf.FaceNormal;
                    if (Math.Abs(n.Z) > 0.005) continue; // vertical faces only: walls
                    faces.Add(new WallFace(n.X, n.Y, pf.Origin.X * MmPerFoot, pf.Origin.Y * MmPerFoot, pf.Area * M2PerFt2));
                }
            }
        }
        var cav = DuctSnapMath.FindCavity(faces, rules.MinFaceAreaM2, rules.FaceAreaRatio);
        cache[w.Id] = cav;
        return cav;
    }

    private static IEnumerable<Solid> Solids(IEnumerable<GeometryObject> geometry)
    {
        foreach (var g in geometry)
        {
            if (g is GeometryInstance gi)
            {
                foreach (var inner in Solids(gi.GetInstanceGeometry())) yield return inner;
            }
            else if (g is Solid sol && sol.Volume > 0)
            {
                yield return sol;
            }
        }
    }

    private static SnapRow MakeRow(BankRec bank, int k, WellBox w, SnapDecision d, bool willMove, string? shortNote, DuctSnapRules rules)
    {
        string status, action, note;
        double shown = d.T;
        switch (d.Kind)
        {
            case SnapKind.Fit:
                (status, action, note) = ("已贴合", "不动", "端点已经在井内壁上（误差内）");
                shown = 0;
                break;
            case SnapKind.Extend:
            case SnapKind.Trim:
                if (willMove)
                {
                    status = "可修改";
                    action = d.Kind == SnapKind.Extend ? "延伸到内壁" : "修剪伸入井内的部分";
                    note = d.Kind == SnapKind.Extend
                        ? $"端点离内壁 {Math.Abs(d.T):0} mm，延伸到内壁"
                        : $"端点伸入井内 {d.T:0} mm，修剪回内壁";
                }
                else
                {
                    status = "需人工";
                    action = "不动";
                    note = shortNote ?? "移动量低于误差，未改";
                }
                break;
            case SnapKind.TrimTooFar:
                (status, action) = ("需人工", "不动");
                note = $"端点在井的中部（伸入 {d.T:0} mm，超过修剪上限 {rules.MaxTrimMm:0} mm），请手动决定截断位置";
                break;
            case SnapKind.ExtendTooFar:
                (status, action) = ("需人工", "不动");
                note = $"端点离内壁 {Math.Abs(d.T):0} mm，超过延伸上限 {rules.MaxExtendMm:0} mm，请手动处理";
                break;
            case SnapKind.Through:
                (status, action) = ("需人工", "不动");
                note = "排管穿过这口井（井在端点的排管方向前方），未自动处理";
                break;
            case SnapKind.Inside:
                (status, action) = ("需人工", "不动");
                note = "整根排管都在井内，可能多余，请手动确认";
                break;
            case SnapKind.NoCavity:
                (status, action) = ("需人工", "不动");
                note = "无法识别这口井的内壁（井族几何不规则），未自动处理";
                break;
            default:
                (status, action) = ("需人工", "不动");
                note = "退化排管（长度为零），未处理";
                break;
        }

        return new SnapRow
        {
            BankId = bank.Id,
            EndIndex = k,
            BankFamily = bank.Family,
            WellId = w.Id,
            WellFamily = w.Family,
            Kind = d.Kind,
            MoveMm = shown,
            Status = status,
            Action = action,
            Note = note,
        };
    }

    private static Pt2 ToMm(XYZ p) => new(p.X * MmPerFoot, p.Y * MmPerFoot);
    private static double Dist(Pt2 a, Pt2 b) => Math.Sqrt((a.X - b.X) * (a.X - b.X) + (a.Y - b.Y) * (a.Y - b.Y));

    // Failures that are only warnings are deleted silently so the change goes through.
    private sealed class WarningsOnly : IFailuresPreprocessor
    {
        public FailureProcessingResult PreprocessFailures(FailuresAccessor failures)
        {
            foreach (var msg in failures.GetFailureMessages())
            {
                if (msg.GetSeverity() == FailureSeverity.Warning) failures.DeleteWarning(msg);
            }
            return FailureProcessingResult.Continue;
        }
    }
}
