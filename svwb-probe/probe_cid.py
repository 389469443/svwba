#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 用卡表反查内存里的 8 位卡 id（只读，2026-10-21）

卡表 gamedata/sv_card_names.json 的 key 是 8 位卡 id（10001110 等）。
扫描进程私有内存里的 int32，命中卡表 key 的位置记录下来，
并打印附近结构，帮助确认 CardParameter.CardId 的真实读取路径。
"""
import json
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
NAMES = os.path.join(HERE, "gamedata", "sv_card_names.json")


def main():
    names = json.load(open(NAMES, encoding="utf-8"))
    keys = np.array(sorted(int(k) for k in names), dtype=np.uint32)
    print("card ids:", len(keys), "range", keys.min(), "-", keys.max())

    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    if not pid:
        print("没有游戏进程")
        return 1
    h = RW.open_proc(pid)
    hits = []
    t0 = time.time()
    for base, size in RW.regions(h, private_only=True):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 4:
                arr = np.frombuffer(raw, dtype=np.uint32)
                mask = (arr >= 10000000) & (arr < 20000000)
                idx = np.nonzero(mask)[0]
                if len(idx):
                    vals = arr[idx]
                    good = np.isin(vals, keys)
                    for i in np.nonzero(good)[0]:
                        hits.append((base + off + int(idx[i]) * 4, int(vals[i])))
            off += n
    print("扫描 %.1f 秒，命中 %d 个 8 位卡 id" % (time.time() - t0, len(hits)))
    for addr, v in hits[:40]:
        near = RW.read(h, addr - 0x10, 0x30)
        print("  @%#x id=%d %s" % (addr, v, names.get(str(v), "")))
        print("     -0x10..+0x20:", near.hex() if near else "?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
