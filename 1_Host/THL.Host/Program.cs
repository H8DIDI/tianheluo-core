using System;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using THL.Contracts;

namespace THL.Host
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.SetHighDpiMode(HighDpiMode.SystemAware);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new HostForm());
        }
    }

    /// <summary>宿主悬浮窗：极简、扁平、Revit 蓝。</summary>
    public sealed class HostForm : Form
    {
        private const string PluginsDir = "Plugins";

        public HostForm()
        {
            Text = "THL Host";
            BackColor = System.Drawing.Color.FromArgb(240, 240, 240);
            FormBorderStyle = FormBorderStyle.FixedToolWindow;
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new System.Drawing.Size(320, 160);
            TopMost = true;

            var label = new Label
            {
                Text = "天河落 · 宿主就绪",
                AutoSize = true,
                ForeColor = System.Drawing.Color.FromArgb(0, 121, 193),
                Font = new System.Drawing.Font("Microsoft YaHei UI", 12F),
                Location = new System.Drawing.Point(110, 55)
            };
            Controls.Add(label);

            LoadPlugins();
        }

        private void LoadPlugins()
        {
            var dir = Path.Combine(AppContext.BaseDirectory, PluginsDir);
            if (!Directory.Exists(dir)) return;
            foreach (var dll in Directory.GetFiles(dir, "*.dll"))
            {
                try
                {
                    var asm = Assembly.LoadFrom(dll);
                    foreach (var t in asm.GetTypes())
                    {
                        if (typeof(IPlugin).IsAssignableFrom(t) && !t.IsAbstract && Activator.CreateInstance(t) is IPlugin p)
                        {
                            p.OnLoad(new PluginContext { Log = Console.WriteLine });
                        }
                    }
                }
                catch { /* 插件加载失败不阻断宿主 */ }
            }
        }
    }
}