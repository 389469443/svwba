#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从截图上读「我方主战者血量」。

用途：**判别哪个 BattlePlayerBase 实例是我方**。
两个实例在内存里长得一样（都有 uid/hero/grade/life），
唯一可靠的区分办法是把它们的 Life 和画面上我方血量对一下。

位置：我方英雄头像右下角的红色盾牌（固定 UI 元素）
     归一化框 ≈ (0.56~0.68, 0.78~0.88)

复用已有资源：
  · 数字分类器 lib/screenshot/digit-samples.json（0~9 全有）
  · 分割方式与 read_stats 一致：V=max(R,G,B) + 高分位阈值

用法：
  python read_own_hp.py <截图>
  python read_own_hp.py --latest      # 抓当前画面再读
"""
import glob
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(PKG, "dsh-shadowverse-huanjing", "tools"))

Image.MAX_IMAGE_PIXELS = None

# 我方血量盾牌（归一化坐标，实测于 2560x1600）
BOX = (0.576, 0.772, 0.640, 0.872)   # 只框住血量盾牌本身（原先 0.56~0.68 太宽，会吃进背景亮区）
NORM_W, NORM_H = 24, 32


def _classifier():
    import digit_classifier as DC
    return DC.get_classifier()


def read_hp(img, box=BOX, debug=None):
    """返回 (血量, 置信概率) 或 (None, 0.0)"""
    W, H = img.size
    x0, y0, x1, y1 = (int(box[0] * W), int(box[1] * H),
                      int(box[2] * W), int(box[3] * H))
    crop = img.crop((x0, y0, x1, y1))
    a = np.asarray(crop, dtype=np.uint8)
    if a.size == 0:
        return None, 0.0

    import cv2
    import cv2
    up = cv2.resize(a, (a.shape[1] * 3, a.shape[0] * 3), interpolation=cv2.INTER_CUBIC)
    R_, G_, B_ = up[:, :, 0].astype(np.int32), up[:, :, 1].astype(np.int32), up[:, :, 2].astype(np.int32)
    V_ = up.max(axis=2).astype(np.int32)
    S_ = (up.max(axis=2).astype(np.int32) - up.min(axis=2).astype(np.int32))

    # 第一步：找**红色盾牌**本体（红远大于绿蓝）
    red = (((R_ > 100) & (R_ > G_ + 35) & (R_ > B_ + 35)).astype(np.uint8)) * 255
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    nred, lred, sred, _ = cv2.connectedComponentsWithStats(red, 8)
    if nred <= 1:
        return None, 0.0
    # 取最大的红色块 = 盾牌
    idx = 1 + int(np.argmax(sred[1:, 4]))
    sx, sy, sw, sh, _area = sred[idx]
    if sw < 20 or sh < 20:
        return None, 0.0

    # 第二步：**只在盾牌内部**找白色数字
    #（盾牌外面有一圈白色描边，直接全局找白会把边框当成数字——实测读成 3 或 5）
    pad_x, pad_y = int(sw * 0.12), int(sh * 0.12)
    ix0, iy0 = sx + pad_x, sy + pad_y
    ix1, iy1 = sx + sw - pad_x, sy + sh - pad_y
    if ix1 <= ix0 or iy1 <= iy0:
        return None, 0.0
    inner = np.zeros_like(red)
    inner[iy0:iy1, ix0:ix1] = 255
    white = (((V_ > 140) & (S_ < 80)).astype(np.uint8)) * 255
    mask = cv2.bitwise_and(white, inner)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    n, labels, stats, _c = cv2.connectedComponentsWithStats(mask, 8)
    Hm = mask.shape[0]
    blobs = []
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < 400 or bh < Hm * 0.18 or bh > Hm * 0.95:
            continue
        ar = bw / max(1, bh)
        if ar < 0.10 or ar > 1.10:
            continue
        blobs.append((x, y, bw, bh, area, i))

    if debug:
        from PIL import ImageDraw
        dbg = Image.fromarray(mask).convert("RGB")
        d = ImageDraw.Draw(dbg)
        for x, y, bw, bh, area, i in blobs:
            d.rectangle([x, y, x + bw, y + bh], outline=(0, 255, 0), width=2)
        dbg.save(debug)

    if not blobs:
        return None, 0.0

    # ⚠️ 只取**最大**的连通块：血量盾牌里只有数字是白色的，
    #    但框里难免混入高光碎片，全收会把结果拼成 "511011" 这种垃圾。
    # 血量可能是**两位数**（实测我方 24）。只在红盾**内部**找白字已经排除了
    # 白色描边，所以这里可以按 x 全收；早先出现 "511011" 垃圾是因为没做内部裁剪。
    blobs.sort(key=lambda t: t[0])
    clf = _classifier()
    import cv2 as _cv
    digits, probs = [], []

    def _one(sub_mask, hh):
        sub = _cv.resize(sub_mask.astype(np.float32) * 255.0, (NORM_W, NORM_H),
                         interpolation=_cv.INTER_AREA)
        return clf.classify((sub / 255.0).astype(np.float32), raw_h=hh)

    for x, y, bw, bh, area, i in blobs:
        m = (labels[y:y + bh, x:x + bw] == i)
        # ★ 两位数的两个数字常常**连成一块**（实测「24」被整块读成「3」）。
        #   按宽高比判断有几个字：单字宽 ≈ 字高×0.6 → k = round(w/h/0.6)
        k = max(1, min(3, int(round((bw / max(1, bh)) / 0.60))))
        if k == 1:
            lab, prob, _r = _one(m, bh / 3)
            if lab is not None:
                digits.append(str(lab))
                probs.append(prob)
            continue
        step = bw / k
        for j in range(k):
            a0 = int(j * step)
            a1 = bw if j == k - 1 else int((j + 1) * step)
            piece = m[:, a0:a1]
            if piece.sum() < 40:
                continue
            lab, prob, _r = _one(piece, bh / 3)
            if lab is not None:
                digits.append(str(lab))
                probs.append(prob)

    if not digits:
        return None, 0.0
    try:
        val = int("".join(digits))
    except ValueError:
        return None, 0.0
    return val, float(np.mean(probs))


def read_hp_anywhere(img, debug=None):
    """
    全屏搜索「红盾 + 白字」的血量盾牌。

    为什么需要：运营阶段和战斗阶段的 UI 布局不同，
    固定框（BOX）在战斗阶段会落空（实测 OCR 返回 None）。
    所以改成：先在全屏找所有"红色盾牌状"连通块，
    再对候选逐个 OCR，最后优先选**靠下、靠右**的那个（我方英雄在右下）。

    返回 (血量, 置信度, 盾牌外框或None)
    """
    import cv2
    W, H = img.size
    a = np.asarray(img, dtype=np.uint8)
    R_ = a[:, :, 0].astype(np.int32)
    G_ = a[:, :, 1].astype(np.int32)
    B_ = a[:, :, 2].astype(np.int32)
    V_ = a.max(axis=2).astype(np.int32)
    S_ = (a.max(axis=2).astype(np.int32) - a.min(axis=2).astype(np.int32))

    # 红色盾牌：红远大于绿蓝，且面积够大
    red = (((R_ > 95) & (R_ > G_ + 35) & (R_ > B_ + 35)).astype(np.uint8)) * 255
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, lab, st, _c = cv2.connectedComponentsWithStats(red, 8)

    clf = _classifier()
    cands = []
    for i in range(1, n):
        x, y, w, h, area = st[i]
        if w < W * 0.012 or w > W * 0.10:
            continue
        if h < H * 0.03 or h > H * 0.14:
            continue
        ar = w / max(1, h)
        if ar < 0.55 or ar > 1.35:      # 盾牌接近方形略高
            continue
        # 盾牌内部找白字
        pad_x, pad_y = int(w * 0.14), int(h * 0.14)
        m = np.zeros_like(red)
        m[y + pad_y:y + h - pad_y, x + pad_x:x + w - pad_x] = 255
        white = (((V_ > 140) & (S_ < 80)).astype(np.uint8)) * 255
        wm = cv2.bitwise_and(white, m)
        nb, lb, sb, _ = cv2.connectedComponentsWithStats(wm, 8)
        if nb <= 1:
            continue
        # 最大的白色连通块 = 数字
        j = 1 + int(np.argmax(sb[1:, 4]))
        gx, gy, gw, gh, ga = sb[j]
        if ga < 120 or gh < h * 0.25:
            continue
        sub = cv2.resize((lb[gy:gy + gh, gx:gx + gw] == j).astype(np.float32) * 255.0,
                         (NORM_W, NORM_H), interpolation=cv2.INTER_AREA) / 255.0
        digit, prob, _r = clf.classify(sub, raw_h=gh)
        if digit is None:
            continue
        # 我方英雄在**右下方**：给右下更高的优先级
        cx, cy = (x + w / 2) / W, (y + h / 2) / H
        prefer = cx * 0.5 + cy * 1.5
        cands.append((prefer, int(digit), prob, (x, y, w, h)))
        if debug:
            from PIL import ImageDraw
            pass
    if not cands:
        return None, 0.0, None
    cands.sort(key=lambda t: -t[0])
    _p, val, prob, box = cands[0]
    return val, prob, box


def grab_screen():
    """
    抓游戏窗口画面。

    ⚠️ 关键：用 **PrintWindow** 而不是从屏幕 BitBlt。
    从屏幕抠图时，只要有窗口盖在游戏上（浏览器、悬浮层），抓到的就是那个窗口，
    于是阶段判定变成 other、随从识别 0 张 —— 实测就是这么踩的。
    """
    import ctypes
    import ctypes.wintypes as wt
    user32 = ctypes.windll.user32
    gdi = ctypes.windll.gdi32
    user32.SetProcessDPIAware()

    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, cls, 128)
        if cls.value == "UnityWndClass" and user32.IsWindowVisible(hwnd):
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 1000:
                found.append((hwnd, r.left, r.top,
                              r.right - r.left, r.bottom - r.top))
        return True
    user32.EnumWindows(cb, 0)
    if not found:
        return None
    hwnd, x, y, w, h = max(found, key=lambda t: t[3] * t[4])

    class BIH(ctypes.Structure):
        _fields_ = [('biSize', ctypes.c_ulong), ('biWidth', ctypes.c_long),
                    ('biHeight', ctypes.c_long), ('biPlanes', ctypes.c_ushort),
                    ('biBitCount', ctypes.c_ushort), ('biCompression', ctypes.c_ulong),
                    ('biSizeImage', ctypes.c_ulong), ('biXPelsPerMeter', ctypes.c_long),
                    ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', ctypes.c_ulong),
                    ('biClrImportant', ctypes.c_ulong)]

    def _to_image(mem):
        bi = BIH()
        bi.biSize = ctypes.sizeof(BIH)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
        return Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1).convert('RGB')

    hwnd_dc = user32.GetWindowDC(hwnd)
    mem = gdi.CreateCompatibleDC(hwnd_dc)
    bmp = gdi.CreateCompatibleBitmap(hwnd_dc, w, h)
    gdi.SelectObject(mem, bmp)

    img = None
    # ① PrintWindow：被遮挡也能拿到窗口自身内容
    try:
        PW_RENDERFULLCONTENT = 0x00000002
        ok = user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)
    except Exception:
        ok = 0
    if ok:
        cand = _to_image(mem)
        # 校验：全黑或几乎单色说明没抓到
        ex = cand.getextrema()
        if any(lo < hi - 8 for lo, hi in ex):
            img = cand

    # ② 退回屏幕 BitBlt（万一 PrintWindow 不支持）
    if img is None:
        try:
            scr = user32.GetDC(0)
            gdi.BitBlt(mem, 0, 0, w, h, scr, x, y, 0x00CC0020)
            user32.ReleaseDC(0, scr)
            cand = _to_image(mem)
            ex = cand.getextrema()
            if any(lo < hi - 8 for lo, hi in ex):
                img = cand
        except Exception:
            pass

    gdi.DeleteObject(bmp)
    gdi.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hwnd_dc)
    return img


def main():
    if "--latest" in sys.argv:
        img = grab_screen()
        if img is None:
            print("找不到游戏窗口")
            return 1
    else:
        if len(sys.argv) < 2:
            print(__doc__)
            return 1
        img = Image.open(sys.argv[1]).convert("RGB")
    dbg = os.path.join(HERE, "logs", "血量掩码.png")
    hp, prob = read_hp(img, debug=dbg)
    print(f"我方血量 = {hp}  (置信 {prob:.0%})")
    return 0 if hp is not None else 2


if __name__ == "__main__":
    sys.exit(main())
