#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顶栏「对方宠物（兔子）」模板匹配 —— 判定运营 / 开打。

用户给的判据：
  · 顶栏正中是**对方的宠物（兔子）** → 运营阶段 → **不算胜率**
  · 没有兔子（换成对方主战者）      → **开打，立即算胜率**

为什么必须用模板匹配（实测教训）：
  只统计"奶油白比例"时两类**重叠**（有兔子 0.001~0.49 都可能），
  因为战斗特效、红色激光、我方宠物上端伸入检测框都会污染颜色统计。
  改成模板匹配后分离极干净（实测：有兔子 ≥0.72，无兔子 ≤0.29）。

模板来源：从**游戏实帧**切出（lib/screenshot/pet-template/pet-live.png），
  保证模板尺寸/风格与实际画面一致；旧的标定图裁图因尺寸过大曾导致全部匹配失败。

用法：
  python tools/pet_detect.py            # 对全部已知帧打分（标定表）
  python tools/pet_detect.py <图>       # 单张
"""
import os
import sys

import cv2
import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
TPL = os.path.join(PKG, "lib", "screenshot", "pet-template", "pet-live.png")

SEARCH = (0.30, 0.0, 0.72, 0.12)     # 顶栏搜索区（含兔子全身，不含下方我方宠物）
THRESHOLD = 0.55                     # 实测：有兔子 ≥0.72，无兔子 ≤0.29
SCALES = (0.75, 0.9, 1.0, 1.1, 1.25, 1.45)


def score(img):
    """返回与兔子模板的最高匹配分（-1 表示模板比搜索区还大，无法匹配）"""
    lo = np.asarray(Image.open(TPL).convert("L"), dtype=np.float32)
    W, H = img.size
    x0, y0, x1, y1 = (int(W * SEARCH[0]), int(H * SEARCH[1]), int(W * SEARCH[2]), int(H * SEARCH[3]))
    band = np.asarray(img.crop((x0, y0, x1, y1)).convert("L"), dtype=np.float32)
    best = -1.0
    for s in SCALES:
        t = cv2.resize(lo, (max(8, int(lo.shape[1] * s)), max(8, int(lo.shape[0] * s))))
        if t.shape[0] > band.shape[0] or t.shape[1] > band.shape[1]:
            continue
        r = cv2.matchTemplate(band, t, cv2.TM_CCOEFF_NORMED)
        best = max(best, float(r.max()))
    return round(best, 4)


def has_pet(img):
    """有兔子 → True（运营）；无兔子 → False（开打，立即算胜率）"""
    return score(img) >= THRESHOLD


def main():
    import glob
    files = [sys.argv[1]] if len(sys.argv) > 1 else (
        sorted(glob.glob(os.path.join(PKG, "shots", "live", "*.png")))
        + [os.path.join(PKG, "shots", "C-seven-slots.webp"),
           os.path.join(PKG, "shots", "G-battle-1895.png"),
           os.path.join(PKG, "shots", "G2-battle-start.png")])
    rows = []
    for p in files:
        if not os.path.isfile(p):
            continue
        s = score(Image.open(p).convert("RGB"))
        rows.append((s, os.path.basename(p)))
    rows.sort()
    print(f"模板 {os.path.basename(TPL)}  阈值 {THRESHOLD}\n")
    for s, n in rows:
        v = "有兔子 → 运营（不算）" if s >= THRESHOLD else "没兔子 → 开打（立即算胜率）"
        print(f"  {s:<9} {n:<36} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
