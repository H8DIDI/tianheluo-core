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
    public bool Done;          // false until an analysis has run
    public double Seconds;     // how long the analysis took

    public int Ready => Rows.Count(r => r.Status == "可修改");
    public int Fit => Rows.Count(r => r.Status == "已贴合");
    public int Manual => Rows.Count(r => r.Status == "需人工");

    public string Summary
    {
        get
        {
            if (!Done) return "尚未读取模型";
            string scope = SelectionUsed ? "只看选中的排管" : "整个模型";
            return $"{scope}：排管 {BanksScanned} 根，靠近井的端点 {Rows.Count} 个 → " +
                   $"可修改 {Ready} 个（延伸 {Rows.Count(r => r.Status == "可修改" && r.Kind == SnapKind.Extend)}、" +
                   $"修剪 {Rows.Count(r => r.Status == "可修改" && r.Kind == SnapKind.Trim)}），" +
                   $"已贴合 {Fit} 个，需人工 {Manual} 个。本次将改 {EndsChanged} 个端点、{Edits.Count} 根排管（只改平面位置，标高不变）。";
        }
    }
}

/// <summary>Result of writing the edits. Checked = banks read back and compared; RolledBack = the whole change was undone.</summary>
public sealed record ApplyResult(int BanksChanged, List<string> Failed, int Checked = 0,
    double MaxElevationDiffMm = 0, bool RolledBack = false);

/// <summary>Revit side of 排管贴井内壁: collects banks and wells, reads the inner walls, classifies ends, writes curves.</summary>
public static class DuctSnapEngine
{
    private const double MmPerFoot = 304.8;
    private const double M2PerFt2 = 0.09290304;

    private sealed record WellBox(long Id, string Family, double MinX, double MaxX, double MinY, double MaxY, Element Element, string Key);
    private sealed record BankRec(long Id, string Family, XYZ A, XYZ B);

    /// <summary>
    /// Inner cavities already read in this window's session, keyed by document, well and bounding box.
    /// A well that moves or is changed gets a new key, so stale values are never reused. The plugin is hot-loaded
    /// per click, so this cache lasts for one window session (first check reads the wells, later checks are fast).
    /// </summary>
    private static readonly Dictionary<string, Cavity?> CavityCache = new();

