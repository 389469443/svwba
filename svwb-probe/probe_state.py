#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
影之诗记牌器 · 纯内存扫描 BattleStatePlayerMpo（只读，2026-10-21）

字段（Frida 实测）：
  HandCards           +16   (BattleCardMpo[] 数组指针)
  DeckCount           +32   (int32)
  Life                +36   (int32)
  MaxLife             +40   (int32)
  CemeteryCount       +88   (int32)
  PlayedCardIds       +112  (int[] 数组指针)
  DestroyedCardIds    +120  (int[] 数组指针)

特征：DeckCount 0~40、Life 1~20、HandCards/PlayedCardIds 是有效数组指针。
不注入、不 hook、不碰 GC，只 ReadProcessMemory。
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
OUT = os.path.join(HERE, "logs", "state_probe.txt")

MIN_PTR = 0x10000
MAX_PTR = 0x7FFFFFFFFFFF


def u64(raw, off):
    return struct.unpack_from("<Q", raw, off)[0] if len(raw) >= off + 8 else 0


def i32(raw, off):
    return struct.unpack_from("<i", raw, off)[0] if len(raw) >= off + 4 else 0


def valid(p):
    return MIN_PTR < p < MAX_PTR


def arr_len(h, RW, arr):
    if not valid(arr):
        return -1
    raw = RW.read(h, arr + 0x18, 4)
    return i32(raw, 0) if raw else -1


def arr_ptrs(h, RW, arr, k=8):
    n = arr_len(h, RW, arr)
    if not (0 < n <= 60):
        return []
    raw = RW.read(h, arr + 0x20, min(n, k) * 8)
    if not raw:
        return []
    out = []
    for i in range(min(n, k, len(raw) // 8)):
        p = u64(raw, i * 8)
        out.append(p if valid(p) else 0)
    return out


def arr_ints(h, RW, arr, k=12):
    n = arr_len(h, RW, arr)
    if not (0 < n <= 60):
        return []
    raw = RW.read(h, arr + 0x20, min(n, k) * 8)
    if not raw:
        return []
    # 元素步长 8（实测卡 id 在低 32 位）
    out = []
    for i in range(min(n, k, len(raw) // 8)):
        out.append(i32(raw, i * 8))
    return out


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
    emit("状态对象扫描 %s pid=%s" % (time.strftime("%H:%M:%S"), pid))
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
            if raw and len(raw) >= 0x90:
                arr = np.frombuffer(raw, dtype=np.uint64)
                if len(arr) > 16:
                    deck = arr[4:-12] & 0xFFFFFFFF
                    life = arr[4:-12] >> 32
                    hand = arr[2:-14]
                    played = arr[14:-2]
                    mask = ((deck >= 0) & (deck <= 40)
                            & (life >= 1) & (life <= 20)
                            & (hand > MIN_PTR) & (hand < MAX_PTR)
                            & (played > MIN_PTR) & (played < MAX_PTR))
                    for i in np.nonzero(mask)[0]:
                        p = base + off + int(i) * 8
                        if p > MIN_PTR:
                            cands.append(p)
            off += n
    emit("候选 %d，扫描 %.1f 秒 / %.0f MB"
         % (len(cands), time.time() - t0, total / 1048576.0))

    good = []
    for p in cands[:50000]:
        hand = u64(RW.read(h, p + 16, 8) or b"", 0)
        played = u64(RW.read(h, p + 112, 8) or b"", 0)
        destroyed = u64(RW.read(h, p + 120, 8) or b"", 0)
        deck = i32(RW.read(h, p + 32, 4) or b"", 0)
        life = i32(RW.read(h, p + 36, 4) or b"", 0)
        hn = arr_len(h, RW, hand)
        pn = arr_len(h, RW, played)
        dn = arr_len(h, RW, destroyed)
        if not (0 <= hn <= 12 and 0 <= pn <= 40 and 0 <= dn <= 40):
            continue
        good.append(p)
        emit("★ p=%#x Deck=%d Life=%d Hand=%d Played=%d Destroyed=%d"
             % (p, deck, life, hn, pn, dn))
        emit("   hand ptrs=%s" % [hex(x) for x in arr_ptrs(h, RW, hand, 4)])
        emit("   played ints=%s" % arr_ints(h, RW, played, 10))
        emit("   destroyed ints=%s" % arr_ints(h, RW, destroyed, 10))
        if len(good) >= 6:
            break
    if not good:
        emit("（没找到 —— 需要在对局中）")
    fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
