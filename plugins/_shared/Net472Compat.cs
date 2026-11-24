// 只在 Revit 2020（net472）编译时引入：补上 C# 新语法依赖的类型。
namespace System.Runtime.CompilerServices { internal static class IsExternalInit { } }

// C# 8 的 ^1、a..b 语法在 .NET Framework 里缺 Index / Range 类型，这里补上（与 .NET 自带实现等价的精简版）。
namespace System
{
    internal readonly struct Index : IEquatable<Index>
    {
        private readonly int _value;
        public Index(int value, bool fromEnd = false) { if (value < 0) throw new ArgumentOutOfRangeException(nameof(value)); _value = fromEnd ? ~value : value; }
        private Index(int value) { _value = value; }
        public static Index Start => new Index(0);
        public static Index End => new Index(~0);
        public static Index FromStart(int value) => new Index(value);
        public static Index FromEnd(int value) => new Index(~value);
        public int Value => _value < 0 ? ~_value : _value;
        public bool IsFromEnd => _value < 0;
        public int GetOffset(int length) { int o = _value; if (IsFromEnd) o += length + 1; return o; }
        public override bool Equals(object? value) => value is Index i && _value == i._value;
        public bool Equals(Index other) => _value == other._value;
        public override int GetHashCode() => _value;
        public static implicit operator Index(int value) => FromStart(value);
        public override string ToString() => IsFromEnd ? "^" + Value : Value.ToString();
    }

    internal readonly struct Range : IEquatable<Range>
    {
        public Index Start { get; }
        public Index End { get; }
        public Range(Index start, Index end) { Start = start; End = end; }
        public override bool Equals(object? value) => value is Range r && r.Start.Equals(Start) && r.End.Equals(End);
        public bool Equals(Range other) => other.Start.Equals(Start) && other.End.Equals(End);
        public override int GetHashCode() => Start.GetHashCode() * 31 + End.GetHashCode();
        public static Range StartAt(Index start) => new Range(start, Index.End);
        public static Range EndAt(Index end) => new Range(Index.Start, end);
        public static Range All => new Range(Index.Start, Index.End);
        public (int Offset, int Length) GetOffsetAndLength(int length)
        {
            int s = Start.GetOffset(length), e = End.GetOffset(length);
            if ((uint)e > (uint)length || (uint)s > (uint)e) throw new ArgumentOutOfRangeException(nameof(length));
            return (s, e - s);
        }
    }
}

namespace System.Runtime.CompilerServices
{
    internal static class RuntimeHelpers
    {
        public static T[] GetSubArray<T>(T[] array, Range range)
        {
            var (offset, length) = range.GetOffsetAndLength(array.Length);
            var dest = new T[length];
            Array.Copy(array, offset, dest, 0, length);
            return dest;
        }
    }
}
