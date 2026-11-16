#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 从 BattleManagerBase 定位 BattlePlayer（只读，2026-10-21）

BattleManagerBase（dump.cs 当前版本）：
  +0x40 BattleEnemy
  +0x48 BattlePlayer   ← BattlePlayerBase，+0xA0..+0xD0 是 5~7 个 List
      +0xA0 HandCardList
      +0xA8 DeckCardList
      +0xB0 BattleStartDeckCardList
      +0xC8 CemeteryList
      +0xD0 BanishList

扫法：找 p 使 [p+0x40]、[p+0x48] 都是有效指针，且 X=[p+0x48] 的
+0xB0 指向的 List size 在 20~48（卡组），再验证 +0xA0..+0xD0 都是 List。

用法： python -u probe_bm.py [--max-scan 3G]
"""
import argparse
import io
import os
import struct
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "logs", "bm.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def list_size(h, RW, addr):
    raw = RW.read(h, addr + 0x18, 4)
    return i32(raw, 0) if raw else -1


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--max-scan", default="3G")
    args = ap.parse_args(argv)
    budget = args.max_scan.upper()
    mult = {"K": 1024, "M": 1 << 20, "G": 1 << 30}.get(budget[-1:], 1)
    budget = int(budget[:-1]) * mult if budget[-1] in "KMG" else int(budget)

    fh = io.open(OUT, "a", encoding="utf-8")

    def emit(s=""):
        print(s, flush=True)
        fh.write(s + "\n")
        fh.flush()

    import shadow_warchess_reader as RW
    pid, _ = RW.find_game_pid()
    emit("=" * 64)
    emit("BattleManager 定位 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)

    total = 0
    cands = []
    t0 = time.time()
    for base, size in RW.regions(h, private_only=True):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x80:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 9:
                    v = (arr > MIN_PTR) & (arr < MAX_PTR)
                    # i 对应 p；p+0x40=i+8, p+0x48=i+9
                    mask = v[8:-1] & v[9:]
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            cands.append((p, int(arr[i + 9])))
            off += n
    emit("候选 (p, X) %d，扫描 %.0f MB，%.1f 秒"
         % (len(cands), total / 1048576.0, time.time() - t0))

    hits = []
    for p, x in cands[:500000]:
        if not valid(x):
            continue
        lst = u64(RW.read(h, x + 0xB0, 8) or b"", 0)
        if not valid(lst):
            continue
        n = list_size(h, RW, lst)
        if not (20 <= n <= 48):
            continue
        sizes = []
        ok = True
        for k in (0xA0, 0xA8, 0xB0, 0xC8, 0xD0):
            v = u64(RW.read(h, x + k, 8) or b"", 0)
            if not valid(v):
                ok = False
                break
            sizes.append((hex(k), list_size(h, RW, v)))
        if ok:
            hits.append((p, x, n, sizes))
            emit("★ BattleManager=%#x BattlePlayer=%#x 卡组size=%d %s"
                 % (p, x, n, sizes))
            if len(hits) >= 5:
                break
    if not hits:
        emit("（没找到）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
