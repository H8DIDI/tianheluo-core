天河落 THL —— Revit 插件宿主 安装说明（支持 Revit 2020 / Revit 2026）

1. 先关闭 Revit。
2. 把整个压缩包【完整解压】到一个文件夹（不要在压缩包窗口里直接运行）。
3. 双击 install.bat，看到“安装完成”即可。电脑上装了哪个 Revit 就装哪个，两个都装了就两个都装。
4. 打开 Revit，顶部出现「天河落」选项卡。首次打开时选「始终加载」。

如果 install.bat 无法运行，可以手动安装：
  - Revit 2026.5 及以上：打开文件夹 net10；Revit 2026.0 ~ 2026.4：打开文件夹 net8
    把里面的 THL.addin 和 THL 文件夹复制到  %APPDATA%\Autodesk\Revit\Addins\2026
  - Revit 2020：打开文件夹 net472
    把里面的 THL.addin 和 THL 文件夹复制到  %APPDATA%\Autodesk\Revit\Addins\2020
  - 再打开 Revit。

出错请把下面的文件发给供应商：
  %TEMP%\thl-install.log
  Revit 2026：%APPDATA%\Tianheluo\logs\startup.log
  Revit 2020：%APPDATA%\Tianheluo\Revit2020\logs\startup.log

卸载：双击 uninstall.bat（2020 和 2026 都会卸掉；已装的插件保留在 %APPDATA%\Tianheluo）。
