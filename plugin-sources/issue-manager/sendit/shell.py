# -*- coding: utf-8 -*-
"""Shell helpers."""

from System.Diagnostics import Process, ProcessStartInfo


def open_path(path):
    """Open a file or folder with its default handler (Explorer for folders).

    Plain Process.Start(path) fails under .NET Core hosts (Revit 2025+,
    where pyRevit runs on .NET 8): UseShellExecute defaults to False there,
    so starting a document/folder path raises Win32Exception 'Access is
    denied'. Routing through ShellExecute restores the .NET Framework
    behaviour on every Revit version.
    """
    psi = ProcessStartInfo()
    psi.FileName = path
    psi.UseShellExecute = True
    Process.Start(psi)