    public static SnapReport Analyse(Document doc, ICollection<ElementId>? selection)
    {
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var rep = new SnapReport();
        var rules = DuctSnapRules.Load(out var err);
        rep.RulesError = err;

        var wells = CollectWells(doc, rules);
        var banks = CollectBanks(doc, rules, selection, rep);
        rep.BanksScanned = banks.Count;

        foreach (var bank in banks)
        {
            var pts = new[] { ToMm(bank.A), ToMm(bank.B) };
            var picked = new (WellBox? Well, SnapDecision Dec)[2];
            bool anyNear = false;
            for (int k = 0; k < 2; k++)
            {
                picked[k] = PickWell(pts[k], pts[1 - k], wells, rules);
                if (picked[k].Well is null)
                {
                    // The end is inside a well's footprint but the bank line never crosses the inner cavity
                    // (e.g. the bank runs along a wall). Do not skip silently: list it for a person to check.
                    // 10 mm slack: an end already moved to the footprint edge must still count as "at the well".
                    var inside = wells.FirstOrDefault(w =>
                        pts[k].X >= w.MinX - 10 && pts[k].X <= w.MaxX + 10 && pts[k].Y >= w.MinY - 10 && pts[k].Y <= w.MaxY + 10);
                    if (inside is not null)
                    {
                        // The bank runs inside the wall: move the end along the bank to the nearest inner wall face ahead of it.
                        var cavInside = CavityOf(inside, rules);
                        var toPlane = cavInside is { } ci ? DuctSnapMath.DistanceToInnerPlane(pts[k], pts[1 - k], ci, rules.ToleranceMm) : null;
                        // Trim (positive) may be long: the excess lies in the wall. Extend (negative) must be short: the end
                        // is already at the well, so a long move means it belongs to a different place.
                        var dec = toPlane is { } e0 && Math.Abs(e0) <= rules.ToleranceMm
                            ? new SnapDecision(SnapKind.Fit, 0)   // already on the inner wall, within tolerance
                            : toPlane is { } e && (e > 0 ? e <= rules.MaxEdgeTrimMm : -e <= rules.MaxExtendMm)
                                ? new SnapDecision(SnapKind.ToInnerWall, e)
                                : new SnapDecision(SnapKind.Unrelated, 0);
                        picked[k] = (inside, dec);
                    }
                }
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
                if (d.Kind is SnapKind.Extend or SnapKind.Trim or SnapKind.ToInnerWall && Math.Abs(d.T) >= rules.ToleranceMm)
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

            // Planned ends. Only the plan position differs from the original; Z is copied from the original end.
            var plannedStart = moved[0] && canEdit ? new XYZ(newMm[0].X / MmPerFoot, newMm[0].Y / MmPerFoot, bank.A.Z) : bank.A;
            var plannedEnd = moved[1] && canEdit ? new XYZ(newMm[1].X / MmPerFoot, newMm[1].Y / MmPerFoot, bank.B.Z) : bank.B;
            if (canEdit) rep.Edits.Add(new BankEdit(bank.Id, plannedStart, plannedEnd));

            for (int k = 0; k < 2; k++)
            {
                if (picked[k].Well is not { } w) continue;
                bool willMove = moved[k] && canEdit;
                rep.Rows.Add(MakeRow(bank, k, w, picked[k].Dec, willMove, shortNote, rules,
                    startBeforeMm: bank.A.Z * MmPerFoot, endBeforeMm: bank.B.Z * MmPerFoot,
                    startAfterMm: plannedStart.Z * MmPerFoot, endAfterMm: plannedEnd.Z * MmPerFoot));
                if (willMove) rep.EndsChanged++;
            }
        }

        if (rep.BanksNearWells == 0) rep.Notes.Add("没有找到靠近井的排管端点（检查井族前缀设置）。");
        int noCav = rep.Rows.Count(r => r.Kind == SnapKind.NoCavity);
        if (noCav > 0) rep.Notes.Add($"有 {noCav} 个端点靠近的井无法识别内壁（已列为需人工）。");
        rep.Seconds = clock.Elapsed.TotalSeconds;
        rep.Done = true;
        return rep;
    }

    /// <summary>Changes the bank curves in one undoable transaction. Failures are listed, the rest still applies.</summary>
    /// <summary>Elevation check: a change of more than this (mm) at either end rolls the whole change back.</summary>
    private const double ZToleranceMm = 0.1;

    /// <summary>Plan check: an end read back more than this (mm) from its planned spot also rolls the change back.</summary>
    private const double PlanToleranceMm = 1.0;

    /// <summary>
    /// Changes the bank curves in one undoable transaction. Before committing, every changed bank is read back from
    /// the model and checked: the ends must sit at the planned spot and keep their elevation. If any bank fails the
    /// check, the whole transaction is rolled back, so the model is left as it was.
    /// </summary>
    public static ApplyResult Apply(Document doc, IEnumerable<BankEdit> edits)
    {
        var failed = new List<string>();
        var changed = new List<(BankEdit Edit, LocationCurve Loc)>();
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
                changed.Add((edit, lc));
            }
            catch (Exception ex)
            {
                failed.Add($"{edit.BankId}：{ex.Message}");
            }
        }
        if (changed.Count == 0)
        {
            tx.RollBack();
            return new ApplyResult(0, failed);
        }

