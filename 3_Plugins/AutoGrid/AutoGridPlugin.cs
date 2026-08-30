using THL.Contracts;

namespace AutoGrid
{
    /// <summary>一键轴网：Revit 快速生成轴网。</summary>
    public sealed class AutoGridPlugin : IPlugin
    {
        public string Id => "autogrid";
        public string Name => "一键轴网";
        public string Version => "0.1.0";

        public void OnLoad(PluginContext context) => context.Log?.Invoke($"[{Id}] 已加载 v{Version}");
        public void OnUnload() { }
    }
}