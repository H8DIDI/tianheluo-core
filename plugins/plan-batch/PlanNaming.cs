namespace KaifaTools.PlanBatch;

/// <summary>
/// Naming rules for the generated plan views. Pure logic (no Revit, no UI) so the picker,
/// the command and the UI smoke test all use the same rules.
/// </summary>
public static class PlanNaming
{
    /// <summary>First row of the template list: plain plan views without a view template.</summary>
    public const string NoTemplateLabel = "（不套用样板）";

    /// <summary>What {样板} becomes for a plain plan view, so the default pattern gives "标高_平面".</summary>
    public const string PlainViewToken = "平面";

    public const string DefaultPattern = "{标高}_{样板}";
    public const string LevelToken = "{标高}";
    public const string TemplateToken = "{样板}";

    // characters Revit does not accept in view names (braces are checked separately, they are placeholders)
    private const string ForbiddenChars = "\\:[]|;<>?`~";

    /// <summary>Null when the pattern can be used, otherwise a message for the user.</summary>
    public static string? Validate(string? pattern)
    {
        string p = pattern?.Trim() ?? "";
        if (p.Length == 0) return "命名规则不能为空。";
        if (!p.Contains(LevelToken, StringComparison.Ordinal)) return "命名规则必须包含 {标高}。";

        string literal = p.Replace(LevelToken, "").Replace(TemplateToken, "");
        if (literal.Contains('{') || literal.Contains('}')) return "只能使用 {标高} 和 {样板} 两个占位符。";
        if (literal.IndexOfAny(ForbiddenChars.ToCharArray()) >= 0) return "命名规则中不能包含这些字符：\\ : [ ] | ; < > ? ` ~";
        return null;
    }

    /// <summary>Name for one level and one template. A null template means a plain plan view.</summary>
    public static string Build(string pattern, string levelName, string? templateName)
        => pattern.Trim().Replace(LevelToken, levelName).Replace(TemplateToken, templateName ?? PlainViewToken);
}
