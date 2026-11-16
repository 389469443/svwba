#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
临时截图清理 —— 超过 2 小时的截图自动删除。

为什么需要：
  调试和运行都会不断产生截图（战斗帧、核对快照、面板截图），
  一局下来能堆到几百 MB，之前就把 C 盘撑到只剩 1 GB。
  截图只是过程产物，留 2 小时足够回看，之后自动清掉。

保护范围：
  · 只删图片（.png/.jpg/.jpeg/.webp）
  · 不碰 json / txt / 日志
  · 不碰 hero-portraits（主战者立绘是资产，不是临时图）
  · 不碰 lib/ 下任何东西
"""
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
GAME = os.path.join(PKG, "dsh-shadowverse-huanjing")

MAX_AGE_HOURS = 2.0
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")

# 需要清扫的目录（相对包根）
TARGETS = [
    os.path.join(HERE, "logs"),
    os.path.join(HERE, "shots"),
    os.path.join(HERE, "_debug"),
    os.path.join(GAME, "shots", "live"),
    os.path.join(GAME, "shots"),
    os.path.join(GAME, "tools", "extracted"),
]

# 绝对不删的路径关键字
KEEP_MARKERS = ("hero-portraits", os.sep + "lib" + os.sep,
                "pet-template")


def sweep(max_age_hours=MAX_AGE_HOURS, verbose=False):
    """删除超期截图，返回 (删除数, 释放字节数)"""
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    freed = 0
    for root in TARGETS:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            low = dirpath.lower()
            if any(m.lower() in low for m in KEEP_MARKERS):
                continue
            for fn in filenames:
                if not fn.lower().endswith(IMG_EXT):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                    if st.st_mtime >= cutoff:
                        continue
                    freed += st.st_size
                    os.remove(fp)
                    removed += 1
                    if verbose:
                        print(f"  删除 {fp}")
                except Exception:
                    pass
    return removed, freed


if __name__ == "__main__":
    import sys
    hours = float(sys.argv[1]) if len(sys.argv) > 1 else MAX_AGE_HOURS
    n, b = sweep(hours, verbose=True)
    print(f"清理完成：删除 {n} 个截图，释放 {b/1048576:.1f} MB"
          f"（阈值 {hours} 小时）")
