// ElementId 新旧写法兼容（2024 起 ElementId 是 long 的 .Value；Revit 2020 只有 int 的 .IntegerValue）。
// 插件代码统一写 id.V() 和 MakeEid(n)，两个版本都能编。放在全局命名空间，任何插件不用 using 就能用。
internal static class EidCompat
{
#if NETFRAMEWORK
    public static long V(this Autodesk.Revit.DB.ElementId id) => id.IntegerValue;
    public static Autodesk.Revit.DB.ElementId MakeEid(long v) => new Autodesk.Revit.DB.ElementId((int)v);
#else
    public static long V(this Autodesk.Revit.DB.ElementId id) => id.Value;
    public static Autodesk.Revit.DB.ElementId MakeEid(long v) => new Autodesk.Revit.DB.ElementId(v);
#endif
}

internal static class TagCompat
{
    /// <summary>标记指向的本文档构件（2022+ 可多个；2020 只有一个）。</summary>
    public static System.Collections.Generic.ICollection<Autodesk.Revit.DB.ElementId> TaggedIds(this Autodesk.Revit.DB.IndependentTag t)
    {
#if NETFRAMEWORK
        var id = t.TaggedLocalElementId;
        var list = new System.Collections.Generic.List<Autodesk.Revit.DB.ElementId>();
        if (id != null && id != Autodesk.Revit.DB.ElementId.InvalidElementId) list.Add(id);
        return list;
#else
        return t.GetTaggedLocalElementIds();
#endif
    }
}
