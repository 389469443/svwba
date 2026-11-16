#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动气泡窗口并截图（诊断用）。"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bubble

w = bubble.BubbleWindow()
w.show("抽到一张好牌啦，主人这波稳了～")
for _ in range(20):
    w.root.update()
    time.sleep(0.05)

from PIL import ImageGrab

x, y = w.root.winfo_x(), w.root.winfo_y()
time.sleep(0.5)
w.root.update()
ImageGrab.grab(bbox=(x, y, x + bubble.WIN_W, y + bubble.WIN_H)).save(
    os.path.join(HERE, "logs", "bubble.png"))
print("shot", x, y)
w.close()
