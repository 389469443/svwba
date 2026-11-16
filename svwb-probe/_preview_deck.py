#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""记牌器界面预览（假数据 + 真实卡表），三个视图各截一张，跑完自动关。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deck_reader
import deck_tracker

names = deck_reader.load_names()
ids = [int(k) for k in list(names.keys())[:40]]


def fake_snapshot():
    rows = []
    for i, cid in enumerate(ids):
        total = 2 if i < 4 else 1
        remaining = 0 if i < 6 else total
        rows.append((cid, total, remaining, 1 if i < 3 else 0))
    return {"in_battle": True, "total": sum(r[1] for r in rows),
            "remaining": sum(r[2] for r in rows),
            "hand": 3, "rows": rows}


deck_reader.snapshot = fake_snapshot

w = deck_tracker.open()
w.root.geometry("+2200+900")
w.root.update()
time.sleep(0.6)

from PIL import ImageGrab
for key in ("remaining", "gone", "all"):
    w.set_view(key)
    w.root.lift()
    w.root.attributes("-topmost", True)
    w.root.update()
    time.sleep(0.35)
    w.root.update()
    out = os.path.join(HERE, "logs", "预览_%s.png" % key)
    ImageGrab.grab(bbox=(2180, 880, 2560, 1400)).save(out)
    print("saved", out)

try:
    w.close()
except Exception:
    pass
