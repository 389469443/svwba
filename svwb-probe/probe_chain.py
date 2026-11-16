#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 引用链定位（只读，2026-10-21）

1. 扫 max_length 35~45 的数组（卡组元素数组）；
2. 反查谁指向它（List._items / List+0x10）；
3. 再反查谁指向这个 List（BattlePlayerBase +0xB0 / 0xA0 / 0xA8 ...）；
4. 验证候选对象的 +0xA0..+0xD0 是 5 个 List，打印手牌/牌库/墓地数量。

用法： python -u probe_chain.py [--max-scan 3G]
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
OUT = os.path.join(HERE, "logs", "chain_probe.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF
PARAM_OFF = 0xA8
CARD_ID_OFF = 0x90


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def scan_arrays(h, RW, budget):
    hits = []
    total = 0
    for base, size in RW.regions(h, private_only=True):
        if total > budget:
            break
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            total += n
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 0x100:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 5:
                    mlen = (arr[3:-1] & 0xFFFFFFFF)
                    elems = arr[4:]
                    mask = ((mlen >= 35) & (mlen <= 45)
                            & (elems > MIN_PTR) & (elems < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            hits.append(p)
            off += n
    return hits, total


def find_refs(h, RW, target, limit=12):
    hits = []
    for base, size in RW.regions(h, private_only=True):
        off = 0
        while off < size:
            n = min(8 << 20, size - off)
            raw = RW.read(h, base + off, n)
            if raw and len(raw) >= 8:
                arr = np.frombuffer(raw, dtype=np.uint64)
                idx = np.nonzero(arr == target)[0]
                for i in idx:
                    hits.append(base + off + int(i) * 8)
                    if len(hits) >= limit:
                        return hits
            off += n
    return hits


def is_card_array(h, RW, arr, need=30):
    raw = RW.read(h, arr + 0x20, 45 * 8)
    if not raw:
        return 0
    ok = 0
    for i in range(min(40, len(raw) // 8)):
        e = u64(raw, i * 8)
        if not valid(e):
            continue
        praw = RW.read(h, e + PARAM_OFF, 8)
        if not praw:
            continue
        param = u64(praw, 0)
        if not valid(param):
            continue
        if RW.read(h, param + CARD_ID_OFF, 4):
            ok += 1
    return ok


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
    emit("引用链定位 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
    if not pid:
        return 1
    h = RW.open_proc(pid)

    arrays, total = scan_arrays(h, RW, budget)
    emit("数组候选 %d（扫描 %.0f MB）" % (len(arrays), total / 1048576.0))
    target = None
    for a in arrays[:3000]:
        if is_card_array(h, RW, a) >= 30:
            target = a
            break
    if not target:
        emit("没找到 40 元素的卡组数组")
        return 1
    emit("★ 卡组数组 = %#x（%d 元素）" % (target, is_card_array(h, RW, target)))

    refs = find_refs(h, RW, target, limit=12)
    emit("引用它的位置 %d 个：" % len(refs))
    for r in refs:
        # List._items 在 +0x10；引用点 r = list+0x10
        list_addr = r - 0x10
        sz = list_size(h, RW, list_addr)
        emit("  ref=%#x  -> 可能是 List %#x size=%s" % (r, list_addr, sz))
        if not (30 <= sz <= 60):
            continue
        refs2 = find_refs(h, RW, list_addr, limit=12)
        emit("    List 被引用 %d 次：" % len(refs2))
        for r2 in refs2:
            for off in (0xA0, 0xA8, 0xB0, 0xB8, 0xC0, 0xC8, 0xD0):
                player = r2 - off
                if player < MIN_PTR:
                    continue
                vals = []
                good = True
                for k in (0xA0, 0xA8, 0xB0, 0xC8, 0xD0):
                    raw = RW.read(h, player + k, 8)
                    v = u64(raw, 0) if raw else 0
                    if not valid(v):
                        good = False
                        break
                    vals.append((k, list_size(h, RW, v)))
                if good:
                    emit("    ★ BattlePlayer=%#x (ref-%#x) 列表 size: %s"
                         % (player, off, vals))
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
