using System.Drawing;
using System.Windows.Forms;

namespace KaifaTools;

/// <summary>
/// 高分屏适配：窗口里的像素（窗口大小、列宽、行高、输入框宽度）都按 100% 缩放写的，
/// 屏幕缩放 125%/150% 时字跟着变大、框不变，表格行和说明文字就被截掉。
/// 在窗体构造函数末尾（控件都加好之后、填数据之前）调用 <see cref="Apply"/>。
/// </summary>
internal static class DpiFit
{
    public static void Apply(Form form)
    {
        float k = form.DeviceDpi / 96f;
        if (k < 1.01f) return;
        form.AutoScaleMode = AutoScaleMode.None;
        Size min = form.MinimumSize;
        form.MinimumSize = Size.Empty;
        form.Scale(new SizeF(k, k));       // 位置、大小、表格布局固定列宽、控件的最小最大尺寸
        Walk(form, k);
        Rectangle wa = Screen.FromPoint(Cursor.Position).WorkingArea;
        if (!min.IsEmpty)
            form.MinimumSize = new Size(Math.Min((int)(min.Width * k), wa.Width), Math.Min((int)(min.Height * k), wa.Height));
        form.Size = new Size(Math.Min(form.Width, wa.Width), Math.Min(form.Height, wa.Height));
    }

    // .NET 不会跟着放大的：表格列宽、行高、表头高度，列表视图的列宽
    private static void Walk(Control parent, float k)
    {
        foreach (Control c in parent.Controls)
        {
            if (c is DataGridView g)
            {
                foreach (DataGridViewColumn col in g.Columns)
                {
                    if (col.AutoSizeMode == DataGridViewAutoSizeColumnMode.Fill) continue;
                    if (col.MinimumWidth > 2) col.MinimumWidth = (int)(col.MinimumWidth * k);
                    col.Width = (int)(col.Width * k);
                }
                g.RowTemplate.Height = (int)(g.RowTemplate.Height * k);
                if (g.ColumnHeadersHeightSizeMode != DataGridViewColumnHeadersHeightSizeMode.AutoSize)
                    g.ColumnHeadersHeight = (int)(g.ColumnHeadersHeight * k);
                if (g.RowHeadersVisible) g.RowHeadersWidth = (int)(g.RowHeadersWidth * k);
            }
            else if (c is ListView lv)
            {
                foreach (ColumnHeader col in lv.Columns)
                    if (col.Width > 0) col.Width = (int)(col.Width * k);
            }
            if (c.Controls.Count > 0) Walk(c, k);
        }
    }
}
