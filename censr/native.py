# -*- coding: utf-8 -*-
"""Нативные штрихи Windows: тёмный заголовок, Mica-подложка, прогресс на таскбаре.

Всё через ctypes/WinAPI, без зависимостей. На не-Windows тихо выключается.
"""

from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"


def keep_awake(on: bool) -> None:
    """Не дать системе уснуть во время обработки (Windows). on=False — снять.
    Экран гасить не мешаем (ES_DISPLAY_REQUIRED не ставим): для обработки не важно."""
    if not IS_WIN:
        return
    try:
        import ctypes
        ES_CONTINUOUS = 0x80000000
        ES_SYSTEM_REQUIRED = 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))
    except Exception:
        pass


def apply_window_effects(widget) -> None:
    """Тёмный заголовок (Win10 1809+) и Mica-подложка (Win11 22H2+)."""
    if not IS_WIN:
        return
    try:
        import ctypes
        from ctypes import wintypes
        hwnd = wintypes.HWND(int(widget.winId()))   # не c_int: HWND > 0x7FFFFFFF
        dwm = ctypes.windll.dwmapi                  # давал OverflowError → эффекты
        TRUE = ctypes.c_int(1)                      # молча пропадали на части машин
        # тёмный титлбар: атрибут 20 (до 1903 — 19)
        for attr in (20, 19):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(TRUE),
                                         ctypes.sizeof(TRUE)) == 0:
                break
        # системная подложка: 38 = DWMWA_SYSTEMBACKDROP_TYPE, 2 = Mica
        backdrop = ctypes.c_int(2)
        dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop),
                                  ctypes.sizeof(backdrop))
    except Exception:
        pass


class TaskbarProgress:
    """Зелёная полоса прогресса на иконке в панели задач (ITaskbarList3)."""

    def __init__(self, widget):
        self._ok = False
        self._ptr = None
        self._co_init = False
        self._release = None
        if not IS_WIN:
            return
        try:
            import ctypes
            from ctypes import wintypes

            self._hwnd = int(widget.winId())
            ole32 = ctypes.windll.ole32
            hr = ole32.CoInitialize(None)
            # S_OK/S_FALSE — инициализировано; отрицательный HRESULT (например,
            # RPC_E_CHANGED_MODE) — нет, и CoUninitialize звать нельзя
            self._co_init = hr >= 0
            if not self._co_init:
                return

            class GUID(ctypes.Structure):
                _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD),
                            ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

            def guid(s):
                g = GUID()
                ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(s), ctypes.byref(g))
                return g

            clsid = guid("{56FDF344-FD6D-11d0-958A-006097C9A090}")
            iid = guid("{EA1AFB91-9E28-4B86-90E9-9E9F8A5EEFAF}")
            ptr = ctypes.c_void_p()
            if ole32.CoCreateInstance(ctypes.byref(clsid), None, 1,
                                      ctypes.byref(iid), ctypes.byref(ptr)) != 0:
                return
            self._ptr = ptr
            # vtable: 3 IUnknown + HrInit(3) ... SetProgressValue(9), SetProgressState(10)
            vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            proto_init = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)
            proto_value = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p,
                                             wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)
            proto_state = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p,
                                             wintypes.HWND, wintypes.DWORD)
            self._set_value = proto_value(vtbl[9])
            self._set_state = proto_state(vtbl[10])
            self._release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])  # IUnknown::Release
            if proto_init(vtbl[3])(ptr) < 0:   # HrInit не удался (нет таскбара/сессии) —
                return                          # не врать, что _ok, на мёртвом интерфейсе
            self._ok = True
        except Exception:
            self._ok = False

    def set(self, percent: int) -> None:
        if self._ok:
            try:
                self._set_value(self._ptr, self._hwnd, int(max(0, min(100, percent))), 100)
            except Exception:
                pass

    def error(self) -> None:
        if self._ok:
            try:
                self._set_state(self._ptr, self._hwnd, 4)  # TBPF_ERROR (красная)
            except Exception:
                pass

    def clear(self) -> None:
        if self._ok:
            try:
                self._set_state(self._ptr, self._hwnd, 0)  # TBPF_NOPROGRESS
            except Exception:
                pass

    def close(self) -> None:
        """Освободить COM-интерфейс и сбалансировать CoInitialize (при выходе)."""
        self._ok = False
        if self._release and self._ptr:
            try:
                self._release(self._ptr)
            except Exception:
                pass
            self._ptr = None
        if self._co_init:
            try:
                import ctypes
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass
            self._co_init = False

    def __del__(self):
        try:
            self.close()         # подстраховка, если closeEvent не вызвался (жёсткий выход)
        except Exception:
            pass
