// .NET Framework 4.7.2 缺的几个小东西（C# 新语法依赖的类型 + 几个 .NET 5+ 才有的 API），让 THL.Host 的源码原样能编。
using System.Runtime.CompilerServices;
using System.Security.Cryptography;

namespace System.Runtime.CompilerServices { internal static class IsExternalInit { } }

namespace THL.Host
{
    internal static class NetCompat
    {
        public static string ToHex(byte[] b) { var sb = new System.Text.StringBuilder(b.Length * 2); foreach (var x in b) sb.Append(x.ToString("x2")); return sb.ToString(); }
        public static byte[] Sha256(byte[] data) { using (var h = SHA256.Create()) return h.ComputeHash(data); }
        public static bool IsAsciiLetterOrDigit(char c) => c < 128 && char.IsLetterOrDigit(c);
        public static Task<string> ReadAllTextAsync(string path) => Task.Run(() => File.ReadAllText(path));
        public static Task<byte[]> ReadAllBytesAsync(string path) => Task.Run(() => File.ReadAllBytes(path));
        public static Task WriteAllBytesAsync(string path, byte[] data) => Task.Run(() => File.WriteAllBytes(path, data));
        public static int ProcessId => System.Diagnostics.Process.GetCurrentProcess().Id;
    }

    internal static class StringCompat
    {
        public static bool Contains(this string s, string value, StringComparison cmp) => s.IndexOf(value, cmp) >= 0;
    }
}
