#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动透明宠物窗并显示一条气泡，3 秒后截图关闭（诊断用）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import pet_view

v = pet_view.PetView()
v.show("抽到一张好牌啦，主人这波稳了～")


def shot():
    try:
        v.root.update()
        from PIL import ImageGrab
        x, y = v.root.winfo_x(), v.root.winfo_y()
        ImageGrab.grab(bbox=(x - 10, y - 10,
                             x + v.total_w + 20, y + v.h + 20)).save(
            os.path.join(HERE, "logs", "pet_bubble.png"))
        print("shot")
    finally:
        v.close()


v.root.after(3000, shot)
v.run()
