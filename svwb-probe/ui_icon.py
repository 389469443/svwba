#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
窗口 / 托盘图标（2026-10-18）

用户给了一张卡面头像作为插件标识，这里统一提供：
  · Tk 窗口图标（任务栏 / 标题栏）
  · 托盘图标用的 .ico 路径（app.py 用 ctypes LoadImageW 加载）

资源文件放在包根 `assets/` 下（`icon.png` / `icon.ico`）。
找不到就安静跳过 —— 不能因为图标把主功能搞崩。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def _candidates(name):
    """兼容工作区（svwb-probe/assets）和打包后（包根/assets）两种位置。"""
    return [
        os.path.join(HERE, "assets", name),
        os.path.join(os.path.dirname(HERE), "assets", name),
        os.path.join(HERE, name),
    ]


def icon_path(name="icon.png"):
    for p in _candidates(name):
        if os.path.isfile(p):
            return p
    return ""


def set_window_icon(root, title=None):
    """
    给 Tk 窗口设置图标：优先 iconphoto（PNG），失败再退回 iconbitmap（ICO）。
    """
    png = icon_path("icon.png")
    ico = icon_path("icon.ico")
    ok = False
    if png:
        try:
            import tkinter as tk
            img = tk.PhotoImage(file=png)
            root.iconphoto(True, img)
            root._icon_photo = img        # 防 GC 把图标回收掉
            ok = True
        except Exception:
            pass
    if not ok and ico:
        try:
            root.iconbitmap(default=ico)
            ok = True
        except Exception:
            pass
    if title:
        try:
            root.title(title)
        except Exception:
            pass
    return ok


def tray_icon_handle():
    """
    给托盘用的 HICON（ctypes）。返回 None 表示没图标文件，调用方自己退回系统图标。
    """
    ico = icon_path("icon.ico")
    if not ico:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.LoadImageW.restype = wintypes.HANDLE
        u.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                 wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        LR_DEFAULTSIZE = 0x0040
        h = u.LoadImageW(None, ico, IMAGE_ICON, 0, 0,
                         LR_LOADFROMFILE | LR_DEFAULTSIZE)
        return h or None
    except Exception:
        return None
