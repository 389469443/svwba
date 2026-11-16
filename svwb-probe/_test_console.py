# -*- coding: utf-8 -*-
"""独立测试控制台：托盘 / ✕ 行为 / — 最小化到任务栏 / 恢复。
不启动子进程、不清真实 history（全部 monkeypatch）。"""
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
import app as A  # noqa: E402

tmp = tempfile.mkdtemp(prefix="app_test_")
A.CONSOLE_POS = os.path.join(tmp, "console-pos.json")
with open(A.CONSOLE_POS, "w", encoding="utf-8") as fh:
    json.dump({"x": 980, "y": 220}, fh)
A.CONSOLE_SET = os.path.join(tmp, "console-settings.json")
with open(A.CONSOLE_SET, "w", encoding="utf-8") as fh:
    json.dump({"closeAction": "minimize"}, fh)
A.App.start_all = lambda self: None
A.App._kill_orphans = lambda self: None
A.App._clear_history = lambda self: None
A.cleanup.sweep = lambda: (0, 0)


def pump(root, sec):
    t0 = time.time()
    while time.time() - t0 < sec:
        try:
            root.update()
        except tk.TclError:
            return
        time.sleep(0.03)


FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


root = tk.Tk()
app = A.App(root)
pump(root, 0.5)
print("tray_ok:", app.tray_ok, " close_action:", app.close_action)
check("托盘图标创建成功", app.tray_ok)
check("closeAction 默认读取正确", app.close_action == "minimize", app.close_action)

# 0) 控制台不透明度实时预览
app._preview_console_alpha(0.55)
check("控制台不透明度实时生效（0.55）",
      abs(float(root.attributes("-alpha")) - 0.55) < 0.01,
      root.attributes("-alpha"))
app._preview_console_alpha(1.0)
check("控制台不透明度恢复（1.0）",
      abs(float(root.attributes("-alpha")) - 1.0) < 0.01,
      root.attributes("-alpha"))

# 1) ✕ = 隐藏到托盘
app.on_close()
pump(root, 0.3)
check("✕(minimize) → 窗口隐藏", not root.winfo_viewable())
check("✕(minimize) → 进程/主循环还在", root.winfo_exists() == 1)

# 托盘恢复（等价于双击）
app.restore_from_tray()
pump(root, 0.6)
check("从托盘恢复 → 窗口可见", bool(root.winfo_viewable()))
check("恢复后仍无边框", bool(root.overrideredirect()))

# 2) 最小化到任务栏
hwnd = app._hwnd()
ImageGrab.grab(bbox=(0, 1540, 2560, 1600), all_screens=True).save(
    os.path.join(HERE, "logs", "_min_before_console.png"))
app._minimize_to_taskbar()
pump(root, 1.5)
iconic = bool(ctypes.windll.user32.IsIconic(hwnd))
print("IsIconic:", iconic)
check("— 最小化后窗口真的 iconic", iconic)
ImageGrab.grab(bbox=(0, 1540, 2560, 1600), all_screens=True).save(
    os.path.join(HERE, "logs", "验证_控制台_任务栏图标.png"))

# 模拟“点任务栏按钮还原”（Windows 对任务栏点击执行的正是 SW_RESTORE）
hwnd2 = app._hwnd()      # 最小化过程中句柄可能变化，重新取
ctypes.windll.user32.ShowWindow(hwnd2, A.SW_RESTORE)
pump(root, 2.0)
check("还原后不再 iconic", not bool(ctypes.windll.user32.IsIconic(app._hwnd())))
check("还原后重新变回无边框", bool(root.overrideredirect()))
check("还原后位置正确", root.geometry().startswith("+980+220")
      or ("+980" in root.geometry() and "+220" in root.geometry()),
      root.geometry())

# 3) ✕ = 退出程序
app.close_action = "exit"
app.on_close()
time.sleep(0.4)
try:
    gone = not root.winfo_exists()
except tk.TclError:
    gone = True
check("✕(exit) → 窗口销毁", gone)

print("\n=== 结果：%d 项失败 ===" % len(FAILS))
for f in FAILS:
    print(" -", f)
sys.exit(1 if FAILS else 0)
