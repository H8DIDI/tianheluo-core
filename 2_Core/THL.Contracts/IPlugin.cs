using System;

namespace THL.Contracts
{
    /// <summary>插件契约：天河落生态所有插件实现此接口。</summary>
    public interface IPlugin
    {
        string Id { get; }
        string Name { get; }
        string Version { get; }
        void OnLoad(PluginContext context);
        void OnUnload();
    }

    /// <summary>插件运行上下文：宿主注入，提供 UI 宿主与日志能力。</summary>
    public sealed class PluginContext
    {
        public Action<string>? Log { get; init; }
        public Func<object?> GetHostWindow { get; init; } = () => null;
    }
}