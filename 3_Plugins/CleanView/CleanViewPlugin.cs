using THL.Contracts;

namespace CleanView
{
    /// <summary>视图清理：一键清理 Revit 无用视图。</summary>
    public sealed class CleanViewPlugin : IPlugin
    {
        public string Id => "cleanview";
        public string Name => "视图清理";
        public string Version => "0.1.0";

        public void OnLoad(PluginContext context) => context.Log?.Invoke($"[{Id}] 已加载 v{Version}");
        public void OnUnload() { }
    }
}