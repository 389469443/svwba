#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动记牌器窗口，4 秒后截图并关闭（诊断用）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deck_tracker

w = deck_tracker.open()
w.root.geometry("+80+80")
w.root.attributes("-topmost", True)


def shot():
    try:
        w.root.update()
        from PIL import ImageGrab
        ImageGrab.grab(bbox=(60, 60, 520, 720)).save(
            os.path.join(HERE, "logs", "shot_now.png"))
        print("shot saved")
    finally:
        w.close()


w.root.after(4000, shot)
w.root.mainloop()
