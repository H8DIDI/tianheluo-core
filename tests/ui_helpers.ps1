# Revit 界面自动化小工具（UI Automation + 真实鼠标点击 + 单窗口截图）。供端到端测试脚本 dot-source 使用。
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing
Add-Type @"
using System; using System.Runtime.InteropServices;
public class UIH {
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
 [StructLayout(LayoutKind.Sequential)] public struct R { public int L,T,Rt,B; } }
"@
$script:AE = [System.Windows.Automation.AutomationElement]
$script:TS = [System.Windows.Automation.TreeScope]
$script:SHOTS = "C:\Users\PCB\AppData\Local\Temp\claude\C--KAIFA\90a925ad-2baa-4535-a5f3-989e0ce735d2\scratchpad"

function Get-RevitProc { Get-Process Revit -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle } | Select-Object -First 1 }
function Get-Wins { $p = Get-RevitProc; $script:AE::RootElement.FindAll($script:TS::Children, (New-Object System.Windows.Automation.PropertyCondition($script:AE::ProcessIdProperty, $p.Id))) }
function Find-Win($like) { foreach ($w in (Get-Wins)) { if ($w.Current.Name -like $like) { return $w } } }
function Find-ByName($parent, $name) { $parent.FindFirst($script:TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($script:AE::NameProperty, $name))) }
function Find-AllByName($parent, $name) { $parent.FindAll($script:TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($script:AE::NameProperty, $name))) }
function Click-El($el) {
    $r = $el.Current.BoundingRectangle
    [void][UIH]::SetCursorPos([int]($r.X + $r.Width / 2), [int]($r.Y + $r.Height / 2)); Start-Sleep -Milliseconds 250
    [UIH]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero); [UIH]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
}
function Shot-Win($w, $file) {
    $h = [IntPtr]$w.Current.NativeWindowHandle; $r = New-Object UIH+R; [void][UIH]::GetWindowRect($h, [ref]$r)
    $b = New-Object System.Drawing.Bitmap ($r.Rt - $r.L), ($r.B - $r.T); $g = [System.Drawing.Graphics]::FromImage($b)
    $dc = $g.GetHdc(); [void][UIH]::PrintWindow($h, $dc, 2); $g.ReleaseHdc($dc); $g.Dispose(); $b.Save((Join-Path $script:SHOTS $file))
}
function Texts-Of($w) {
    $w.FindAll($script:TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($script:AE::ControlTypeProperty, [System.Windows.Automation.ControlType]::Text))) | ForEach-Object { $_.Current.Name }
}
function Close-Win($w) { $wp = $null; if ($w.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$wp)) { $wp.Close(); $true } else { $false } }
