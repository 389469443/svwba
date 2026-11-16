#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抓屏 —— 从游戏窗口读取当前画面。

关键：用 **PrintWindow(PW_RENDERFULLCONTENT)** 而不是从屏幕 BitBlt。
从屏幕抠图时，只要有窗口盖在游戏上（浏览器、悬浮层），抓到的就是那个窗口，
于是阶段判定变成 other、随从识别 0 张 —— 实测就是这么踩的。
PrintWindow 让窗口自己重绘到内存 DC，**被遮挡也能抓到正确内容**。
"""
import ctypes
import ctypes.wintypes as wt

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

PW_RENDERFULLCONTENT = 0x00000002


class _BIH(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_ulong), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', ctypes.c_ushort),
                ('biBitCount', ctypes.c_ushort), ('biCompression', ctypes.c_ulong),
                ('biSizeImage', ctypes.c_ulong), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', ctypes.c_ulong),
                ('biClrImportant', ctypes.c_ulong)]


def _blank(img):
    """全黑或几乎单色 → 说明没抓到"""
    ex = img.getextrema()
    return not any(lo < hi - 8 for lo, hi in ex)


def grab_screen():
    """返回 PIL.Image（游戏窗口画面），抓不到返回 None"""
    user32 = ctypes.windll.user32
    gdi = ctypes.windll.gdi32
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if cls.value == "UnityWndClass" and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 1000:
                found.append((hwnd, r.left, r.top,
                              r.right - r.left, r.bottom - r.top))
        return True
    user32.EnumWindows(cb, 0)
    if not found:
        return None
    hwnd, x, y, w, h = max(found, key=lambda t: t[3] * t[4])

    hwnd_dc = user32.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hwnd_dc)
    bmp = gdi.CreateCompatibleBitmap(hwnd_dc, w, h)
    gdi.SelectObject(mem, bmp)

    def _to_image():
        bi = _BIH()
        bi.biSize = ctypes.sizeof(_BIH)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')

    img = None
    try:
        ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    except Exception:
        ok = 0
    if ok:
        cand = _to_image()
        if not _blank(cand):
            img = cand
    if img is None:
        try:
            scr = user32.GetDC(0)
            gdi.BitBlt(mem, 0, 0, w, h, scr, x, y, 0x00CC0020)
            user32.ReleaseDC(0, scr)
            cand = _to_image()
            if not _blank(cand):
                img = cand
        except Exception:
            pass

    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hwnd_dc)
    return img
