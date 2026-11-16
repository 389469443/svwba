# -*- coding: utf-8 -*-
"""独立验证：悬浮层设置/位置热更新 + 判定器 delay/sims 热读（PID 不变）。"""
import io
import json
import os
import sys
import tempfile
import time

import tkinter as tk

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "dsh-shadowverse-huanjing", "tools"))

FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


def write(p, d):
    tmp = p + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, p)


# ---- 1) 悬浮层设置/位置热读 ----
import overlay as OV  # noqa: E402

tmp = tempfile.mkdtemp(prefix="ov_hot_")
OV.SETTINGS = os.path.join(tmp, "s.json")
OV.POSFILE = os.path.join(tmp, "p.json")
OV.STATE = os.path.join(tmp, "feed.json")
write(OV.SETTINGS, {"alpha": 0.9, "text_alpha": 1.0, "scale": 1.0,
                    "titleColor": "#c9a86a", "locked": True})
write(OV.POSFILE, {"x": 300, "y": 200, "scale": 1.0, "locked": True})
write(OV.STATE, {"phase": "other", "win": None})
ov = OV.Overlay(demo=False)
ov.root.withdraw()
try:
    ov._poll_hot()
    write(OV.SETTINGS, {"alpha": 0.5, "text_alpha": 0.8, "scale": 1.3,
                        "titleColor": "#ffb6c1", "locked": False})
    ov._poll_hot()
    check("悬浮层 alpha 热更新", abs(float(ov.root.attributes("-alpha")) - 0.5) < 0.01,
          ov.root.attributes("-alpha"))
    check("悬浮层 scale 热更新", abs(ov.scale - 1.3) < 0.01, ov.scale)
    check("悬浮层 文字透明度热更新", abs(ov.text_alpha - 0.8) < 0.01, ov.text_alpha)
    check("悬浮层 locked 热更新", ov.locked is False)
    write(OV.POSFILE, {"x": 640, "y": 480, "scale": 1.3, "locked": False})
    ov._poll_hot()
    check("悬浮层位置热更新", tuple(ov.pos) == (640, 480), ov.pos)
finally:
    try:
        ov.root.destroy()
    except Exception:
        pass

# ---- 2) 判定器 delay/sims 热读（同一 PID）----
import watch_mem as WM  # noqa: E402

js = os.path.join(tmp, "judge.json")
WM.JUDGE_SET = js
write(js, {"delay": 2.0, "sims": 5000})
d1, s1 = WM.read_hot_settings(0.2, 20000)
check("判定器 delay 热读为 2.0", abs(d1 - 2.0) < 1e-6, d1)
check("判定器 sims 热读为 5000", s1 == 5000, s1)
pid1 = os.getpid()
write(js, {"delay": 0.3, "sims": 3000})
d2, s2 = WM.read_hot_settings(d1, s1)
check("判定器 delay 再次热读为 0.3", abs(d2 - 0.3) < 1e-6, d2)
check("判定器 sims 再次热读为 3000", s2 == 3000, s2)
check("热读前后 PID 不变", os.getpid() == pid1, os.getpid())

# 非法值限幅
write(js, {"delay": 99, "sims": -5})
d3, s3 = WM.read_hot_settings(d2, s2)
check("delay 限幅到 3.0", abs(d3 - 3.0) < 1e-6, d3)
check("sims 限幅到 500", s3 == 500, s3)

print("\n=== 结果：%d 项失败 ===" % len(FAILS))
sys.exit(1 if FAILS else 0)
