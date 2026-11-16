# -*- coding: utf-8 -*-
"""独立验证：战绩面板背景/文字不透明度分开控制 + 500ms 热更新。"""
import ctypes
import json
import os
import sys
import tempfile
import time

import tkinter as tk
from PIL import ImageGrab

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import panel_ui as PU  # noqa: E402

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

tmp = tempfile.mkdtemp(prefix="panel_alpha_")
PU.PANEL_SET = os.path.join(tmp, "panel-settings.json")
PU.POSF = os.path.join(tmp, "panel-pos.json")
with open(PU.POSF, "w", encoding="utf-8") as fh:
    json.dump({"x": 700, "y": 300, "locked": False}, fh)


def write_set(alpha, text_alpha):
    with open(PU.PANEL_SET, "w", encoding="utf-8") as fh:
        json.dump({"alpha": alpha, "text_alpha": text_alpha, "scale": 1.8,
                   "force_topmost": True, "hero_display": "both"}, fh)


def pump(root, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.03)


def grab(root, name):
    import ctypes.wintypes as wt
    r = wt.RECT()
    h = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
    ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
    ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom),
                   all_screens=True).save(os.path.join(HERE, "logs", name))
    print("saved", name, (r.left, r.top, r.right - r.left, r.bottom - r.top))


FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


write_set(0.5, 1.0)
root = tk.Tk()
p = PU.Panel(root)
pump(root, 1.0)
print("initial alpha:", p._alpha, "text:", p._text_alpha,
      "root alpha:", root.attributes("-alpha"))
check("背景 0.5 生效", abs(float(root.attributes("-alpha")) - 0.5) < 0.01,
      root.attributes("-alpha"))
check("文字 1.0 生效", abs(p._text_alpha - 1.0) < 0.01)
grab(root, "验证_面板_背景50_文字100.png")

# 热更新：控制台改文件 → 面板 500ms 内应用
write_set(0.9, 0.6)
pump(root, 1.5)
print("after live update alpha:", p._alpha, "text:", p._text_alpha)
check("热更新背景 0.9", abs(float(root.attributes("-alpha")) - 0.9) < 0.01,
      root.attributes("-alpha"))
check("热更新文字 0.6", abs(p._text_alpha - 0.6) < 0.01, p._text_alpha)
grab(root, "验证_面板_背景90_文字60.png")

try:
    root.destroy()
except Exception:
    pass
print("\n=== 结果：%d 项失败 ===" % len(FAILS))
sys.exit(1 if FAILS else 0)
