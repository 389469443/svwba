# -*- coding: utf-8 -*-
"""
单实例守卫：同名窗口已经存在时直接退出。

为什么需要：控制台拉起一份 + 手动再拉一份 → 屏幕上出现两个一模一样的面板。
"""
import ctypes
import ctypes.wintypes as wt


def already_running(title_part):
    """
    如果已经存在包含 `title_part` 的**可见**顶层窗口，返回 True。

    注意排除自己：调用时机在 `mainloop` 之前，此时自己的窗口还没 map，
    所以不会把自己算进去。
    """
    if not title_part:
        return False
    u = ctypes.windll.user32
    hits = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _):
        if not u.IsWindowVisible(hwnd):
            return True
        n = u.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if title_part in buf.value:
            hits.append(hwnd)
        return True

    try:
        u.EnumWindows(_cb, 0)
    except Exception:
        return False
    return len(hits) > 0
