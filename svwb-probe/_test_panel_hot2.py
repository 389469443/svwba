# -*- coding: utf-8 -*-
"""验证战绩面板：尺寸 / 锁定 / 位置 热更新。"""
import ctypes
import json
import os
import sys
import tempfile
import time

import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import panel_ui as PU  # noqa: E402

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

tmp = tempfile.mkdtemp(prefix="panel_hot_")
PU.PANEL_SET = os.path.join(tmp, "panel-settings.json")
PU.POSF = os.path.join(tmp, "panel-pos.json")
with open(PU.PANEL_SET, "w", encoding="utf-8") as fh:
    json.dump({"alpha": 0.5, "text_alpha": 1.0, "scale": 1.0,
               "force_topmost": True, "hero_display": "both"}, fh)
with open(PU.POSF, "w", encoding="utf-8") as fh:
    json.dump({"x": 700, "y": 300, "locked": False}, fh)

FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def write(p, d):
    tmpf = p + ".tmp"
    with open(tmpf, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmpf, p)


def pump(root, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.03)


root = tk.Tk()
p = PU.Panel(root)
pump(root, 0.8)
check("初始尺寸 1.0", abs(p._scale - 1.0) < 0.01, p._scale)

write(PU.PANEL_SET, {"alpha": 0.7, "text_alpha": 1.0, "scale": 1.4,
                     "force_topmost": True, "hero_display": "both",
                     "locked": True})
pump(root, 1.5)
check("尺寸热更新到 1.4", abs(p._scale - 1.4) < 0.01, p._scale)
check("锁定热更新为 True", p.locked is True)
check("锁定按钮变 🔒", p.lock_btn.cget("text") == "🔒")
check("输入框变只读",
      p._note_entries and p._note_entries[0].cget("state") == "readonly")

write(PU.POSF, {"x": 820, "y": 420, "locked": True})
pump(root, 1.2)
check("位置热更新到 (820,420)",
      ("+820" in root.geometry() and "+420" in root.geometry()),
      root.geometry())

try:
    root.destroy()
except Exception:
    pass
print("\n=== 结果：%d 项失败 ===" % len(FAILS))
sys.exit(1 if FAILS else 0)
