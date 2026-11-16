# -*- coding: utf-8 -*-
"""验证：直播模式切换后窗口能被 PrintWindow 抓到（平均亮度非全黑）。"""
import ctypes
import ctypes.wintypes as wt
import io
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing", "tools"))
import overlay as OV  # noqa: E402
from PIL import Image  # noqa: E402

u = ctypes.windll.user32
gdi = ctypes.windll.gdi32
try:
    u.SetProcessDPIAware()
except Exception:
    pass

tmp = tempfile.mkdtemp(prefix="live_")
OV.SETTINGS = os.path.join(tmp, "s.json")
OV.POSFILE = os.path.join(tmp, "p.json")
OV.STATE = os.path.join(tmp, "f.json")
with open(OV.SETTINGS, "w", encoding="utf-8") as fh:
    json.dump({"alpha": 0.9, "text_alpha": 1.0, "scale": 1.0,
               "titleColor": "#c9a86a", "locked": True, "liveMode": False}, fh)
with open(OV.POSFILE, "w", encoding="utf-8") as fh:
    json.dump({"x": 320, "y": 320, "scale": 1.0, "locked": True}, fh)
with open(OV.STATE, "w", encoding="utf-8") as fh:
    json.dump({"phase": "battle", "win": 40, "loss": 55, "draw": 5,
               "damageDist": {"toPlayer": [{"damage": 6, "pPercent": 25.0},
                                           {"damage": 7, "pPercent": 75.0}]}}, fh)


class _BIH(ctypes.Structure):
    _fields_ = [('biSize', ctypes.c_ulong), ('biWidth', ctypes.c_long),
                ('biHeight', ctypes.c_long), ('biPlanes', ctypes.c_ushort),
                ('biBitCount', ctypes.c_ushort), ('biCompression', ctypes.c_ulong),
                ('biSizeImage', ctypes.c_ulong), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', ctypes.c_ulong),
                ('biClrImportant', ctypes.c_ulong)]


def capture(hwnd):
    r = wt.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    hdc = u.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hdc)
    bmp = gdi.CreateCompatibleBitmap(hdc, w, h)
    gdi.SelectObject(mem, bmp)
    try:
        u.PrintWindow(hwnd, mem, 2)
    except Exception:
        pass
    bi = _BIH()
    bi.biSize = ctypes.sizeof(_BIH)
    bi.biWidth = w
    bi.biHeight = -h
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    im = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')
    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    u.ReleaseDC(hwnd, hdc)
    return im


def mean_brightness(im):
    g = im.convert("L")
    px = list(g.getdata())
    return sum(px) / max(1, len(px))


FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


ov = OV.Overlay(demo=False)
for _ in range(12):
    ov.root.update()
    time.sleep(0.05)
hwnd = ov._hwnd()
geo0 = ov.root.geometry()
im0 = capture(hwnd)
m0 = mean_brightness(im0)
print("透明模式 PrintWindow 平均亮度:", round(m0, 2), "geometry:", geo0)

# 热切换：写设置文件 → _poll_hot
with open(OV.SETTINGS, "w", encoding="utf-8") as fh:
    json.dump({"alpha": 0.9, "text_alpha": 1.0, "scale": 1.0,
               "titleColor": "#c9a86a", "locked": True, "liveMode": True}, fh)
for _ in range(4):
    ov.root.update()
    time.sleep(0.1)
ov._poll_hot()
for _ in range(8):
    ov.root.update()
    time.sleep(0.05)
im1 = capture(hwnd)
m1 = mean_brightness(im1)
print("直播模式 PrintWindow 平均亮度:", round(m1, 2), "geometry:", ov.root.geometry())
ex1 = u.GetWindowLongW(hwnd, -20)
check("直播模式被热切换开启", ov.live_mode is True)
check("直播模式抓图非全黑（平均亮度 > 10）", m1 > 10, round(m1, 2))
check("直播模式已去掉 WS_EX_LAYERED（OBS 可采集）",
      not (ex1 & 0x00080000), hex(ex1))
check("直播模式 alpha = 1.0",
      abs(float(ov.root.attributes("-alpha")) - 1.0) < 0.01,
      ov.root.attributes("-alpha"))
check("直播模式已关闭色键",
      str(ov.root.attributes("-transparentcolor")).lower() == "#f0f1f2",
      ov.root.attributes("-transparentcolor"))
check("切换前后位置/大小不变", ov.root.geometry() == geo0, ov.root.geometry())
im1.save(os.path.join(HERE, "logs", "验证_直播模式_PrintWindow.png"))

# 切回透明
with open(OV.SETTINGS, "w", encoding="utf-8") as fh:
    json.dump({"alpha": 0.9, "text_alpha": 1.0, "scale": 1.0,
               "titleColor": "#c9a86a", "locked": True, "liveMode": False}, fh)
ov._poll_hot()
for _ in range(8):
    ov.root.update()
    time.sleep(0.05)
im2 = capture(hwnd)
m2 = mean_brightness(im2)
print("切回透明后平均亮度:", round(m2, 2))
ex0 = u.GetWindowLongW(hwnd, -20)
check("能切回透明模式", ov.live_mode is False)
check("切回后恢复 WS_EX_LAYERED", bool(ex0 & 0x00080000), hex(ex0))
check("切回后 alpha 恢复 < 1.0",
      float(ov.root.attributes("-alpha")) < 0.99,
      ov.root.attributes("-alpha"))

try:
    ov.root.destroy()
except Exception:
    pass
print("\n=== 结果：%d 项失败 ===" % len(FAILS))
sys.exit(1 if FAILS else 0)
