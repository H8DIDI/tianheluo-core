# -*- coding: utf-8 -*-
"""WPF data-binding helpers for IronPython row models."""

from System.ComponentModel import INotifyPropertyChanged, PropertyChangedEventArgs


class Notifiable(INotifyPropertyChanged):
    """INotifyPropertyChanged via the IronPython add_/remove_ event pattern.

    Subclasses call self._notify("PropName") from property setters so WPF
    grids refresh, and windows can subscribe with add_PropertyChanged() to
    react to user edits (e.g. staged-change counters).
    """

    def __init__(self):
        self._handlers = []

    def add_PropertyChanged(self, handler):
        self._handlers.append(handler)

    def remove_PropertyChanged(self, handler):
        if handler in self._handlers:
            self._handlers.remove(handler)

    def _notify(self, prop):
        args = PropertyChangedEventArgs(prop)
        for handler in list(self._handlers):
            handler(self, args)