        int checkedCount = 0;
        double maxDz = 0;
        var bad = new List<string>();
        foreach (var (edit, lc) in changed)
        {
            var cur = lc.Curve;
            var (dz, dxy) = DuctSnapMath.CompareEnds(
                ToPt3(edit.Start), ToPt3(edit.End), ToPt3(cur.GetEndPoint(0)), ToPt3(cur.GetEndPoint(1)));
            checkedCount++;
            maxDz = Math.Max(maxDz, dz);
            if (dz > ZToleranceMm || dxy > PlanToleranceMm)
                bad.Add($"{edit.BankId}：端点标高变了 {dz:0.00} mm，位置偏差 {dxy:0.00} mm");
        }
        if (bad.Count > 0)
        {
            tx.RollBack();
            failed.AddRange(bad);
            return new ApplyResult(0, failed, checkedCount, maxDz, RolledBack: true);
        }

        var status = tx.Commit();
        if (status != TransactionStatus.Committed)
        {
            failed.Add("事务未提交：" + status);
            return new ApplyResult(0, failed, checkedCount, maxDz, RolledBack: true);
        }
        return new ApplyResult(changed.Count, failed, checkedCount, maxDz);
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
            double x0 = bb.Min.X * MmPerFoot, x1 = bb.Max.X * MmPerFoot, y0 = bb.Min.Y * MmPerFoot, y1 = bb.Max.Y * MmPerFoot;
            string key = $"{doc.PathName}|{fi.Id.Value}|{x0:0.0}|{y0:0.0}|{x1:0.0}|{y1:0.0}";
            list.Add(new WellBox(fi.Id.Value, fam, x0, x1, y0, y1, fi, key));
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

    private static (WellBox? Well, SnapDecision Dec) PickWell(Pt2 end, Pt2 other, List<WellBox> wells, DuctSnapRules rules)
    {
        WellBox? best = null;
        var bestDec = new SnapDecision(SnapKind.Unrelated, 0);
        int bestRank = int.MaxValue;
        double bestAbs = double.MaxValue;
        double margin = rules.MaxExtendMm;
        foreach (var w in wells)
        {
            if (end.X < w.MinX - margin || end.X > w.MaxX + margin || end.Y < w.MinY - margin || end.Y > w.MaxY + margin) continue;
            var cav = CavityOf(w, rules);
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

    private static Cavity? CavityOf(WellBox w, DuctSnapRules rules)
    {
        if (CavityCache.TryGetValue(w.Key, out var known)) return known;
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
        CavityCache[w.Key] = cav;
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

    private static SnapRow MakeRow(BankRec bank, int k, WellBox w, SnapDecision d, bool willMove, string? shortNote, DuctSnapRules rules,
        double startBeforeMm, double endBeforeMm, double startAfterMm, double endAfterMm)
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
            case SnapKind.ToInnerWall:
                if (willMove && d.T > 0)
                {
                    status = "可修改";
                    action = "修剪到井的内壁";
                    note = $"排管沿井墙走，端点在墙里；修剪掉墙里多出的 {d.T:0} mm，端点停在井的内壁";
                }
                else if (willMove)
                {
                    status = "可修改";
                    action = "延伸到井的内壁";
                    note = $"排管端点已在井外缘，延伸 {-d.T:0} mm 回到井的内壁";
                }
                else
                {
                    status = "需人工";
                    action = "不动";
                    note = shortNote ?? "移动量低于误差，未改";
                }
                break;
            case SnapKind.Unrelated:
                (status, action) = ("需人工", "不动");
                note = "端点在井的墙体里，排管与井内腔不相交；整段都在井内或多出的长度超过上限，需人工确认";
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
            StartZBeforeMm = startBeforeMm,
            StartZAfterMm = startAfterMm,
            EndZBeforeMm = endBeforeMm,
            EndZAfterMm = endAfterMm,
        };
    }

    private static Pt2 ToMm(XYZ p) => new(p.X * MmPerFoot, p.Y * MmPerFoot);
    private static Pt3 ToPt3(XYZ p) => new(p.X * MmPerFoot, p.Y * MmPerFoot, p.Z * MmPerFoot);
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
