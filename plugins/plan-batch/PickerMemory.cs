using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;

namespace KaifaTools.PlanBatch;

/// <summary>
/// Last selection of the picker. Stored by name, not by index, so it still works after the model changes.
/// Names that no longer exist in the model are ignored when the picker opens.
/// </summary>
public sealed record PickerMemory(IReadOnlyList<string> Templates, IReadOnlyList<string> Levels, string NamePattern);

/// <summary>
/// Reads and writes the picker memory file (Documents\KaifaTools\批量平面图记忆.json).
/// Never throws: a missing or broken file just means nothing is remembered.
/// </summary>
public static class PickerMemoryStore
{
    public static string DefaultPath
    {
        get
        {
            string docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
            if (string.IsNullOrEmpty(docs)) docs = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return Path.Combine(docs, "KaifaTools", "批量平面图记忆.json");
        }
    }

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        PropertyNameCaseInsensitive = true,
        // keep the Chinese names readable in the file instead of \uXXXX escapes
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    private sealed class FileModel
    {
        public List<string?>? Templates { get; set; }
        public List<string?>? Levels { get; set; }
        public string? NamePattern { get; set; }
    }

    /// <summary>Returns the remembered selection, or null when there is no usable file.</summary>
    public static PickerMemory? Load(string? path = null)
    {
        try
        {
            string file = path ?? DefaultPath;
            if (!File.Exists(file)) return null;
            var model = JsonSerializer.Deserialize<FileModel>(File.ReadAllText(file, Encoding.UTF8), Options);
            if (model is null) return null;
            return new PickerMemory(Clean(model.Templates), Clean(model.Levels), model.NamePattern ?? "");
        }
        catch (Exception)
        {
            return null;
        }
    }

    /// <summary>Writes the selection. Returns false when the file could not be written; never throws.</summary>
    public static bool Save(PickerMemory memory, string? path = null)
    {
        try
        {
            string file = path ?? DefaultPath;
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(file))!);
            var model = new FileModel
            {
                Templates = memory.Templates.Cast<string?>().ToList(),
                Levels = memory.Levels.Cast<string?>().ToList(),
                NamePattern = memory.NamePattern,
            };
            // write to a temp file first, so a crash mid-write does not leave a broken memory file
            string tmp = file + ".tmp";
            File.WriteAllText(tmp, JsonSerializer.Serialize(model, Options), Encoding.UTF8);
            File.Move(tmp, file, overwrite: true);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static List<string> Clean(IEnumerable<string?>? names)
        => names?.Where(n => !string.IsNullOrWhiteSpace(n)).Select(n => n!).ToList() ?? new List<string>();
}
